from __future__ import annotations

import copy
import datetime as dt
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

SPC_BASE = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/FeatureServer"
LAYERS = {"day1": 1, "day2": 9, "day3": 17}
RISK_ORDER = {"NONE": 0, "TSTM": 1, "MRGL": 2, "SLGT": 3, "ENH": 4, "MDT": 5, "HIGH": 6}
RISK_NAMES = {
    "NONE": "No Categorical Risk", "TSTM": "General Thunderstorms", "MRGL": "Marginal Risk",
    "SLGT": "Slight Risk", "ENH": "Enhanced Risk", "MDT": "Moderate Risk", "HIGH": "High Risk",
}


def _normalize_label(value: Any) -> str:
    label = str(value or "").strip().upper()
    aliases = {
        "GENERAL THUNDER": "TSTM", "GENERAL THUNDERSTORMS": "TSTM", "MARGINAL": "MRGL",
        "SLIGHT": "SLGT", "ENHANCED": "ENH", "MODERATE": "MDT",
    }
    return aliases.get(label, label if label in RISK_ORDER else "NONE")


def fetch_spc_point(location: dict[str, Any], timeout: float = 12.0, client: httpx.Client | None = None) -> dict[str, Any]:
    lon = float(location["longitude"]); lat = float(location["latitude"])
    results: dict[str, Any] = {}
    owned = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        for name, layer in LAYERS.items():
            url = f"{SPC_BASE}/{layer}/query"
            params = {
                "f": "json",
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "label,label2,issue,expire,idp_filedate",
                "returnGeometry": "false",
            }
            response = client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"].get("message") or f"SPC {name} query failed")
            features = payload.get("features") or []
            candidates = []
            for f in features:
                a = f.get("attributes") or {}
                risk = _normalize_label(a.get("label") or a.get("label2"))
                candidates.append({
                    "risk": risk,
                    "name": RISK_NAMES[risk],
                    "rank": RISK_ORDER[risk],
                    "issue": a.get("issue"),
                    "expire": a.get("expire"),
                    "filedate": a.get("idp_filedate"),
                })
            best = max(candidates, key=lambda x: x["rank"], default={"risk": "NONE", "name": RISK_NAMES["NONE"], "rank": 0})
            results[name] = best
    finally:
        if owned:
            client.close()
    results["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    results["source"] = "NOAA/NWS Storm Prediction Center"
    return results


class SPCManager:
    """SPC categorical outlooks for every configured ZIP."""
    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self._lock = threading.RLock(); self._stop = threading.Event(); self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._revision = 0
        workers = max(1, min(8, int(os.environ.get("WEATHERSTREAM_REFRESH_WORKERS", "4"))))
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spc-refresh")
        self._client = httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True, limits=httpx.Limits(max_connections=max(6, workers * 2), max_keepalive_connections=max(4, workers)))
        self._snapshot: dict[str, Any] = {"outlook": {}, "by_location": {}, "last_update": None, "last_error": None, "errors_by_location": {}}

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._thread = threading.Thread(target=self._run, name="spc-refresh", daemon=True); self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()
        if self._thread and self._thread is not threading.current_thread(): self._thread.join(timeout=5.0)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._client.close()

    def request_refresh(self) -> None:
        self._wake.set()

    def snapshot(self, location_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            snap = copy.deepcopy(self._snapshot)
        if location_id:
            return {
                "outlook": copy.deepcopy((snap.get("by_location") or {}).get(location_id) or {}),
                "last_update": snap.get("last_update"),
                "last_error": (snap.get("errors_by_location") or {}).get(location_id),
            }
        return snap

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def status(self) -> dict[str, Any]:
        snap = self.snapshot(); day1 = (snap.get("outlook") or {}).get("day1") or {}
        by_location = snap.get("by_location") or {}
        return {
            "available": bool(by_location or snap.get("outlook")),
            "locations_loaded": len(by_location),
            "day1_risk": day1.get("risk"), "day1_name": day1.get("name"),
            "last_update": snap.get("last_update"), "last_error": snap.get("last_error"),
            "errors_by_location": snap.get("errors_by_location") or {},
        }

    def _run(self) -> None:
        next_refresh = 0.0
        while not self._stop.is_set():
            settings = self.config_store.get(); cfg = settings.get("spc") or {}; now = time.monotonic()
            if cfg.get("enabled", True) and now >= next_refresh:
                locations = list(settings.get("locations") or [])
                primary_id = settings.get("primary_location_id")
                with self._lock:
                    previous = copy.deepcopy(self._snapshot.get("by_location") or {})
                by_location: dict[str, Any] = {}
                errors: dict[str, str] = {}
                def fetch_location(loc: dict[str, Any]):
                    lid = loc.get("id")
                    try:
                        return lid, fetch_spc_point(loc, client=self._client), None
                    except Exception as exc:
                        return lid, previous.get(lid), str(exc)
                futures = [self._executor.submit(fetch_location, loc) for loc in locations if loc.get("id")]
                for future in as_completed(futures):
                    lid, outlook, error = future.result()
                    if outlook is not None:
                        by_location[lid] = outlook
                    if error:
                        errors[lid] = error
                primary = copy.deepcopy(by_location.get(primary_id) or {})
                with self._lock:
                    self._snapshot = {
                        "outlook": primary,
                        "by_location": by_location,
                        "last_update": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "last_error": "; ".join(errors.values()) if errors and not by_location else None,
                        "errors_by_location": errors,
                    }
                    self._revision += 1
                next_refresh = now + max(300, int(cfg.get("refresh_seconds", 900)))
            self._wake.wait(timeout=30)
            if self._wake.is_set(): self._wake.clear(); next_refresh = 0.0
