from __future__ import annotations

import asyncio
import datetime as dt
import queue
import re
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from PIL import Image

from app.cache import CacheManager
from app.config import CONFIG_DIR, ConfigStore
from app.guide import channel_specs, generate_xmltv
from app.history import HistoryStore
from app.places import PlaceManager
from app.radar import RadarManager
from app.renderer import BUILTIN_RWN_LOGO, WeatherRenderer
from app.spc import SPCManager
from app.streamer import LIVE_DIR, PREVIEW_PATH, Streamer, encoder_capabilities
from app.tts import TTSManager
from app.weather import WeatherManager, resolve_zip
from app.operations import BUILTIN_PROFILES, create_backup_bytes, restore_backup_bytes, create_diagnostics_bytes, system_status
from app.observability import observability
from app.notifications import NotificationManager
from app.security import SlidingWindowLimiter, authentication_enabled, client_address, valid_basic_authorization

BASE=Path(__file__).resolve().parent
config_store=ConfigStore(); history_store=HistoryStore(); cache_manager=CacheManager(config_store)
weather_manager=WeatherManager(config_store,history_store); place_manager=PlaceManager(config_store); radar_manager=RadarManager(config_store); spc_manager=SPCManager(config_store)
renderer=WeatherRenderer(config_store,weather_manager,radar_manager,place_manager,history_store,spc_manager); tts_manager=TTSManager(config_store); streamer=Streamer(config_store,renderer,tts_manager)
notification_manager=NotificationManager(config_store)
_service_ready = False
_rate_limiter = SlidingWindowLimiter()
_status_lock = threading.Lock()
_status_cached_at = 0.0
_status_cached: dict | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service_ready
    notification_manager.start(); weather_manager.start(); place_manager.start(); radar_manager.start(); spc_manager.start(); cache_manager.start(); streamer.start()
    _service_ready = True
    observability.event("lifecycle", "WeatherStream service started", version="0.2.5.1")
    try:
        yield
    finally:
        _service_ready = False
        observability.event("lifecycle", "WeatherStream service stopping")
        notification_manager.stop(); streamer.stop(); tts_manager.stop(); cache_manager.stop(); spc_manager.stop(); radar_manager.stop(); place_manager.stop(); weather_manager.stop()

app=FastAPI(title="WeatherStream / Roller Weather Network",version="0.2.5.1",lifespan=lifespan)
templates=Jinja2Templates(directory=str(BASE/"templates")); app.mount("/static",StaticFiles(directory=str(BASE/"static")),name="static")
LIVE_DIR.mkdir(parents=True,exist_ok=True)

_PUBLIC_API_GETS = {"/api/status", "/api/channels", "/api/preview"}
_EXPENSIVE_LIMITS = {
    ("POST", "/api/tts/download"): (2, 300),
    ("POST", "/api/tts/test"): (10, 60),
    ("POST", "/api/backup/restore"): (2, 600),
    ("POST", "/api/stream/restart"): (10, 60),
    ("POST", "/api/history/vacuum"): (2, 600),
    ("POST", "/api/notifications/test"): (3, 300),
}


def _admin_protected(path: str, method: str) -> bool:
    if path == "/admin" or path.startswith("/admin/") or path == "/setup":
        return True
    if not path.startswith("/api/"):
        return False
    if method == "GET" and (path in _PUBLIC_API_GETS or path.startswith("/api/preview/")):
        return False
    return True


@app.middleware("http")
async def security_and_metrics(request: Request, call_next):
    started = time.perf_counter()
    path = request.url.path
    method = request.method.upper()
    if _admin_protected(path, method) and not valid_basic_authorization(request.headers.get("authorization")):
        observability.increment("authentication_failures_total")
        response = JSONResponse({"detail": "Administrator authentication required."}, status_code=401) if path.startswith("/api/") else PlainTextResponse("Administrator authentication required.", status_code=401)
        response.headers["WWW-Authenticate"] = 'Basic realm="WeatherStream Admin", charset="UTF-8"'
        observability.count_request(method, path, 401, time.perf_counter() - started)
        return response
    policy = _EXPENSIVE_LIMITS.get((method, path))
    if policy:
        address = client_address(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))
        result = _rate_limiter.check(f"{address}:{method}:{path}", *policy)
        if not result.allowed:
            observability.increment("rate_limit_rejections_total")
            response = JSONResponse({"detail": "Too many requests for this operation."}, status_code=429, headers={"Retry-After": str(result.retry_after)})
            observability.count_request(method, path, 429, time.perf_counter() - started)
            return response
    try:
        response = await call_next(request)
    except Exception:
        route = getattr(request.scope.get("route"), "path", path)
        observability.count_request(method, route, 500, time.perf_counter() - started)
        raise
    route = getattr(request.scope.get("route"), "path", path)
    observability.count_request(method, route, response.status_code, time.perf_counter() - started)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
    return response

class ZipRequest(BaseModel): postal_code:str
class SettingsRequest(BaseModel):
    station_name:str|None=None; station_callsign:str|None=None; station_slogan:str|None=None; service_area:str|None=None; public_base_url:str|None=None; theme:str|None=None
    weather_refresh_seconds:int|None=None; alert_refresh_seconds:int|None=None; nws_user_agent:str|None=None
    music:dict|None=None; radar:dict|None=None; alerts:dict|None=None; presentation:dict|None=None; slides:dict|None=None; branding:dict|None=None; maps:dict|None=None
    storm_guidance:dict|None=None; spc:dict|None=None; history:dict|None=None; smart_programming:dict|None=None; dayparts:dict|None=None; cache:dict|None=None; channels:dict|None=None; video:dict|None=None; performance:dict|None=None; custom_profiles:dict|None=None; tts:dict|None=None; notifications:dict|None=None
class TtsTestRequest(BaseModel):
    text:str|None=None; voice:str|None=None; speed:float|None=None; volume:float|None=None
class TtsVoiceRequest(BaseModel):
    voice:str|None=None
class SetupRequest(BaseModel):
    postal_code:str
    station_name:str|None=None
    station_callsign:str|None=None
    service_area:str|None=None
    theme:str|None=None
    streaming_mode:str|None=None


@app.get("/",response_class=HTMLResponse)
def root(request:Request):
    if not config_store.get().get("locations"): return RedirectResponse("/setup",status_code=307)
    return templates.TemplateResponse("index.html",{"request":request})
@app.get("/setup",response_class=HTMLResponse)
def setup_page(request:Request): return templates.TemplateResponse("setup.html",{"request":request})
@app.get("/admin",response_class=HTMLResponse)
def admin(request:Request): return templates.TemplateResponse("admin.html",{"request":request})
@app.get("/admin/settings",response_class=HTMLResponse)
def admin_settings(request:Request): return templates.TemplateResponse("settings.html",{"request":request})
@app.get("/admin/channels",response_class=HTMLResponse)
def admin_channels(request:Request): return templates.TemplateResponse("channels.html",{"request":request})
@app.get("/api/settings")
def api_settings(): return config_store.get()

@app.get("/api/setup/status")
def api_setup_status():
    settings=config_store.get(); locations=settings.get("locations") or []
    return {"needs_setup":not bool(locations),"configured_locations":len(locations),"station_name":settings.get("station_name"),"version":"0.2.5.1"}

@app.post("/api/setup/complete")
def api_setup_complete(payload:SetupRequest):
    postal=payload.postal_code.strip()
    if not re.fullmatch(r"\d{5}",postal): raise HTTPException(status_code=400,detail="Enter a five-digit U.S. ZIP code.")
    try: resolved=resolve_zip(postal)
    except ValueError as exc: raise HTTPException(status_code=404,detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=502,detail=f"Unable to resolve ZIP code: {exc}") from exc
    location=config_store.add_location(resolved)
    config_store.update_general({
        "station_name":(payload.station_name or "Roller Weather Network").strip()[:40],
        "station_callsign":(payload.station_callsign or "RWN").strip()[:12],
        "service_area":(payload.service_area or resolved.get("name") or postal).strip()[:64],
        "theme":payload.theme or "local-90s",
        "channels":{"streaming_mode":payload.streaming_mode or "on_demand"},
    })
    weather_manager.request_refresh(); place_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); streamer.request_reconfigure()
    observability.event("settings","First-run setup completed",postal_code=postal,location_id=location.get("id"))
    return {"ok":True,"location":location,"redirect":"/"}

@app.post("/api/settings")
def api_update_settings(payload:SettingsRequest):
    changes=payload.model_dump(exclude_none=True)
    updated=config_store.update_general(changes)
    if set(changes) & {"weather_refresh_seconds", "alert_refresh_seconds", "nws_user_agent", "smart_programming", "dayparts"}: weather_manager.request_refresh()
    if set(changes) & {"maps"}: place_manager.request_refresh()
    if set(changes) & {"radar", "maps"}: radar_manager.request_refresh()
    if set(changes) & {"spc"}: spc_manager.request_refresh()
    if "channels" in changes: streamer.request_reconfigure()
    # Encoder/audio/performance changes need a worker restart. Presentation, station,
    # slide timing and theme settings hot-reload from ConfigStore in the renderer.
    if set(changes) & {"video", "music", "performance", "tts"}: streamer.request_restart(reason="settings change")
    observability.event("settings", "Settings updated", sections=sorted(changes))
    return updated

@app.post("/api/locations")
def api_add_location(payload:ZipRequest):
    postal=payload.postal_code.strip()
    if not re.fullmatch(r"\d{5}",postal): raise HTTPException(status_code=400,detail="WeatherStream accepts 5-digit U.S. ZIP codes.")
    try: resolved=resolve_zip(postal)
    except ValueError as exc: raise HTTPException(status_code=404,detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=502,detail=f"Unable to resolve ZIP code: {exc}") from exc
    location=config_store.add_location(resolved); weather_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); streamer.request_reconfigure(); return location

@app.delete("/api/locations/{location_id}")
def api_remove_location(location_id:str):
    if not config_store.remove_location(location_id): raise HTTPException(status_code=404,detail="Location not found.")
    weather_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); streamer.request_reconfigure(); return {"ok":True}

@app.post("/api/locations/{location_id}/primary")
def api_set_primary(location_id:str):
    if not config_store.set_primary(location_id): raise HTTPException(status_code=404,detail="Location not found.")
    weather_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); streamer.request_reconfigure(); return {"ok":True}

@app.post("/api/refresh")
def api_refresh(): weather_manager.request_refresh(); place_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); observability.event("refresh", "Manual data refresh requested"); return {"ok":True}
@app.post("/api/stream/restart")
def api_stream_restart(): streamer.request_restart(reason="manual all-channel restart"); observability.event("stream", "All active channels restart requested"); return {"ok":True}
@app.post("/api/cache/clean")
def api_cache_clean(): return cache_manager.clean()

def _source_stamp(value):
    if isinstance(value,(int,float)):
        try:return dt.datetime.fromtimestamp(value,tz=dt.timezone.utc).isoformat()
        except Exception:return None
    return value

def _channel_payload(request:Request|None=None):
    settings=config_store.get(); base=(settings.get("public_base_url") or (str(request.base_url).rstrip("/") if request else "")).rstrip("/"); status=streamer.status().get("channels") or {}; rows=[]
    for spec in channel_specs(settings):
        st=status.get(spec["key"]) or {}; loc=spec.get("location") or {}
        rows.append({"id":spec["id"],"key":spec["key"],"name":spec["name"],"mode":spec["mode"],"postal_code":loc.get("postal_code"),"location_id":loc.get("id"),"url":f"{base}/live/{spec['key']}/index.m3u8" if base else f"/live/{spec['key']}/index.m3u8",**st})
    return rows

@app.get("/api/channels")
def api_channels(request:Request): return {"channels":_channel_payload(request),"m3u":"/playlist.m3u","xmltv":"/guide.xml"}

@app.get("/api/channel-catalog")
def api_channel_catalog():
    settings=config_store.get(); cfg=settings.get("channels") or {}; locations=list(settings.get("locations") or []); primary_id=settings.get("primary_location_id")
    raw=[]
    for loc in locations[:max(1,min(24,int(cfg.get("max_zip_channels",12))))]:
        postal=str(loc.get("postal_code") or loc.get("id") or "local")
        raw.append({"key":f"zip-{postal}","name":f"RWN Local - {loc.get('name') or postal}","mode":"local","master_enabled":bool(cfg.get("per_zip_enabled",True))})
    if primary_id:
        raw.append({"key":"radar","name":"RWN Radar","mode":"radar","master_enabled":bool(cfg.get("radar_enabled",True))})
        raw.append({"key":"severe","name":"RWN Severe Weather","mode":"severe","master_enabled":bool(cfg.get("severe_enabled",True))})
    lineup=cfg.get("lineup") if isinstance(cfg.get("lineup"),list) else []; meta={x.get("key"):x for x in lineup if isinstance(x,dict) and x.get("key")}; overrides=cfg.get("overrides") if isinstance(cfg.get("overrides"),dict) else {}; status=(streamer.status().get("channels") or {})
    out=[]
    for idx,item in enumerate(raw):
        row=meta.get(item["key"],{}); enabled=bool(item["master_enabled"] and row.get("enabled",True))
        out.append({**item,"enabled":enabled,"number":int(row.get("number") or (201+idx)),"name":str(row.get("name") or item["name"]),"override":dict(overrides.get(item["key"]) or {}),"status":status.get(item["key"])})
    out.sort(key=lambda x:(x["number"],x["key"]))
    return {"channels":out}

@app.get("/api/status")
def api_status():
    global _status_cached_at, _status_cached
    now = time.monotonic()
    with _status_lock:
        if _status_cached is not None and now - _status_cached_at < 1.0:
            return _status_cached
    snapshot=weather_manager.snapshot(); settings=config_store.get(); pid=settings.get("primary_location_id"); stream_status=streamer.status()
    sources=dict(snapshot.get("sources") or {}); rstat=radar_manager.status(); pstat=place_manager.status(); sstat=spc_manager.status(); hstat=history_store.status(pid)
    sources["spc"]={"last_success":sstat.get("last_update") if not sstat.get("last_error") else None,"last_error":sstat.get("last_error")}
    sources["radar"]={"last_success":_source_stamp(rstat.get("last_update")),"last_error":rstat.get("last_error")}; sources["geonames"]={"last_success":_source_stamp(pstat.get("last_update")),"last_error":pstat.get("last_error")}
    all_alerts=sum(len(v or []) for v in (snapshot.get("alerts_by_location") or {}).values())
    result = {
        "version":"0.2.5.1","network":{"name":settings.get("station_name"),"callsign":settings.get("station_callsign")},"security":{"admin_authentication":authentication_enabled()},
        "weather":{"last_weather_update":snapshot.get("last_weather_update"),"last_alert_update":snapshot.get("last_alert_update"),"last_error":snapshot.get("last_error"),"locations_loaded":len(snapshot.get("locations",{})),"active_alerts":all_alerts,"location_status":snapshot.get("location_status") or {},"performance":weather_manager.performance_status()},
        "severe_weather":{"takeover_active":renderer.takeover_alert_for(pid) is not None,"top_event":((snapshot.get("alerts_by_location") or {}).get(pid) or [{}])[0].get("event") if ((snapshot.get("alerts_by_location") or {}).get(pid) or []) else None},
        "programming":renderer.programming_status(*renderer._channel_context(pid,"local")),
        "presentation":{
            "scheduled_update_active":any(bool(((row or {}).get("local_on_8s") or {}).get("active")) for row in (stream_status.get("channels") or {}).values()),
            "scheduled_trigger_window":renderer.scheduled_update_active(*renderer._channel_context(pid,"local")),
            "transition":(settings.get("presentation") or {}).get("transition"),
            "retro_effects_enabled":((settings.get("presentation") or {}).get("retro_effects") or {}).get("enabled",False),
        },
        "radar":rstat,"places":pstat,"spc":sstat,"history":hstat,"storm_guidance":{"available":bool((snapshot.get("storm_guidance") or {}).get("hourly")),"last_error":(snapshot.get("storm_guidance") or {}).get("error")},
        "sources":sources,"cache":{**cache_manager.status(),"retention_hours":(settings.get("cache") or {}).get("retention_hours",48)},"render_cache":renderer.context_cache_status(),"stream":stream_status,"tts":tts_manager.status(settings),"notifications":notification_manager.status(),"configured_locations":len(settings.get("locations",[])),
    }
    with _status_lock:
        _status_cached = result
        _status_cached_at = time.monotonic()
    return result

BRANDING_DIR=CONFIG_DIR/"branding"; BRANDING_LOGO=BRANDING_DIR/"logo.png"
@app.get("/branding/logo.png")
def branding_logo():
    path=BRANDING_LOGO if BRANDING_LOGO.exists() else BUILTIN_RWN_LOGO
    if not path.exists(): raise HTTPException(status_code=404,detail="No station logo available.")
    return FileResponse(path,media_type="image/png",headers={"Cache-Control":"no-store"})

@app.put("/api/branding/logo")
async def upload_branding_logo(request:Request):
    body=await request.body()
    if not body or len(body)>5*1024*1024: raise HTTPException(status_code=400,detail="Logo must be an image smaller than 5 MB.")
    try:
        Image.MAX_IMAGE_PIXELS = 20_000_000
        probe=Image.open(BytesIO(body)); probe.verify()
        image=Image.open(BytesIO(body))
        if image.width * image.height > 20_000_000: raise ValueError("image dimensions are too large")
        image=image.convert("RGBA")
        if image.width<16 or image.height<16: raise ValueError("image too small")
        image.thumbnail((1600,1000),Image.Resampling.LANCZOS)
    except Exception as exc: raise HTTPException(status_code=400,detail=f"Unable to read logo image: {exc}") from exc
    BRANDING_DIR.mkdir(parents=True,exist_ok=True); tmp=BRANDING_LOGO.with_suffix(".tmp.png"); image.save(tmp,"PNG",optimize=True); tmp.replace(BRANDING_LOGO); return {"ok":True,"width":image.width,"height":image.height,"applied":"hot"}
@app.delete("/api/branding/logo")
def delete_branding_logo(): BRANDING_LOGO.unlink(missing_ok=True); return {"ok":True,"using_builtin":True,"applied":"hot"}

@app.get("/preview.jpg")
def preview():
    if PREVIEW_PATH.exists(): return FileResponse(PREVIEW_PATH,media_type="image/jpeg",headers={"Cache-Control":"no-store"})
    image=renderer.render(); buf=BytesIO(); image.save(buf,format="JPEG",quality=82); return Response(buf.getvalue(),media_type="image/jpeg",headers={"Cache-Control":"no-store"})

@app.get("/api/preview/{slide_name}.jpg")
def preview_slide(slide_name:str,test_alert:bool=False,location_id:str|None=None,channel_mode:str="local"):
    image=renderer.render_preview(slide_name,test_alert=test_alert,location_id=location_id,channel_mode=channel_mode); buf=BytesIO(); image.save(buf,format="JPEG",quality=86); return Response(buf.getvalue(),media_type="image/jpeg",headers={"Cache-Control":"no-store"})

@app.get("/api/rundown/preview")
def rundown_preview(location_id:str|None=None,channel_mode:str="local"):
    if channel_mode not in {"local","radar","severe"}: raise HTTPException(status_code=400,detail="Unknown channel mode.")
    settings,snapshot=renderer._channel_context(location_id=location_id,channel_mode=channel_mode)
    sequence=renderer._sequence(settings,snapshot,time.time())
    return {
        "channel_mode":channel_mode,
        "location_id":settings.get("_render_location_id"),
        "total_seconds":sum(duration for _,duration in sequence),
        "phases":[{"slide":name,"duration_seconds":duration,"preview_url":f"/api/preview/{name}.jpg?channel_mode={channel_mode}" + (f"&location_id={location_id}" if location_id else "")} for name,duration in sequence],
    }

def _channel_spec_for_key(key: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
        return None
    for spec in channel_specs(config_store.get()):
        if spec.get("key") == key:
            return spec
    return None

@app.api_route("/live/{key}/index.m3u8", methods=["GET", "HEAD"])
async def live_playlist(key: str, request: Request):
    """Start an idle on-demand channel and wait for its first HLS playlist."""
    if _channel_spec_for_key(key) is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")
    worker = streamer.activate_channel(key)
    if worker is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")
    playlist_path = LIVE_DIR / key / "index.m3u8"
    startup_timeout = streamer.startup_timeout()
    deadline = time.monotonic() + startup_timeout
    initial_status = streamer.channel_status(key) or {}
    fallback_seen = int(initial_status.get("hardware_fallback_count") or 0)
    if str(initial_status.get("requested_encoder") or "software") != "software":
        # A cold on-demand hardware tune may need to probe mapped render nodes
        # before FFmpeg starts. Keep that work inside the same client request.
        deadline += min(15, startup_timeout)
    while time.monotonic() < deadline:
        try:
            if playlist_path.exists():
                return FileResponse(playlist_path, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control":"no-cache, no-store, must-revalidate"})
        except OSError:
            pass
        status = streamer.channel_status(key) or {}
        fallback_count = int(status.get("hardware_fallback_count") or 0)
        if fallback_count > fallback_seen:
            # Keep the original tune request alive while the same worker switches
            # from failed QSV/VAAPI to libx264 and produces its first segment.
            fallback_seen = fallback_count
            deadline = max(deadline, time.monotonic() + startup_timeout)
        if status.get("last_error") and not status.get("running") and not status.get("lifecycle_state") == "STARTING":
            break
        await asyncio.sleep(0.15)
    status = streamer.channel_status(key) or {}
    raise HTTPException(status_code=503, detail=f"Channel {key} did not become HLS-ready within {streamer.startup_timeout()} seconds. {status.get('last_error') or ''}".strip(), headers={"Retry-After":"2"})

@app.api_route("/live/{key}/{filename}", methods=["GET", "HEAD"])
def live_segment(key: str, filename: str):
    """Serve HLS transport-stream segments and refresh the channel idle timer."""
    if _channel_spec_for_key(key) is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")
    if not re.fullmatch(r"segment_[0-9]+\.ts", filename):
        raise HTTPException(status_code=404, detail="HLS asset not found.")
    streamer.note_channel_activity(key)
    path = LIVE_DIR / key / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="HLS segment not found.")
    return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control":"public, max-age=30"})

@app.post("/api/channels/{key}/start")
def api_channel_start(key: str):
    if _channel_spec_for_key(key) is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")
    worker = streamer.activate_channel(key)
    if worker is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")
    return {"ok":True,"key":key,"state":"STARTING"}

@app.post("/api/channels/{key}/local8-test")
def api_channel_local8_test(key: str):
    spec = _channel_spec_for_key(key)
    if spec is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")
    if spec.get("mode") != "local":
        raise HTTPException(status_code=400, detail="Local on the 8s is available only on RWN Local channels.")
    if not streamer.start_local8_test(key):
        raise HTTPException(status_code=409, detail="Unable to start Local on the 8s while a severe-weather takeover is active.")
    return {"ok":True,"key":key,"state":"LOCAL_ON_THE_8S"}

@app.post("/api/channels/{key}/stop")
def api_channel_stop(key: str):
    if _channel_spec_for_key(key) is None:
        raise HTTPException(status_code=404, detail="Channel not found or disabled.")
    status = streamer.channel_status(key) or {}
    if status.get("streaming_mode") == "always_on":
        raise HTTPException(status_code=409, detail="Always On channels cannot be stopped while enabled. Change this channel to On Demand first.")
    if not streamer.stop_channel(key):
        raise HTTPException(status_code=404, detail="Channel not found.")
    return {"ok":True,"key":key,"state":"IDLE"}

@app.get("/playlist.m3u")
def playlist(request:Request):
    settings=config_store.get(); base=(settings.get("public_base_url") or str(request.base_url).rstrip("/")).rstrip("/"); logo=f"{base}/branding/logo.png"; lines=["#EXTM3U"]
    for spec in channel_specs(settings):
        lines.append(f'#EXTINF:-1 tvg-id="{spec["id"]}" tvg-name="{spec["name"]}" tvg-chno="{spec.get("number","")}" tvg-logo="{logo}" group-title="Roller Weather Network",{spec["name"]}')
        lines.append(f'{base}/live/{spec["key"]}/index.m3u8')
    lines.append(""); return PlainTextResponse("\n".join(lines),media_type="audio/x-mpegurl")

@app.get("/guide.xml")
def guide_xml():
    settings=config_store.get(); severe={}
    for loc in settings.get("locations",[]): severe[str(loc.get("id"))]=renderer.takeover_alert_for(loc.get("id")) is not None
    return Response(generate_xmltv(settings,severe_by_location=severe,hours=24),media_type="application/xml",headers={"Cache-Control":"no-store"})

@app.post("/api/channels/{key}/restart")
def api_channel_restart(key:str):
    if not streamer.restart_channel(key, reason="manual channel restart"):
        raise HTTPException(status_code=404, detail="Channel is not currently running.")
    return {"ok":True,"key":key}

@app.post("/api/channels/{key}/config")
async def api_channel_config(key:str, request:Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key): raise HTTPException(status_code=400,detail="Invalid channel key.")
    body=await request.json(); settings=config_store.get(); channels=settings.get("channels") or {}; lineup=list(channels.get("lineup") or []); overrides=dict(channels.get("overrides") or {})
    row=next((x for x in lineup if isinstance(x,dict) and x.get("key")==key),None)
    if row is None:
        row={"key":key,"enabled":True,"number":201+len(lineup),"name":""}; lineup.append(row)
    for fld in ("enabled","number","name"):
        if fld in body: row[fld]=body[fld]
    if isinstance(body.get("override"),dict): overrides[key]=dict(body["override"])
    updated=config_store.update_general({"channels":{"lineup":lineup,"overrides":overrides}})
    was_running = streamer.is_channel_running(key)
    streamer.request_reconfigure()
    if was_running:
        streamer.restart_channel(key, reason="channel configuration change")
    return {"ok":True,"channels":updated.get("channels"),"restarted":was_running}

@app.get("/api/tts/status")
def api_tts_status():
    return tts_manager.status(config_store.get())

@app.post("/api/tts/download")
def api_tts_download(payload:TtsVoiceRequest):
    settings=config_store.get()
    if payload.voice:
        settings.setdefault("tts", {})["voice"] = payload.voice
    try:
        return tts_manager.ensure_voice(settings, force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@app.post("/api/tts/test")
def api_tts_test(payload:TtsTestRequest):
    settings=config_store.get()
    test_tts=settings.setdefault("tts", {})
    if payload.voice: test_tts["voice"] = payload.voice
    if payload.speed is not None: test_tts["speed"] = payload.speed
    if payload.volume is not None: test_tts["volume"] = payload.volume
    text=(payload.text or "Roller Weather Network. This is a Local on the 8s text to speech test.").strip()[:700]
    try:
        wav=tts_manager.synthesize_wav_bytes(text, settings)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(wav,media_type="audio/wav",headers={"Cache-Control":"no-store","Content-Disposition":'inline; filename="rwn-tts-test.wav"'})

@app.get("/api/system")
def api_system(): return system_status()

@app.get("/api/encoders")
def api_encoders(): return encoder_capabilities(force=True)

@app.get("/api/profiles")
def api_profiles():
    custom=config_store.get().get("custom_profiles") or {}
    return {"built_in":BUILTIN_PROFILES,"custom":custom}

@app.post("/api/profiles/{profile_name}/apply")
def api_profile_apply(profile_name:str):
    settings=config_store.get(); custom=settings.get("custom_profiles") or {}
    profile=BUILTIN_PROFILES.get(profile_name) or custom.get(profile_name)
    if not isinstance(profile,dict): raise HTTPException(status_code=404,detail="Profile not found.")
    changes=profile.get("settings") if isinstance(profile.get("settings"),dict) else profile
    updated=config_store.update_general(changes); streamer.request_restart(reason=f"profile {profile_name}"); return {"ok":True,"profile":profile_name,"settings":updated}

@app.post("/api/profiles/{profile_name}/save")
def api_profile_save(profile_name:str):
    name=re.sub(r"[^a-z0-9_-]+","-",profile_name.lower()).strip("-")[:32]
    if not name: raise HTTPException(status_code=400,detail="Invalid profile name.")
    settings=config_store.get(); saved={
        "label": profile_name[:48], "description":"Custom WeatherStream profile",
        "settings": {"theme":settings.get("theme"),"video":settings.get("video"),"performance":settings.get("performance"),"presentation":{"transition":(settings.get("presentation") or {}).get("transition"),"background_motion":(settings.get("presentation") or {}).get("background_motion"),"retro_effects":((settings.get("presentation") or {}).get("retro_effects") or {})}}
    }
    custom=dict(settings.get("custom_profiles") or {}); custom[name]=saved; config_store.update_general({"custom_profiles":custom}); return {"ok":True,"name":name}

@app.get("/api/backup")
def api_backup():
    data=create_backup_bytes(config_store.get())
    return Response(data,media_type="application/zip",headers={"Content-Disposition":'attachment; filename="weatherstream-v0.2.5.1-backup.zip"'})

@app.post("/api/backup/restore")
async def api_backup_restore(request:Request):
    data=await request.body()
    if not data or len(data)>100*1024*1024: raise HTTPException(status_code=400,detail="Backup must be a ZIP smaller than 100 MB.")
    try: result=restore_backup_bytes(data,config_store,history_store)
    except Exception as exc: raise HTTPException(status_code=400,detail=f"Unable to restore backup: {exc}") from exc
    weather_manager.request_refresh(); place_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); streamer.request_restart(reason="backup restore"); streamer.request_reconfigure(); return result

@app.post("/api/history/vacuum")
def api_history_vacuum(): return history_store.vacuum()

@app.get("/api/diagnostics")
def api_diagnostics():
    settings=config_store.get(); status=api_status(); channels={"channels":_channel_payload(None)}
    data=create_diagnostics_bytes(settings,status,channels,streamer)
    return Response(data,media_type="application/zip",headers={"Content-Disposition":'attachment; filename="weatherstream-v0.2.5.1-diagnostics.zip"'})

@app.get("/health")
def health(): return {"status":"ok","ready":_service_ready,"version":"0.2.5.1"}

@app.get("/health/live")
def health_live(): return {"status":"ok","version":"0.2.5.1"}

@app.get("/health/ready")
def health_ready():
    if not _service_ready: return JSONResponse({"status":"starting","ready":False,"version":"0.2.5.1"},status_code=503)
    return {"status":"ok","ready":True,"version":"0.2.5.1"}

@app.get("/metrics", response_class=PlainTextResponse)
def metrics(): return PlainTextResponse(observability.prometheus(), media_type="text/plain; version=0.0.4")

@app.get("/api/events")
def api_events(limit:int=100): return {"events":observability.events(limit),"authentication_enabled":authentication_enabled()}

@app.get("/api/notifications/status")
def api_notifications_status(): return notification_manager.status()

@app.post("/api/notifications/test")
def api_notifications_test():
    try: notification_manager.enqueue_test()
    except (ValueError, queue.Full) as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
    return {"ok":True,"queued":True}
