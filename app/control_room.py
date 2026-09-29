from __future__ import annotations

import datetime as dt
import time
from typing import Any

SOURCE_LABELS = {
    "nws_observations": "NWS Observations",
    "open_meteo": "Open-Meteo Forecast",
    "weather": "Weather Data",
    "nws_forecast": "NWS Forecast",
    "alerts": "NWS Alerts",
    "nws_alerts": "NWS Alerts",
    "storm_guidance": "Storm Guidance",
    "spc": "SPC Outlooks",
    "radar": "Radar",
    "nhc_tropical": "NHC Tropical",
    "satellite": "GOES Satellite",
    "lightning": "GLM Lightning",
    "usgs_rivers": "USGS Rivers",
    "air_quality": "Air Quality",
    "climate_normals": "Climate Normals",
    "geonames": "Map Places",
    "weather_history": "Local History",
}

# Age at which a successful fetch should be presented as stale in Studio.
STALE_AFTER_SECONDS = {
    "nws_observations": 45 * 60,
    "open_meteo": 30 * 60,
    "weather": 30 * 60,
    "nws_forecast": 2 * 60 * 60,
    "alerts": 8 * 60,
    "nws_alerts": 8 * 60,
    "storm_guidance": 45 * 60,
    "spc": 60 * 60,
    "radar": 20 * 60,
    "nhc_tropical": 45 * 60,
    "satellite": 30 * 60,
    "lightning": 20 * 60,
    "usgs_rivers": 45 * 60,
    "air_quality": 90 * 60,
    "climate_normals": 7 * 24 * 60 * 60,
    "geonames": 24 * 60 * 60,
    "weather_history": 45 * 60,
}


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _age_text(age: float | None) -> str:
    if age is None:
        return "never"
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age // 60)}m ago"
    if age < 86400:
        return f"{int(age // 3600)}h ago"
    return f"{int(age // 86400)}d ago"


def source_health(sources: dict[str, Any], now: float | None = None) -> list[dict[str, Any]]:
    """Normalize heterogeneous source telemetry into Studio health rows."""
    now = float(now or time.time())
    rows: list[dict[str, Any]] = []
    for key, raw in (sources or {}).items():
        row = raw if isinstance(raw, dict) else {}
        enabled = row.get("enabled", True) is not False
        stamp = _timestamp(row.get("last_success") or row.get("last_update"))
        age = max(0.0, now - stamp) if stamp is not None else None
        error = str(row.get("last_error") or "").strip()
        upstream_state = str(row.get("state") or "").lower()
        stale_after = int(STALE_AFTER_SECONDS.get(str(key), 2 * 60 * 60))
        if not enabled:
            state = "DISABLED"
        elif error and stamp is None:
            state = "ERROR"
        elif error:
            state = "CACHED" if age is not None and age <= stale_after * 2 else "ERROR"
        elif age is None:
            state = "WAITING"
        elif age > stale_after:
            state = "STALE"
        elif upstream_state in {"cached", "fallback"}:
            state = "CACHED"
        else:
            state = "HEALTHY"
        rows.append({
            "id": str(key),
            "label": SOURCE_LABELS.get(str(key), str(key).replace("_", " ").title()),
            "state": state,
            "age_seconds": round(age, 1) if age is not None else None,
            "age_text": _age_text(age),
            "last_success": row.get("last_success") or row.get("last_update"),
            "last_error": error or None,
            "available": row.get("available"),
        })
    order = {"ERROR": 0, "STALE": 1, "CACHED": 2, "WAITING": 3, "HEALTHY": 4, "DISABLED": 5}
    rows.sort(key=lambda x: (order.get(x["state"], 9), x["label"]))
    return rows
