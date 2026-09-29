from __future__ import annotations

from typing import Any


EVENT_IDENTITIES: dict[str, dict[str, Any]] = {
    "severe": {
        "name": "Severe Weather",
        "desk": "SEVERE WEATHER CENTER",
        "slug": "ALERTS • RADAR • STORM THREATS",
        "bg": "#130b10", "panel": "#311219", "panel2": "#1d0d13",
        "title": "#ffffff", "text": "#fff7f7", "accent": "#ff313d", "accent2": "#ff9d1f",
        "muted": "#e3a7aa", "ticker": "#16080d", "alert": "#ff313d", "motif": "warning",
    },
    "flood": {
        "name": "Flood",
        "desk": "FLOOD & HYDROLOGY DESK",
        "slug": "RAINFALL • RIVERS • FLOOD THREATS",
        "bg": "#031b2a", "panel": "#0a4256", "panel2": "#062b3d",
        "title": "#ffffff", "text": "#f2fcff", "accent": "#22c8ef", "accent2": "#42e6ae",
        "muted": "#9ad9e6", "ticker": "#021723", "alert": "#28d17c", "motif": "water",
    },
    "winter": {
        "name": "Winter Weather",
        "desk": "WINTER WEATHER DESK",
        "slug": "SNOW • ICE • COLD IMPACTS",
        "bg": "#0a1730", "panel": "#173d77", "panel2": "#102958",
        "title": "#ffffff", "text": "#f7fbff", "accent": "#77d8ff", "accent2": "#c9f2ff",
        "muted": "#b7d8f5", "ticker": "#07152d", "alert": "#8b7dff", "motif": "ice",
    },
    "heat": {
        "name": "Extreme Heat",
        "desk": "HEAT WEATHER DESK",
        "slug": "HEAT INDEX • TEMPERATURE • SAFETY",
        "bg": "#2a0d07", "panel": "#6f1d0b", "panel2": "#43130b",
        "title": "#fff8ed", "text": "#fff9f2", "accent": "#ff9f1c", "accent2": "#ff3f1f",
        "muted": "#f3bd91", "ticker": "#251008", "alert": "#ff4d25", "motif": "heat",
    },
    "wildfire": {
        "name": "Wildfire & Smoke",
        "desk": "FIRE WEATHER DESK",
        "slug": "FIRE RISK • WIND • AIR QUALITY",
        "bg": "#1e1308", "panel": "#573211", "panel2": "#362108",
        "title": "#fff8ec", "text": "#fffaf3", "accent": "#ff9a1f", "accent2": "#d85c17",
        "muted": "#dfbd87", "ticker": "#1b1107", "alert": "#ff6f1f", "motif": "smoke",
    },
    "tropical": {
        "name": "Tropical Weather",
        "desk": "RWN TROPICS WATCH",
        "slug": "NHC TRACKS • SATELLITE • LOCAL IMPACTS",
        "bg": "#041a38", "panel": "#0b4d86", "panel2": "#082e60",
        "title": "#ffffff", "text": "#f4fbff", "accent": "#21c7ff", "accent2": "#5df0cf",
        "muted": "#a7d9f2", "ticker": "#03152d", "alert": "#d61f2c", "motif": "ocean",
    },
}

MODE_TO_IDENTITY = {
    "severe": "severe",
    "event_tornado": "severe",
    "event_flood": "flood",
    "event_winter": "winter",
    "event_heat": "heat",
    "event_wildfire": "wildfire",
    "tropics": "tropical",
}

STORY_TO_IDENTITY = {
    "severe": "severe", "storms": "severe",
    "flood": "flood", "winter": "winter", "cold": "winter",
    "heat": "heat", "wildfire": "wildfire", "air_quality": "wildfire",
    "tropical": "tropical",
}


def identity_key(settings: dict[str, Any]) -> str | None:
    """Return the configured dedicated-desk identity for this render context.

    v0.3.9 intentionally themes specialty channels, not ordinary local channels. A
    manual local Story Engine classification therefore does not silently recolor the
    user's everyday RWN service. Preview/test callers can set ``_event_identity``.
    """
    cfg = settings.get("event_identity") or {}
    if not cfg.get("enabled", True):
        return None
    explicit = str(settings.get("_event_identity") or "").strip().lower()
    key = explicit if explicit in EVENT_IDENTITIES else MODE_TO_IDENTITY.get(str(settings.get("_channel_mode") or "local"))
    if not key:
        return None
    if not (cfg.get("desks") or {}).get(key, True):
        return None
    return key


def identity(settings: dict[str, Any]) -> dict[str, Any] | None:
    key = identity_key(settings)
    if not key:
        return None
    return {"key": key, **EVENT_IDENTITIES[key]}


def apply_identity_colors(base: dict[str, str], settings: dict[str, Any]) -> dict[str, str]:
    row = identity(settings)
    if not row:
        return base
    out = dict(base)
    for key in ("bg", "panel", "panel2", "title", "text", "accent", "muted", "ticker", "alert"):
        if row.get(key):
            out[key] = str(row[key])
    out["accent2"] = str(row.get("accent2") or out.get("accent"))
    out["event_key"] = str(row["key"])
    out["event_desk"] = str(row["desk"])
    out["event_slug"] = str(row["slug"])
    out["event_motif"] = str(row["motif"])
    out["event_desk_enabled"] = bool((settings.get("event_identity") or {}).get("desk_bug", True))
    out["style"] = "event"
    return out
