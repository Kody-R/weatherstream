from __future__ import annotations

import datetime as dt
from typing import Any


AVAILABLE_SLIDES = [
    "station_id", "current", "condition_focus", "today", "nws_forecast", "temperature_trend", "hourly",
    "precipitation", "storm_outlook", "spc_outlook", "radar_local", "radar_regional", "radar_wide",
    "map_engine", "map_satellite", "map_lightning", "seven_day", "regional_map", "weather_history", "almanac",
    "alert", "alert_radar", "event_summary", "tropical_update", "tropical_systems", "tropical_track", "tropical_local",
]


def _minutes(value: str) -> int | None:
    try:
        hour, minute = (int(x) for x in value.split(":", 1))
        return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else None
    except Exception:
        return None


def active_sequence(settings: dict[str, Any], region_id: str, channel_mode: str, now: dt.datetime) -> list[str] | None:
    studio = settings.get("studio") or {}
    if not studio.get("enabled", True):
        return None
    current = now.hour * 60 + now.minute
    weekday = now.weekday()
    for row in studio.get("schedules") or []:
        if not isinstance(row, dict) or not row.get("enabled", True):
            continue
        if row.get("region_id") not in {None, "", "*", region_id} or row.get("channel_mode") not in {None, "", "*", channel_mode}:
            continue
        days = row.get("days") or list(range(7))
        if weekday not in days:
            continue
        start, end = _minutes(str(row.get("start") or "00:00")), _minutes(str(row.get("end") or "23:59"))
        if start is None or end is None:
            continue
        inside = start <= current <= end if start <= end else current >= start or current <= end
        sequence = [str(x) for x in (row.get("sequence") or []) if str(x) in AVAILABLE_SLIDES or str(x).startswith("bumper:")]
        if inside and sequence:
            return sequence
    sequences = studio.get("sequences") if isinstance(studio.get("sequences"), dict) else {}
    sequence = sequences.get(channel_mode)
    return [str(x) for x in sequence if str(x) in AVAILABLE_SLIDES or str(x).startswith("bumper:")] if isinstance(sequence, list) and sequence else None


def bumper(settings: dict[str, Any], bumper_id: str) -> dict[str, Any] | None:
    for row in (settings.get("studio") or {}).get("bumpers") or []:
        if isinstance(row, dict) and str(row.get("id")) == bumper_id:
            return row
    return None

