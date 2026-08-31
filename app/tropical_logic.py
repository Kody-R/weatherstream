from __future__ import annotations

import datetime as dt
import html
import math
import re
from typing import Any


CLASSIFICATION_NAMES = {
    "TD": "Tropical Depression", "SD": "Subtropical Depression", "STD": "Subtropical Depression",
    "TS": "Tropical Storm", "SS": "Subtropical Storm", "STS": "Subtropical Storm",
    "HU": "Hurricane", "PC": "Potential Tropical Cyclone", "PTC": "Potential Tropical Cyclone",
    "EX": "Post-Tropical Cyclone", "LO": "Low", "DB": "Disturbance", "WV": "Tropical Wave", "TY": "Typhoon",
}
TROPICAL_ALERT_TERMS = (
    "tropical storm watch", "tropical storm warning", "hurricane watch",
    "hurricane warning", "storm surge watch", "storm surge warning",
    "hurricane local statement", "extreme wind warning",
)


def parse_coordinate(value: Any, *, longitude: bool = False) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().upper()
    if not text:
        return None
    sign = -1.0 if text.endswith(("S", "W")) else 1.0
    try:
        result = float(text.rstrip("NSEW")) * sign
    except ValueError:
        return None
    if longitude and not -180 <= result <= 180:
        return None
    if not longitude and not -90 <= result <= 90:
        return None
    return result


def normalize_storm(raw: dict[str, Any]) -> dict[str, Any] | None:
    storm_id = str(raw.get("id") or "").lower()
    if not storm_id.startswith("al"):
        return None
    lat = parse_coordinate(raw.get("latitude_numeric", raw.get("latitude")))
    lon = parse_coordinate(raw.get("longitude_numeric", raw.get("longitude")), longitude=True)
    classification = str(raw.get("classification") or "").upper()
    intensity = raw.get("intensity")
    try: intensity = int(float(intensity))
    except (TypeError, ValueError): intensity = None
    track = []
    for point in raw.get("track") or []:
        if not isinstance(point, (list, tuple)) or len(point) < 2: continue
        try: plat, plon = float(point[0]), float(point[1])
        except (TypeError, ValueError): continue
        if -90 <= plat <= 90 and -180 <= plon <= 180: track.append([plat, plon])
    return {
        "id": storm_id,
        "name": str(raw.get("name") or storm_id).title(),
        "classification": classification,
        "classification_name": CLASSIFICATION_NAMES.get(classification, classification or "Tropical Cyclone"),
        "intensity_kt": intensity,
        "intensity_mph": round(intensity * 1.15078) if intensity is not None else None,
        "pressure_mb": raw.get("pressure"),
        "latitude": lat,
        "longitude": lon,
        "movement_degrees": raw.get("movementDir"),
        "movement_mph": raw.get("movementSpeed"),
        "last_update": raw.get("lastUpdate"),
        "public_advisory": dict(raw.get("publicAdvisory") or {}),
        "forecast_track": dict(raw.get("forecastTrack") or {}),
        "track": track,
    }


def clean_outlook_text(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:5000]


def outlook_development_max(value: str) -> int:
    values = [int(item) for item in re.findall(r"(?:formation chance|chance)[^%]{0,80}?(\d{1,3})\s*percent", value, flags=re.I)]
    return max([x for x in values if 0 <= x <= 100] or [0])


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def in_gulf_region(lat: float, lon: float) -> bool:
    # Operational envelope for Gulf-focused programming. It deliberately includes
    # the Florida Straits and Yucatan Channel, where Gulf impacts often first enter.
    return 18.0 <= lat <= 31.8 and -98.8 <= lon <= -79.0


def hurricane_season_active(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now.month, now.day) >= (6, 1) and (now.month, now.day) <= (11, 30)


def tropical_alerts(alerts: list[dict[str, Any]] | None) -> list[str]:
    events = []
    for alert in alerts or []:
        event = str(alert.get("event") or "")
        if any(term in event.lower() for term in TROPICAL_ALERT_TERMS):
            events.append(event)
    return events


def evaluate_activation(snapshot: dict[str, Any], location: dict[str, Any] | None, alerts: list[dict[str, Any]] | None, cfg: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    local_alerts = tropical_alerts(alerts)
    if local_alerts:
        reasons.append(f"Official local alert: {local_alerts[0]}")
    radius = max(100, min(2000, int(cfg.get("activation_radius_miles", 750))))
    try:
        local_lat = float((location or {}).get("latitude")); local_lon = float((location or {}).get("longitude"))
    except (TypeError, ValueError):
        local_lat = local_lon = None
    nearest: dict[str, Any] | None = None
    for storm in snapshot.get("systems") or []:
        points = []
        if storm.get("latitude") is not None and storm.get("longitude") is not None:
            points.append((float(storm["latitude"]), float(storm["longitude"])))
        points.extend((float(x[0]), float(x[1])) for x in (storm.get("track") or []) if len(x) >= 2)
        if any(in_gulf_region(lat, lon) for lat, lon in points):
            reasons.append(f"{storm.get('name')} is in or forecast into the Gulf region")
        if local_lat is not None and points:
            distance = min(haversine_miles(local_lat, local_lon, lat, lon) for lat, lon in points)
            if nearest is None or distance < nearest["distance_miles"]:
                nearest = {"storm_id": storm.get("id"), "name": storm.get("name"), "distance_miles": round(distance)}
            if distance <= radius:
                reasons.append(f"{storm.get('name')} is within {round(distance)} miles of the primary location")
    outlook = snapshot.get("outlook") or {}
    threshold = max(0, min(100, int(cfg.get("gulf_development_threshold", 40))))
    if outlook.get("gulf_mentioned") and int(outlook.get("development_max") or 0) >= threshold:
        reasons.append(f"Gulf disturbance development chance is at least {threshold}%")
    return {"triggered": bool(reasons), "reasons": list(dict.fromkeys(reasons)), "nearest": nearest, "local_alerts": local_alerts}
