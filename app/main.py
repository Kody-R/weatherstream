from __future__ import annotations

import datetime as dt
import os
import re
import time
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from PIL import Image

from app.cache import CacheManager
from app.config import CONFIG_DIR, ConfigStore
from app.guide import generate_xmltv
from app.history import HistoryStore
from app.places import PlaceManager
from app.radar import RadarManager
from app.renderer import BUILTIN_RWN_LOGO, WeatherRenderer
from app.spc import SPCManager
from app.streamer import LIVE_DIR, PREVIEW_PATH, Streamer
from app.weather import WeatherManager, resolve_zip

BASE = Path(__file__).resolve().parent
config_store = ConfigStore()
history_store = HistoryStore()
cache_manager = CacheManager(config_store)
weather_manager = WeatherManager(config_store, history_store)
place_manager = PlaceManager(config_store)
radar_manager = RadarManager(config_store)
spc_manager = SPCManager(config_store)
renderer = WeatherRenderer(config_store, weather_manager, radar_manager, place_manager, history_store, spc_manager)
streamer = Streamer(config_store, renderer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    weather_manager.start(); place_manager.start(); radar_manager.start(); spc_manager.start(); cache_manager.start(); streamer.start()
    yield
    streamer.stop(); cache_manager.stop(); spc_manager.stop(); radar_manager.stop(); place_manager.stop(); weather_manager.stop()


app = FastAPI(title="WeatherStream / Roller Weather Network", version="0.1.7.1", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
LIVE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/live", StaticFiles(directory=str(LIVE_DIR)), name="live")


class ZipRequest(BaseModel): postal_code: str
class SettingsRequest(BaseModel):
    station_name: str | None = None; station_callsign: str | None = None; station_slogan: str | None = None
    service_area: str | None = None; public_base_url: str | None = None; theme: str | None = None
    weather_refresh_seconds: int | None = None; alert_refresh_seconds: int | None = None; nws_user_agent: str | None = None
    music: dict | None = None; radar: dict | None = None; alerts: dict | None = None; presentation: dict | None = None
    slides: dict | None = None; branding: dict | None = None; maps: dict | None = None; storm_guidance: dict | None = None
    spc: dict | None = None; history: dict | None = None; smart_programming: dict | None = None; dayparts: dict | None = None; cache: dict | None = None


@app.get("/", response_class=HTMLResponse)
def root(request: Request): return templates.TemplateResponse("index.html", {"request": request})

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request): return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/api/settings")
def api_settings(): return config_store.get()

@app.post("/api/settings")
def api_update_settings(payload: SettingsRequest):
    updated = config_store.update_general(payload.model_dump(exclude_none=True))
    weather_manager.request_refresh(); place_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); streamer.request_restart()
    return updated

@app.post("/api/locations")
def api_add_location(payload: ZipRequest):
    postal = payload.postal_code.strip()
    if not re.fullmatch(r"\d{5}", postal): raise HTTPException(status_code=400, detail="v0.1.7.1 accepts 5-digit U.S. ZIP codes.")
    try: resolved = resolve_zip(postal)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc: raise HTTPException(status_code=502, detail=f"Unable to resolve ZIP code: {exc}") from exc
    location = config_store.add_location(resolved); weather_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); return location

@app.delete("/api/locations/{location_id}")
def api_remove_location(location_id: str):
    if not config_store.remove_location(location_id): raise HTTPException(status_code=404, detail="Location not found.")
    weather_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); return {"ok": True}

@app.post("/api/locations/{location_id}/primary")
def api_set_primary(location_id: str):
    if not config_store.set_primary(location_id): raise HTTPException(status_code=404, detail="Location not found.")
    weather_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); return {"ok": True}

@app.post("/api/refresh")
def api_refresh():
    weather_manager.request_refresh(); place_manager.request_refresh(); radar_manager.request_refresh(); spc_manager.request_refresh(); return {"ok": True}

@app.post("/api/stream/restart")
def api_stream_restart(): streamer.request_restart(); return {"ok": True}


@app.post('/api/cache/clean')
def api_cache_clean(): return cache_manager.clean()

def _source_stamp(value):
    if isinstance(value, (int, float)):
        try: return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).isoformat()
        except Exception: return None
    return value

@app.get("/api/status")
def api_status():
    snapshot=weather_manager.snapshot(); settings=config_store.get(); pid=settings.get('primary_location_id')
    sources=dict(snapshot.get('sources') or {})
    rstat=radar_manager.status(); pstat=place_manager.status(); sstat=spc_manager.status(); hstat=history_store.status(pid)
    sources['spc']={'last_success':sstat.get('last_update') if not sstat.get('last_error') else None,'last_error':sstat.get('last_error')}
    sources['radar']={'last_success':_source_stamp(rstat.get('last_update')),'last_error':rstat.get('last_error')}
    sources['geonames']={'last_success':_source_stamp(pstat.get('last_update')),'last_error':pstat.get('last_error')}
    return {
        'version':'0.1.7.1',
        'network': {'name':settings.get('station_name'),'callsign':settings.get('station_callsign')},
        'weather': {'last_weather_update':snapshot.get('last_weather_update'),'last_alert_update':snapshot.get('last_alert_update'),'last_error':snapshot.get('last_error'),'locations_loaded':len(snapshot.get('locations',{})),'active_alerts':len(snapshot.get('alerts',[]))},
        'severe_weather': {'takeover_active':renderer._takeover_alert(settings,snapshot) is not None,'top_event':(snapshot.get('alerts') or [{}])[0].get('event') if snapshot.get('alerts') else None},
        'programming': renderer.programming_status(settings,snapshot),
        'presentation': {'scheduled_update_active':renderer.scheduled_update_active(settings,snapshot),'transition':(settings.get('presentation') or {}).get('transition'),'retro_effects_enabled':((settings.get('presentation') or {}).get('retro_effects') or {}).get('enabled',False)},
        'radar':rstat,'places':pstat,'spc':sstat,'history':hstat,
        'storm_guidance': {'available':bool((snapshot.get('storm_guidance') or {}).get('hourly')),'last_error':(snapshot.get('storm_guidance') or {}).get('error')},
        'sources':sources,
        'cache': {**cache_manager.status(),'retention_hours':(settings.get('cache') or {}).get('retention_hours',48)},
        'stream':streamer.status(),'configured_locations':len(settings.get('locations',[])),
    }

BRANDING_DIR=CONFIG_DIR/'branding'; BRANDING_LOGO=BRANDING_DIR/'logo.png'

@app.get('/branding/logo.png')
def branding_logo():
    path=BRANDING_LOGO if BRANDING_LOGO.exists() else BUILTIN_RWN_LOGO
    if not path.exists(): raise HTTPException(status_code=404,detail='No station logo available.')
    return FileResponse(path,media_type='image/png',headers={'Cache-Control':'no-store'})

@app.put('/api/branding/logo')
async def upload_branding_logo(request: Request):
    body=await request.body()
    if not body or len(body)>5*1024*1024: raise HTTPException(status_code=400,detail='Logo must be an image smaller than 5 MB.')
    try:
        image=Image.open(BytesIO(body)).convert('RGBA')
        if image.width<16 or image.height<16: raise ValueError('image too small')
        image.thumbnail((1600,1000),Image.Resampling.LANCZOS)
    except Exception as exc: raise HTTPException(status_code=400,detail=f'Unable to read logo image: {exc}') from exc
    BRANDING_DIR.mkdir(parents=True,exist_ok=True); tmp=BRANDING_LOGO.with_suffix('.tmp.png'); image.save(tmp,'PNG',optimize=True); tmp.replace(BRANDING_LOGO); streamer.request_restart()
    return {'ok':True,'width':image.width,'height':image.height}

@app.delete('/api/branding/logo')
def delete_branding_logo():
    BRANDING_LOGO.unlink(missing_ok=True); streamer.request_restart(); return {'ok':True,'using_builtin':True}

@app.get('/preview.jpg')
def preview():
    if PREVIEW_PATH.exists(): return FileResponse(PREVIEW_PATH,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    image=renderer.render(); buf=BytesIO(); image.save(buf,format='JPEG',quality=82); return Response(buf.getvalue(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})

@app.get('/api/preview/{slide_name}.jpg')
def preview_slide(slide_name: str, test_alert: bool=False):
    image=renderer.render_preview(slide_name,test_alert=test_alert); buf=BytesIO(); image.save(buf,format='JPEG',quality=86); return Response(buf.getvalue(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})

@app.get('/playlist.m3u')
def playlist(request: Request):
    settings=config_store.get(); base=(settings.get('public_base_url') or str(request.base_url).rstrip('/')).rstrip('/'); station=settings.get('station_name') or 'Roller Weather Network'
    body='\n'.join(['#EXTM3U',f'#EXTINF:-1 tvg-id="rwn.local" tvg-name="{station}" group-title="Weather",{station}',f'{base}/live/weather.m3u8',''])
    return PlainTextResponse(body,media_type='audio/x-mpegurl')

@app.get('/guide.xml')
def guide_xml():
    settings=config_store.get(); severe=renderer._takeover_alert(settings,weather_manager.snapshot()) is not None
    return Response(generate_xmltv(settings,severe_active=severe,hours=24),media_type='application/xml',headers={'Cache-Control':'no-store'})

@app.get('/health')
def health(): return {'status':'ok','version':'0.1.7.1','stream':streamer.status()}
