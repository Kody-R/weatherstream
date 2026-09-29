from __future__ import annotations

import datetime as dt
import math
from typing import Any

import httpx

AIR_QUALITY_API = "https://air-quality-api.open-meteo.com/v1/air-quality"
USGS_LATEST = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/latest-continuous/items"
USGS_CONTINUOUS = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/continuous/items"
NCEI_ACCESS = "https://www.ncei.noaa.gov/access/services/data/v1"


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _aqi_category(aqi: float | None) -> str:
    if aqi is None:
        return "UNKNOWN"
    if aqi <= 50:
        return "GOOD"
    if aqi <= 100:
        return "MODERATE"
    if aqi <= 150:
        return "UNHEALTHY FOR SENSITIVE GROUPS"
    if aqi <= 200:
        return "UNHEALTHY"
    if aqi <= 300:
        return "VERY UNHEALTHY"
    return "HAZARDOUS"


def fetch_air_quality(location: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch key U.S. AQI fields without requiring a user API key.

    Open-Meteo's air-quality endpoint is used as the zero-configuration provider.
    The screen labels it as modeled air-quality guidance, not an EPA monitor reading.
    """
    owned = client is None
    client = client or httpx.Client(timeout=15.0, follow_redirects=True)
    try:
        response = client.get(
            AIR_QUALITY_API,
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "timezone": "auto",
                "current": "us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide",
                "hourly": "us_aqi,pm2_5,pm10",
                "forecast_days": 2,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
    finally:
        if owned:
            client.close()
    cur = data.get("current") or {}
    aqi = _float(cur.get("us_aqi"))
    pm25 = _float(cur.get("pm2_5"))
    pm10 = _float(cur.get("pm10"))
    ozone = _float(cur.get("ozone"))
    no2 = _float(cur.get("nitrogen_dioxide"))
    pollutant = "PM2.5"
    candidates = [(pm25 or -1, "PM2.5"), (pm10 or -1, "PM10"), (ozone or -1, "OZONE"), (no2 or -1, "NO2")]
    if any(v >= 0 for v, _ in candidates):
        pollutant = max(candidates)[1]
    return {
        "aqi": aqi,
        "category": _aqi_category(aqi),
        "pm2_5": pm25,
        "pm10": pm10,
        "ozone": ozone,
        "nitrogen_dioxide": no2,
        "primary_pollutant": pollutant,
        "hourly": data.get("hourly") or {},
        "source": "Open-Meteo / CAMS",
        "kind": "modeled_guidance",
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _bbox(lat: float, lon: float, radius_miles: float) -> str:
    lat_delta = radius_miles / 69.0
    lon_delta = radius_miles / max(10.0, 69.0 * math.cos(math.radians(lat)))
    return f"{lon-lon_delta:.5f},{lat-lat_delta:.5f},{lon+lon_delta:.5f},{lat+lat_delta:.5f}"


def _features(payload: Any) -> list[dict[str, Any]]:
    return list((payload or {}).get("features") or []) if isinstance(payload, dict) else []


def fetch_nearby_rivers(location: dict[str, Any], radius_miles: int = 60, max_gauges: int = 3, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch nearby USGS gage-height/streamflow observations from the modern OGC API."""
    lat, lon = float(location["latitude"]), float(location["longitude"])
    owned = client is None
    client = client or httpx.Client(timeout=18.0, follow_redirects=True)
    try:
        common = {"f": "json", "bbox": _bbox(lat, lon, radius_miles), "limit": 100}
        gage_resp = client.get(USGS_LATEST, params={**common, "parameter_code": "00065"}, timeout=18.0)
        gage_resp.raise_for_status()
        flow_resp = client.get(USGS_LATEST, params={**common, "parameter_code": "00060"}, timeout=18.0)
        flow_resp.raise_for_status()
        gages = _features(gage_resp.json())
        flows = _features(flow_resp.json())
    finally:
        if owned:
            client.close()

    flow_by_id: dict[str, dict[str, Any]] = {}
    for feature in flows:
        props = feature.get("properties") or {}
        mid = str(props.get("monitoring_location_id") or "")
        if mid:
            flow_by_id[mid] = props

    rows: list[dict[str, Any]] = []
    for feature in gages:
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        glon, glat = _float(coords[0]), _float(coords[1])
        if glat is None or glon is None:
            continue
        distance = _haversine_miles(lat, lon, glat, glon)
        mid = str(props.get("monitoring_location_id") or "")
        flow = flow_by_id.get(mid) or {}
        rows.append({
            "id": mid,
            "number": props.get("monitoring_location_number") or mid.replace("USGS-", ""),
            "name": props.get("monitoring_location_name") or "USGS Gauge",
            "gage_height_ft": _float(props.get("value")),
            "gage_height_time": props.get("time"),
            "streamflow_cfs": _float(flow.get("value")),
            "streamflow_time": flow.get("time"),
            "distance_miles": distance,
            "latitude": glat,
            "longitude": glon,
            "provisional": str(props.get("approval_status") or "").lower() != "approved",
        })
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("id") or row.get("number") or "")
        if not key:
            continue
        previous = unique.get(key)
        if previous is None or str(row.get("gage_height_time") or "") > str(previous.get("gage_height_time") or ""):
            unique[key] = row
    rows = sorted(unique.values(), key=lambda x: x["distance_miles"])
    rows = rows[: max(1, min(6, int(max_gauges)))]
    return {
        "gauges": rows,
        "radius_miles": radius_miles,
        "source": "USGS Water Data",
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def fetch_gauge_history(gauge_id: str, hours: int = 6, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    if not gauge_id:
        return []
    owned = client is None
    client = client or httpx.Client(timeout=18.0, follow_redirects=True)
    try:
        response = client.get(
            USGS_CONTINUOUS,
            params={
                "f": "json",
                "monitoring_location_id": gauge_id,
                "parameter_code": "00065",
                "datetime": f"PT{max(1, min(48, int(hours)))}H",
                "limit": 500,
            },
            timeout=18.0,
        )
        response.raise_for_status()
        rows = []
        for feature in _features(response.json()):
            props = feature.get("properties") or {}
            value = _float(props.get("value"))
            if value is not None:
                rows.append({"time": props.get("time"), "value": value})
        rows.sort(key=lambda x: str(x.get("time") or ""))
        return rows
    finally:
        if owned:
            client.close()


def fetch_ncei_daily_normals(station_id: str, day: dt.date | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch official 1991-2020 daily normals for a configured NCEI station.

    NCEI station identifiers are not the same as ICAO/NWS station identifiers,
    so v0.3.5 keeps this explicit rather than guessing a climatology station.
    """
    station_id = str(station_id or "").strip().upper()
    if station_id.startswith("GHCND:"):
        station_id = station_id.split(":", 1)[1]
    if not station_id:
        return {}
    day = day or dt.date.today()
    # Daily normals are climatological by calendar day; NCEI accepts dates from
    # the normals period. 2020 safely includes leap day if needed.
    query_date = dt.date(2020, day.month, min(day.day, 29 if day.month == 2 else day.day)).isoformat()
    owned = client is None
    client = client or httpx.Client(timeout=18.0, follow_redirects=True)
    try:
        response = client.get(
            NCEI_ACCESS,
            params={
                "dataset": "normals-daily-1991-2020",
                "stations": station_id,
                "startDate": query_date,
                "endDate": query_date,
                "format": "json",
                "units": "standard",
                "includeAttributes": "false",
                "dataTypes": "DLY-TMAX-NORMAL,DLY-TMIN-NORMAL,DLY-TAVG-NORMAL,MTD-PRCP-NORMAL",
            },
            timeout=18.0,
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owned:
            client.close()
    row = payload[0] if isinstance(payload, list) and payload else payload if isinstance(payload, dict) else {}
    def first(*keys: str) -> float | None:
        for key in keys:
            if key in row:
                val = _float(row.get(key))
                if val is not None:
                    return val
        return None
    return {
        "station_id": station_id,
        "station_name": row.get("NAME") or row.get("station_name") or row.get("STATION") or station_id,
        "normal_high_f": first("DLY-TMAX-NORMAL", "DLY_TMAX_NORMAL"),
        "normal_low_f": first("DLY-TMIN-NORMAL", "DLY_TMIN_NORMAL"),
        "normal_mean_f": first("DLY-TAVG-NORMAL", "DLY_TAVG_NORMAL"),
        "normal_mtd_precip_in": first("MTD-PRCP-NORMAL", "MTD_PRCP_NORMAL"),
        "source": "NOAA NCEI 1991-2020 Normals",
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
