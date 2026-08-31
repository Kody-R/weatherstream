from __future__ import annotations

import copy
import datetime as dt
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from app.observability import observability

OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GFS = "https://api.open-meteo.com/v1/gfs"
NWS_ALERTS = "https://api.weather.gov/alerts/active"
NWS_POINTS = "https://api.weather.gov/points/{lat},{lon}"

WMO_DESCRIPTIONS = {
    0: "Clear", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing Fog", 51: "Light Drizzle", 53: "Drizzle",
    55: "Heavy Drizzle", 56: "Freezing Drizzle", 57: "Heavy Freezing Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain", 66: "Freezing Rain",
    67: "Heavy Freezing Rain", 71: "Light Snow", 73: "Snow", 75: "Heavy Snow",
    77: "Snow Grains", 80: "Rain Showers", 81: "Rain Showers", 82: "Heavy Showers",
    85: "Snow Showers", 86: "Heavy Snow Showers", 95: "Thunderstorms",
    96: "Thunderstorms / Hail", 99: "Severe Thunderstorms / Hail",
}


def describe_weather(code: int | None) -> str:
    return WMO_DESCRIPTIONS.get(int(code) if code is not None else -1, "Weather Unavailable")


def wind_direction(degrees: float | None) -> str:
    if degrees is None:
        return "--"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((degrees + 11.25) / 22.5) % 16]


def resolve_zip(postal_code: str) -> dict[str, Any]:
    with httpx.Client(timeout=12.0, follow_redirects=True) as client:
        response = client.get(
            OPEN_METEO_GEOCODE,
            params={"name": postal_code, "count": 10, "language": "en", "format": "json", "countryCode": "US"},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            raise ValueError(f"ZIP code {postal_code} was not found.")
        selected = next((r for r in results if postal_code in (r.get("postcodes") or [])), results[0])
        return {
            "postal_code": postal_code,
            "name": selected.get("name", postal_code),
            "admin1": selected.get("admin1", ""),
            "country_code": selected.get("country_code", "US"),
            "latitude": selected["latitude"],
            "longitude": selected["longitude"],
            "timezone": selected.get("timezone", "auto"),
        }


def fetch_forecast(location: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "forecast_days": 7,
        "current": ",".join([
            "temperature_2m", "relative_humidity_2m", "apparent_temperature", "is_day",
            "precipitation", "weather_code", "cloud_cover", "surface_pressure",
            "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        ]),
        "hourly": ",".join([
            "temperature_2m", "apparent_temperature", "precipitation_probability",
            "precipitation", "weather_code", "relative_humidity_2m", "wind_speed_10m",
            "wind_gusts_10m", "cape", "lifted_index",
        ]),
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_probability_max", "precipitation_sum", "sunrise", "sunset",
            "daylight_duration", "sunshine_duration", "uv_index_max",
        ]),
    }
    owned = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        response = client.get(OPEN_METEO_FORECAST, params=params, timeout=15.0)
        response.raise_for_status()
        data = response.json()
    finally:
        if owned:
            client.close()

    current = data.get("current", {})
    current["description"] = describe_weather(current.get("weather_code"))
    current["wind_cardinal"] = wind_direction(current.get("wind_direction_10m"))
    return {
        "location": copy.deepcopy(location),
        "timezone": data.get("timezone", location.get("timezone", "")),
        "current": current,
        "hourly": data.get("hourly", {}),
        "daily": data.get("daily", {}),
        "nws": {"periods": [], "office": "", "error": None},
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }



def fetch_storm_guidance(location: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch NOAA-model thunderstorm guidance via Open-Meteo's GFS/NBM API.

    This is forecast guidance, not observed lightning-strike data. WeatherStream
    labels it accordingly and never uses it to issue alerts.
    """
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "timezone": "auto",
        "forecast_days": 2,
        "wind_speed_unit": "mph",
        "hourly": ",".join([
            "thunderstorm_probability", "cape", "lifted_index",
            "precipitation_probability", "wind_gusts_10m",
        ]),
    }
    owned = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        response = client.get(OPEN_METEO_GFS, params=params, timeout=15.0)
        response.raise_for_status()
        data = response.json()
    finally:
        if owned:
            client.close()
    return {
        "hourly": data.get("hourly", {}),
        "timezone": data.get("timezone", location.get("timezone", "")),
        "model": "NOAA GFS/NBM via Open-Meteo",
        "error": None,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

def fetch_nws_forecast(location: dict[str, Any], user_agent: str, client: httpx.Client | None = None) -> dict[str, Any]:
    headers = {
        "User-Agent": user_agent or "WeatherStream/0.2.5.1 (Roller Weather Network local weather display)",
        "Accept": "application/geo+json",
    }
    lat = float(location["latitude"])
    lon = float(location["longitude"])
    owned = client is None
    client = client or httpx.Client(timeout=12.0, follow_redirects=True)
    try:
        point_resp = client.get(NWS_POINTS.format(lat=f"{lat:.4f}", lon=f"{lon:.4f}"), headers=headers, timeout=12.0)
        point_resp.raise_for_status()
        props = point_resp.json().get("properties") or {}
        forecast_url = props.get("forecast")
        if not forecast_url:
            raise RuntimeError("NWS points response did not include a forecast link.")
        forecast_resp = client.get(forecast_url, headers=headers, timeout=12.0)
        forecast_resp.raise_for_status()
        forecast_props = forecast_resp.json().get("properties") or {}
        periods = forecast_props.get("periods") or []
    finally:
        if owned:
            client.close()
    clean_periods = []
    for p in periods[:14]:
        clean_periods.append({
            "number": p.get("number"),
            "name": p.get("name") or "Forecast",
            "startTime": p.get("startTime"),
            "endTime": p.get("endTime"),
            "isDaytime": p.get("isDaytime"),
            "temperature": p.get("temperature"),
            "temperatureUnit": p.get("temperatureUnit") or "F",
            "windSpeed": p.get("windSpeed") or "",
            "windDirection": p.get("windDirection") or "",
            "shortForecast": p.get("shortForecast") or "",
            "detailedForecast": p.get("detailedForecast") or "",
        })
    return {
        "periods": clean_periods,
        "office": props.get("cwa") or props.get("gridId") or "",
        "forecast_url": forecast_url,
        "error": None,
    }


def fetch_alerts(location: dict[str, Any], user_agent: str, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": user_agent or "WeatherStream/0.2.5.1 (Roller Weather Network local weather display)",
        "Accept": "application/geo+json",
    }
    point = f"{float(location['latitude']):.4f},{float(location['longitude']):.4f}"
    owned = client is None
    client = client or httpx.Client(timeout=12.0, follow_redirects=True)
    try:
        response = client.get(NWS_ALERTS, params={"point": point}, headers=headers, timeout=12.0)
        response.raise_for_status()
        features = response.json().get("features") or []
    finally:
        if owned:
            client.close()

    alerts: list[dict[str, Any]] = []
    for item in features:
        props = item.get("properties") or {}
        alerts.append({
            "id": props.get("id") or item.get("id"),
            "event": props.get("event") or "Weather Alert",
            "headline": props.get("headline") or props.get("event") or "Weather Alert",
            "severity": props.get("severity") or "Unknown",
            "urgency": props.get("urgency") or "Unknown",
            "certainty": props.get("certainty") or "Unknown",
            "areaDesc": props.get("areaDesc") or "",
            "description": props.get("description") or "",
            "instruction": props.get("instruction") or "",
            "effective": props.get("effective"),
            "onset": props.get("onset"),
            "ends": props.get("ends"),
            "expires": props.get("expires"),
            "senderName": props.get("senderName") or "National Weather Service",
            # Polygon/MultiPolygon for county-based warnings; some zone products legitimately have null geometry.
            "geometry": copy.deepcopy(item.get("geometry")),
        })
    severity_order = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}
    alerts.sort(key=lambda a: severity_order.get(a.get("severity", "Unknown"), 4))
    return alerts


class WeatherManager:
    """Refreshes weather for every configured ZIP and preserves cached data on failures.

    v0.1.8.1 keeps per-location NWS forecasts, alerts, storm guidance and freshness
    metadata so each ZIP can drive its own independent RWN channel.
    """

    def __init__(self, config_store, history_store=None) -> None:
        self.config_store = config_store
        self.history_store = history_store
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._revision = 0
        workers = max(1, min(8, int(os.environ.get("WEATHERSTREAM_REFRESH_WORKERS", "4"))))
        self._refresh_workers = workers
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="weather-refresh")
        self._client = httpx.Client(
            timeout=httpx.Timeout(18.0, connect=8.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=max(8, workers * 3), max_keepalive_connections=max(4, workers * 2), keepalive_expiry=30.0),
        )
        self._snapshot: dict[str, Any] = {
            "locations": {},
            "alerts": [],
            "alerts_by_location": {},
            "storm_guidance": {},
            "storm_guidance_by_location": {},
            "location_status": {},
            "last_weather_update": None,
            "last_alert_update": None,
            "last_error": None,
            "sources": {},
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="weather-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._client.close()

    def request_refresh(self) -> None:
        self._wake.set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def performance_status(self) -> dict[str, int]:
        with self._lock:
            return {"revision": self._revision, "refresh_workers": self._refresh_workers}

    def snapshot_if_changed(self, previous_revision: int | None = None) -> tuple[int, dict[str, Any] | None]:
        """Copy the published weather state only when a renderer needs a new revision."""
        with self._lock:
            revision = self._revision
            if previous_revision == revision:
                return revision, None
            return revision, copy.deepcopy(self._snapshot)

    def _changed_locked(self) -> None:
        self._revision += 1

    def snapshot_for(self, location_id: str | None) -> dict[str, Any]:
        """Return a normal renderer snapshot with per-location aliases selected."""
        snap = self.snapshot()
        if not location_id:
            return snap
        snap["alerts"] = copy.deepcopy((snap.get("alerts_by_location") or {}).get(location_id) or [])
        snap["storm_guidance"] = copy.deepcopy((snap.get("storm_guidance_by_location") or {}).get(location_id) or {})
        return snap

    def _set_error(self, exc: Exception | None) -> None:
        with self._lock:
            value = str(exc) if exc else None
            if self._snapshot.get("last_error") != value:
                self._snapshot["last_error"] = value
                self._changed_locked()

    def _mark_source(self, name: str, ok: bool, error: str | None = None) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        previous_error: str | None = None
        with self._lock:
            current = copy.deepcopy((self._snapshot.get("sources") or {}).get(name) or {})
            previous_error = current.get("last_error")
            current["last_attempt"] = stamp
            if ok:
                current["last_success"] = stamp
                current["last_error"] = None
            else:
                current["last_error"] = error or "Unknown error"
            self._snapshot.setdefault("sources", {})[name] = current
            self._changed_locked()
        next_error = None if ok else (error or "Unknown error")
        if next_error and next_error != previous_error:
            observability.event("source", f"{name} source unavailable", source=name, state="error", error=next_error[:500])
        elif ok and previous_error:
            observability.event("source", f"{name} source recovered", source=name, state="recovered")

    def _set_location_status(self, location_id: str, section: str, *, ok: bool, error: str | None = None, cached: bool = False) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._lock:
            row = copy.deepcopy((self._snapshot.get("location_status") or {}).get(location_id) or {})
            info = copy.deepcopy(row.get(section) or {})
            info["last_attempt"] = stamp
            info["cached"] = bool(cached)
            if ok:
                info["last_success"] = stamp
                info["last_error"] = None
                info["state"] = "fresh"
            else:
                info["last_error"] = error or "Unknown error"
                info["state"] = "cached" if cached else "unavailable"
            row[section] = info
            self._snapshot.setdefault("location_status", {})[location_id] = row
            self._changed_locked()

    def _run(self) -> None:
        next_weather = 0.0
        next_alert = 0.0
        next_storm = 0.0
        while not self._stop.is_set():
            settings = self.config_store.get()
            now = time.monotonic()
            try:
                if now >= next_weather:
                    self._refresh_weather(settings)
                    next_weather = now + int(settings.get("weather_refresh_seconds", 600))
                if now >= next_alert:
                    self._refresh_alerts(settings)
                    next_alert = now + int(settings.get("alert_refresh_seconds", 60))
                if now >= next_storm:
                    self._refresh_storm_guidance(settings)
                    storm_cfg = settings.get("storm_guidance") or {}
                    next_storm = now + int(storm_cfg.get("refresh_seconds", 900))
                self._set_error(None)
            except Exception as exc:
                self._set_error(exc)
                next_weather = min(next_weather, now + 30) if next_weather else now + 30
                next_alert = min(next_alert, now + 30) if next_alert else now + 30
                next_storm = min(next_storm, now + 30) if next_storm else now + 30

            wait_for = max(1.0, min(next_weather, next_alert, next_storm) - time.monotonic())
            self._wake.wait(timeout=min(wait_for, 30.0))
            if self._wake.is_set():
                self._wake.clear()
                next_weather = 0.0
                next_alert = 0.0
                next_storm = 0.0

    def _refresh_weather(self, settings: dict[str, Any]) -> None:
        locations = settings.get("locations") or []
        if not locations:
            with self._lock:
                self._snapshot["locations"] = {}
                self._snapshot["last_weather_update"] = dt.datetime.now(dt.timezone.utc).isoformat()
                self._changed_locked()
            return

        with self._lock:
            previous_locations = copy.deepcopy(self._snapshot.get("locations") or {})
        new_locations: dict[str, Any] = {}
        errors: list[str] = []
        open_ok = 0
        nws_ok = 0
        history_pending: list[tuple[str, dict[str, Any], None]] = []

        def fetch_location(location: dict[str, Any]):
            lid = location["id"]
            weather_ok = False
            nws_success = False
            weather_error = None
            try:
                data = fetch_forecast(location, self._client)
                weather_ok = True
                self._set_location_status(lid, "weather", ok=True)
            except Exception as exc:
                cached = previous_locations.get(lid)
                weather_error = f"{location.get('postal_code')}: weather: {exc}"
                self._set_location_status(lid, "weather", ok=False, error=str(exc), cached=bool(cached))
                if cached:
                    data = copy.deepcopy(cached)
                    data["stale"] = True
                    data["stale_reason"] = str(exc)
                else:
                    return lid, None, weather_ok, nws_success, weather_error

            # Every ZIP gets an NWS narrative forecast in v0.1.8.1.
            try:
                data["nws"] = fetch_nws_forecast(location, settings.get("nws_user_agent", ""), self._client)
                nws_success = True
                self._set_location_status(lid, "nws_forecast", ok=True)
            except Exception as exc:
                previous_nws = (previous_locations.get(lid) or {}).get("nws") or {}
                if previous_nws.get("periods"):
                    data["nws"] = copy.deepcopy(previous_nws)
                    data["nws"]["error"] = str(exc)
                    data["nws"]["stale"] = True
                else:
                    data["nws"] = {"periods": [], "office": "", "error": str(exc)}
                self._set_location_status(lid, "nws_forecast", ok=False, error=str(exc), cached=bool(previous_nws.get("periods")))
            return lid, data, weather_ok, nws_success, weather_error

        futures = [self._executor.submit(fetch_location, location) for location in locations]
        for future in as_completed(futures):
            lid, data, weather_success, nws_success, weather_error = future.result()
            if weather_error:
                errors.append(weather_error)
            open_ok += int(weather_success)
            nws_ok += int(nws_success)
            if data is None:
                continue
            new_locations[lid] = data
            if self.history_store and (settings.get("history") or {}).get("enabled", True):
                history_pending.append((lid, data.get("current") or {}, None))

        if self.history_store and (settings.get("history") or {}).get("enabled", True):
            try:
                self.history_store.record_many(history_pending)
                for lid, _, _ in history_pending:
                    self._set_location_status(lid, "history", ok=True)
                self.history_store.cleanup_if_due(int((settings.get("history") or {}).get("retention_days", 90)))
                self._mark_source("weather_history", True)
            except Exception as exc:
                for lid, _, _ in history_pending:
                    self._set_location_status(lid, "history", ok=False, error=str(exc))
                self._mark_source("weather_history", False, str(exc))

        self._mark_source("open_meteo", open_ok > 0, None if open_ok else ("; ".join(errors) or "No locations loaded"))
        self._mark_source("nws_forecast", nws_ok > 0, None if nws_ok else "NWS forecast unavailable for all configured ZIPs")
        with self._lock:
            self._snapshot["locations"] = new_locations
            self._snapshot["last_weather_update"] = dt.datetime.now(dt.timezone.utc).isoformat()
            if errors:
                self._snapshot["last_error"] = "; ".join(errors)
            self._changed_locked()

    def _refresh_storm_guidance(self, settings: dict[str, Any]) -> None:
        cfg = settings.get("storm_guidance") or {}
        locations = settings.get("locations") or []
        primary_id = settings.get("primary_location_id")
        if not cfg.get("enabled", True) or not locations:
            with self._lock:
                self._snapshot["storm_guidance"] = {}
                self._snapshot["storm_guidance_by_location"] = {}
                self._changed_locked()
            return

        with self._lock:
            previous = copy.deepcopy(self._snapshot.get("storm_guidance_by_location") or {})
        result: dict[str, Any] = {}
        errors: list[str] = []
        successes = 0
        def fetch_location(location: dict[str, Any]):
            lid = location["id"]
            try:
                storm = fetch_storm_guidance(location, self._client)
                self._set_location_status(lid, "storm_guidance", ok=True)
                return lid, storm, None
            except Exception as exc:
                cached = previous.get(lid)
                if cached:
                    storm = copy.deepcopy(cached)
                    storm["error"] = str(exc)
                    storm["stale"] = True
                else:
                    storm = None
                self._set_location_status(lid, "storm_guidance", ok=False, error=str(exc), cached=bool(cached))
                return lid, storm, f"{location.get('postal_code')}: {exc}"
        futures = [self._executor.submit(fetch_location, location) for location in locations]
        for future in as_completed(futures):
            lid, storm, error = future.result()
            if storm is not None:
                result[lid] = storm
            if error:
                errors.append(error)
            else:
                successes += 1
        with self._lock:
            self._snapshot["storm_guidance_by_location"] = result
            self._snapshot["storm_guidance"] = copy.deepcopy(result.get(primary_id) or {})
            self._changed_locked()
        self._mark_source("storm_guidance", successes > 0, None if successes else ("; ".join(errors) or "No storm guidance"))

    def _refresh_alerts(self, settings: dict[str, Any]) -> None:
        locations = settings.get("locations") or []
        primary_id = settings.get("primary_location_id")
        if not locations:
            with self._lock:
                self._snapshot["alerts"] = []
                self._snapshot["alerts_by_location"] = {}
                self._snapshot["last_alert_update"] = dt.datetime.now(dt.timezone.utc).isoformat()
                self._changed_locked()
            return

        with self._lock:
            previous = copy.deepcopy(self._snapshot.get("alerts_by_location") or {})
        result: dict[str, list[dict[str, Any]]] = {}
        errors: list[str] = []
        successes = 0
        def fetch_location(location: dict[str, Any]):
            lid = location["id"]
            try:
                alerts = fetch_alerts(location, settings.get("nws_user_agent", ""), self._client)
                self._set_location_status(lid, "alerts", ok=True)
                return lid, alerts, None
            except Exception as exc:
                cached = previous.get(lid)
                alerts = copy.deepcopy(cached) if cached is not None else None
                self._set_location_status(lid, "alerts", ok=False, error=str(exc), cached=cached is not None)
                return lid, alerts, f"{location.get('postal_code')}: {exc}"
        futures = [self._executor.submit(fetch_location, location) for location in locations]
        for future in as_completed(futures):
            lid, alerts, error = future.result()
            if alerts is not None:
                result[lid] = alerts
            if error:
                errors.append(error)
            else:
                successes += 1

        locations_by_id = {str(location.get("id")): location for location in locations}
        for lid, alerts in result.items():
            old_ids = {str(row.get("id") or row.get("identifier") or "") for row in previous.get(lid, [])}
            location = locations_by_id.get(str(lid)) or {}
            for alert in alerts:
                severity = str(alert.get("severity") or "Unknown")
                alert_id = str(alert.get("id") or alert.get("identifier") or "")
                if alert_id and alert_id not in old_ids and severity in {"Severe", "Extreme"}:
                    observability.event(
                        "severe",
                        str(alert.get("event") or "Severe weather alert"),
                        alert_id=alert_id,
                        severity=severity,
                        location_id=lid,
                        postal_code=location.get("postal_code"),
                    )

        with self._lock:
            self._snapshot["alerts_by_location"] = result
            self._snapshot["alerts"] = copy.deepcopy(result.get(primary_id) or [])
            self._snapshot["last_alert_update"] = dt.datetime.now(dt.timezone.utc).isoformat()
            self._changed_locked()
        self._mark_source("nws_alerts", successes > 0, None if successes else ("; ".join(errors) or "No alert data"))
