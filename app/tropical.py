from __future__ import annotations

import copy
import datetime as dt
import io
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.observability import observability
from app.tropical_logic import clean_outlook_text, evaluate_activation, hurricane_season_active, normalize_storm, outlook_development_max


CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/maps/currentStorms/currentStorms.json"
ATLANTIC_OUTLOOK_URL = "https://www.nhc.noaa.gov/xml/TWOAT.xml"
ALLOWED_NHC_HOSTS = {"nhc.noaa.gov", "www.nhc.noaa.gov", "hurricanes.gov", "www.hurricanes.gov"}


def _track_from_kmz(payload: bytes) -> list[list[float]]:
    if len(payload) > 12 * 1024 * 1024:
        raise ValueError("NHC track archive is too large")
    points: list[list[float]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [info for info in archive.infolist() if info.filename.lower().endswith(".kml") and info.file_size <= 5 * 1024 * 1024]
        if not members:
            return points
        root = ET.fromstring(archive.read(members[0]))
    for node in root.iter():
        if not node.tag.lower().endswith("coordinates") or not node.text: continue
        for token in node.text.replace("\n", " ").split():
            pieces = token.split(",")
            if len(pieces) < 2: continue
            try: lon, lat = float(pieces[0]), float(pieces[1])
            except ValueError: continue
            if -180 <= lon <= 180 and -90 <= lat <= 90 and ([lat, lon] not in points):
                points.append([lat, lon])
    return points[:40]


def _parse_outlook(payload: bytes) -> dict[str, Any]:
    root = ET.fromstring(payload)
    item = next((node for node in root.iter() if node.tag.lower().endswith("item")), None)
    values: dict[str, str] = {}
    if item is not None:
        for child in item:
            values[child.tag.split("}")[-1].lower()] = child.text or ""
    text = clean_outlook_text(values.get("description") or values.get("summary") or values.get("title") or "")
    return {
        "title": clean_outlook_text(values.get("title") or "Atlantic Tropical Weather Outlook")[:180],
        "text": text,
        "issued": values.get("pubdate") or values.get("updated"),
        "link": values.get("link"),
        "development_max": outlook_development_max(text),
        "gulf_mentioned": "gulf" in text.lower(),
    }


class TropicalManager:
    """Cached official-NHC status, outlook, and forecast-track data."""

    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self._lock = threading.RLock(); self._stop = threading.Event(); self._wake = threading.Event()
        self._thread: threading.Thread | None = None; self._revision = 0
        self._client = httpx.Client(timeout=httpx.Timeout(18.0, connect=8.0), follow_redirects=False, headers={"User-Agent":"WeatherStream/0.3.0 (RWN Tropics Watch)"}, limits=httpx.Limits(max_connections=6,max_keepalive_connections=4))
        self._snapshot: dict[str, Any] = {"systems":[],"outlook":{},"last_update":None,"last_error":None}
        self._last_trigger_at: float | None = None; self._activation_state = False; self._activation_reasons: list[str] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread=threading.Thread(target=self._run,name="tropical-refresh",daemon=True); self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()
        if self._thread and self._thread is not threading.current_thread(): self._thread.join(timeout=5.0)
        self._client.close()

    def request_refresh(self) -> None: self._wake.set()

    def revision(self) -> int:
        with self._lock: return self._revision

    def snapshot(self) -> dict[str, Any]:
        with self._lock: return copy.deepcopy(self._snapshot)

    def _get_nhc(self, url: str) -> httpx.Response:
        current=url
        for _ in range(3):
            parsed=urlsplit(current)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_NHC_HOSTS:
                raise ValueError("NHC data URL left the official host allow-list")
            response=self._client.get(current)
            if response.status_code not in {301,302,303,307,308}:
                response.raise_for_status(); return response
            location=response.headers.get("location")
            if not location: response.raise_for_status()
            current=urljoin(current,location)
        raise ValueError("Too many NHC redirects")

    def _fetch_track(self, storm: dict[str, Any]) -> list[list[float]]:
        url = str((storm.get("forecastTrack") or {}).get("kmzFile") or "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ALLOWED_NHC_HOSTS: return []
        response=self._get_nhc(url)
        return _track_from_kmz(response.content)

    def _refresh_once(self) -> None:
        previous=self.snapshot(); previous_error=previous.get("last_error"); errors=[]; systems=[]; outlook=previous.get("outlook") or {}
        try:
            response=self._get_nhc(CURRENT_STORMS_URL); payload=response.json()
            raw_systems=payload.get("activeStorms") if isinstance(payload,dict) else []
            for raw in raw_systems or []:
                if not isinstance(raw,dict): continue
                normalized=normalize_storm(raw)
                if normalized is None: continue
                try: normalized["track"]=self._fetch_track(raw)
                except Exception as exc: errors.append(f"{normalized['name']} track: {exc}")
                systems.append(normalized)
        except Exception as exc: errors.append(f"current storms: {exc}"); systems=previous.get("systems") or []
        try:
            response=self._get_nhc(ATLANTIC_OUTLOOK_URL); outlook=_parse_outlook(response.content)
        except Exception as exc: errors.append(f"outlook: {exc}")
        old_ids={x.get("id") for x in previous.get("systems") or []}; new_ids={x.get("id") for x in systems}
        with self._lock:
            self._snapshot={"systems":systems,"outlook":outlook,"last_update":dt.datetime.now(dt.timezone.utc).isoformat(),"last_error":"; ".join(errors) if errors else None}
            self._revision += 1
        next_error="; ".join(errors) if errors else None
        if next_error and next_error != previous_error:
            observability.event("source","NHC tropical source degraded",source="nhc_tropical",state="error",error=next_error[:500])
        elif not next_error and previous_error:
            observability.event("source","NHC tropical source recovered",source="nhc_tropical",state="recovered")
        for storm in systems:
            if storm.get("id") not in old_ids:
                observability.event("tropical",f"NHC began tracking {storm.get('name')}",storm_id=storm.get("id"),classification=storm.get("classification_name"))
        for storm_id in old_ids-new_ids:
            observability.event("tropical","NHC system no longer active",storm_id=storm_id)

    def activation_status(self, location: dict[str, Any] | None = None, alerts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        cfg=self.config_store.get().get("tropical") or {}; snapshot=self.snapshot()
        direct=evaluate_activation(snapshot,location,alerts,cfg) if cfg.get("enabled",True) else {"triggered":False,"reasons":[],"nearest":None,"local_alerts":[]}
        now=time.time(); cooldown=max(0,min(86400,int(cfg.get("cooldown_seconds",21600))))
        with self._lock:
            if direct["triggered"]: self._last_trigger_at=now
            active=bool(direct["triggered"] or (self._last_trigger_at and now-self._last_trigger_at<cooldown))
            reasons=direct["reasons"] or (["Activation cooldown after the last official trigger"] if active else [])
            if active != self._activation_state:
                observability.event("tropical","Tropics Watch channel activated" if active else "Tropics Watch channel returned to standby",reasons=reasons)
            self._activation_state=active; self._activation_reasons=list(reasons)
        return {**direct,"active":active,"reasons":reasons,"cooldown_remaining_seconds":round(max(0,cooldown-(now-(self._last_trigger_at or 0)))) if self._last_trigger_at else 0}

    def status(self, location: dict[str, Any] | None = None, alerts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        snap=self.snapshot(); activation=self.activation_status(location,alerts); cfg=self.config_store.get().get("tropical") or {}
        return {"enabled":bool(cfg.get("enabled",True)),"season_active":hurricane_season_active(),"segment_active":bool(cfg.get("segment_enabled",True) and (hurricane_season_active() or snap.get("systems"))),"systems":snap.get("systems") or [],"system_count":len(snap.get("systems") or []),"outlook":snap.get("outlook") or {},"last_update":snap.get("last_update"),"last_error":snap.get("last_error"),"activation":activation}

    def _run(self) -> None:
        next_refresh=0.0
        while not self._stop.is_set():
            settings=self.config_store.get(); cfg=settings.get("tropical") or {}; now=time.monotonic()
            if cfg.get("enabled",True) and now>=next_refresh:
                try: self._refresh_once()
                except Exception as exc:
                    with self._lock: self._snapshot["last_error"]=str(exc); self._revision+=1
                next_refresh=now+max(300,min(3600,int(cfg.get("refresh_seconds",600))))
            self._wake.wait(timeout=30)
            if self._wake.is_set(): self._wake.clear(); next_refresh=0.0
