from __future__ import annotations

import io
import json
import math
import os
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

GEONAMES_CITIES_URL = "https://download.geonames.org/export/dump/cities5000.zip"
CACHE_DIR = Path(os.environ.get("WEATHERSTREAM_PLACE_CACHE", "/config/cache/places"))
CITY_DB = CACHE_DIR / "us-cities5000.json"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class PlaceManager:
    """Caches a compact U.S. city index for automatic map labels.

    GeoNames cities5000 is CC BY 4.0 and is downloaded at runtime once, then
    reduced to U.S. populated places in /config. Rendering never calls GeoNames.
    """

    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._cities: list[dict[str, Any]] = []
        self._last_update: float | None = None
        self._last_error: str | None = None
        self._client = httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0), follow_redirects=True, headers={"User-Agent": "WeatherStream/0.3.0"}, limits=httpx.Limits(max_connections=4, max_keepalive_connections=2))
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_cache()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="place-manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        self._client.close()

    def request_refresh(self) -> None:
        self._wake.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cities_loaded": len(self._cities),
                "last_update": self._last_update,
                "last_error": self._last_error,
                "source": "GeoNames cities5000",
            }

    def nearby(
        self,
        lat: float,
        lon: float,
        radius_miles: float = 180.0,
        max_count: int = 10,
        min_population: int = 5000,
    ) -> list[dict[str, Any]]:
        with self._lock:
            source = list(self._cities)
        candidates: list[dict[str, Any]] = []
        for city in source:
            pop = int(city.get("population") or 0)
            if pop < min_population:
                continue
            try:
                distance = haversine_miles(lat, lon, float(city["latitude"]), float(city["longitude"]))
            except Exception:
                continue
            if distance > radius_miles:
                continue
            item = dict(city)
            item["distance_miles"] = distance
            # Favor significant nearby population centers without letting a distant
            # large city crowd every smaller regional label off the map.
            item["label_score"] = math.log10(max(1000, pop)) * 36.0 - distance * 0.13
            candidates.append(item)
        candidates.sort(key=lambda x: (-x["label_score"], x["distance_miles"], -int(x.get("population") or 0)))
        return candidates[: max(1, min(24, int(max_count)))]

    def _load_cache(self) -> None:
        if not CITY_DB.exists():
            return
        try:
            data = json.loads(CITY_DB.read_text(encoding="utf-8"))
            if isinstance(data, list):
                with self._lock:
                    self._cities = data
                    self._last_update = CITY_DB.stat().st_mtime
        except Exception as exc:
            with self._lock:
                self._last_error = f"Unable to read city cache: {exc}"

    def _run(self) -> None:
        # Refresh no more than weekly; GeoNames is a static gazetteer, not live weather.
        while not self._stop.is_set():
            stale = not CITY_DB.exists() or (time.time() - CITY_DB.stat().st_mtime) > 7 * 86400
            if stale:
                try:
                    self._refresh()
                    with self._lock:
                        self._last_error = None
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)
                    self._load_cache()
            self._wake.wait(6 * 3600)
            self._wake.clear()

    def _refresh(self) -> None:
        settings = self.config_store.get()
        ua = settings.get("nws_user_agent") or "WeatherStream/0.3.0 (Roller Weather Network local weather display)"
        self._client.headers["User-Agent"] = ua
        resp = self._client.get(GEONAMES_CITIES_URL, timeout=45.0)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            name = next((n for n in zf.namelist() if n.endswith(".txt")), None)
            if not name:
                raise RuntimeError("GeoNames cities5000 archive did not contain its data file.")
            raw = zf.read(name).decode("utf-8", errors="replace")

        cities: list[dict[str, Any]] = []
        for line in raw.splitlines():
            cols = line.split("\t")
            if len(cols) < 19 or cols[8] != "US" or cols[6] != "P":
                continue
            try:
                cities.append({
                    "id": int(cols[0]),
                    "name": cols[1],
                    "latitude": float(cols[4]),
                    "longitude": float(cols[5]),
                    "feature_code": cols[7],
                    "admin1": cols[10],
                    "population": int(cols[14] or 0),
                    "timezone": cols[17],
                })
            except Exception:
                continue

        if not cities:
            raise RuntimeError("GeoNames city index parsed zero U.S. places.")
        tmp = CITY_DB.with_suffix(".tmp")
        tmp.write_text(json.dumps(cities, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CITY_DB)
        with self._lock:
            self._cities = cities
            self._last_update = time.time()
