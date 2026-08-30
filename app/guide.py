from __future__ import annotations

import datetime as dt
from html import escape
from zoneinfo import ZoneInfo
from typing import Any


def _xmltv_time(value: dt.datetime) -> str:
    return value.strftime("%Y%m%d%H%M%S %z")


def _tz(settings: dict[str, Any]) -> dt.tzinfo:
    pid = settings.get("primary_location_id")
    loc = next((x for x in settings.get("locations", []) if x.get("id") == pid), None)
    name = (loc or {}).get("timezone")
    try:
        return ZoneInfo(name) if name and name != "auto" else dt.datetime.now().astimezone().tzinfo
    except Exception:
        return dt.datetime.now().astimezone().tzinfo


def _daypart_title(hour: int, station: str) -> tuple[str, str]:
    if 5 <= hour < 10: return (f"{station} Morning Weather", "Current conditions, today's forecast, radar and the morning outlook.")
    if 10 <= hour < 17: return (f"{station} Daytime Weather", "Local conditions, radar, forecast trends and regional weather.")
    if 17 <= hour < 22: return (f"{station} Evening Weather", "Evening conditions, tonight's forecast, radar and the extended outlook.")
    return (f"{station} Overnight Weather", "Overnight conditions, tomorrow's forecast, radar and weather information.")


def generate_xmltv(settings: dict[str, Any], severe_active: bool = False, hours: int = 24) -> str:
    tz = _tz(settings)
    now = dt.datetime.now(tz).replace(second=0, microsecond=0)
    start = now.replace(minute=0)
    end = start + dt.timedelta(hours=max(6, min(72, hours)))
    station = settings.get("station_name") or "Roller Weather Network"
    channel_id = "rwn.local"
    scheduled = ((settings.get("presentation") or {}).get("scheduled_updates") or {})
    marks = sorted({int(x) for x in scheduled.get("minute_marks", [8,18,28,38,48,58]) if 0 <= int(x) <= 59})
    window = max(30, min(300, int(scheduled.get("window_seconds", 120))))
    enabled = bool(scheduled.get("enabled", False))

    events: list[tuple[dt.datetime, dt.datetime, str, str]] = []
    cursor = start
    while cursor < end:
        hour_end = min(end, cursor.replace(minute=0) + dt.timedelta(hours=1))
        cuts = [(cursor, None)]
        if enabled:
            for mark in marks:
                u0 = cursor.replace(minute=mark, second=0)
                if cursor <= u0 < hour_end:
                    u1 = min(hour_end, u0 + dt.timedelta(seconds=window))
                    cuts.append((u0, "local")); cuts.append((u1, None))
        cuts.append((hour_end, None)); cuts.sort(key=lambda x: x[0])
        for i in range(len(cuts)-1):
            a, tag = cuts[i]; b = cuts[i+1][0]
            if b <= a: continue
            if severe_active and a <= now < b:
                title = "RWN Severe Weather Coverage"; desc = "Continuous local severe-weather coverage, alerts and radar from Roller Weather Network."
            elif tag == "local":
                title = "RWN Local Weather Update"; desc = "Current conditions, local radar and the latest Roller Weather Network forecast."
            else:
                title, desc = _daypart_title(a.hour, station)
            events.append((a,b,title,desc))
        cursor = hour_end

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="WeatherStream 0.1.7.1">']
    lines += [f'  <channel id="{channel_id}">', f'    <display-name>{escape(station)}</display-name>', '  </channel>']
    for a,b,title,desc in events:
        lines += [f'  <programme start="{_xmltv_time(a)}" stop="{_xmltv_time(b)}" channel="{channel_id}">', f'    <title>{escape(title)}</title>', f'    <desc>{escape(desc)}</desc>', '    <category>Weather</category>', '  </programme>']
    lines.append('</tv>')
    return "\n".join(lines) + "\n"
