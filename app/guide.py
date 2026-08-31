from __future__ import annotations

import datetime as dt
from html import escape
from typing import Any
from zoneinfo import ZoneInfo


def _xmltv_time(value: dt.datetime) -> str:
    return value.strftime("%Y%m%d%H%M%S %z")


def _loc_tz(location: dict[str, Any] | None) -> dt.tzinfo:
    name = (location or {}).get("timezone")
    try:
        return ZoneInfo(name) if name and name != "auto" else dt.datetime.now().astimezone().tzinfo
    except Exception:
        return dt.datetime.now().astimezone().tzinfo


def _daypart_title(hour: int, station: str, city: str) -> tuple[str, str]:
    prefix = f"{station} - {city}" if city else station
    if 5 <= hour < 10: return (f"{prefix} Morning Weather", "Current conditions, today's forecast and the morning outlook.")
    if 10 <= hour < 17: return (f"{prefix} Daytime Weather", "Local conditions, forecast trends and weather intelligence.")
    if 17 <= hour < 22: return (f"{prefix} Evening Weather", "Evening conditions, tonight's forecast and the extended outlook.")
    return (f"{prefix} Overnight Weather", "Overnight conditions, tomorrow's forecast and local weather information.")


def _local_events(settings: dict[str, Any], location: dict[str, Any], severe: bool, hours: int) -> list[tuple[dt.datetime,dt.datetime,str,str]]:
    tz=_loc_tz(location); now=dt.datetime.now(tz).replace(second=0,microsecond=0); start=now.replace(minute=0); end=start+dt.timedelta(hours=max(6,min(72,hours)))
    station=settings.get("station_callsign") or "RWN"; city=location.get("name") or location.get("postal_code") or "Local"
    scheduled=((settings.get("presentation") or {}).get("scheduled_updates") or {})
    marks=sorted({int(x) for x in scheduled.get("minute_marks",[8,18,28,38,48,58]) if 0<=int(x)<=59}); window=max(30,min(300,int(scheduled.get("window_seconds",120)))); enabled=bool(scheduled.get("enabled",False))
    events=[]; cursor=start
    while cursor<end:
        hour_end=min(end,cursor.replace(minute=0)+dt.timedelta(hours=1)); cuts=[(cursor,None)]
        if enabled:
            for mark in marks:
                u0=cursor.replace(minute=mark,second=0)
                if cursor<=u0<hour_end:
                    u1=min(hour_end,u0+dt.timedelta(seconds=window)); cuts.append((u0,"local")); cuts.append((u1,None))
        cuts.append((hour_end,None)); cuts.sort(key=lambda x:x[0])
        for i in range(len(cuts)-1):
            a,tag=cuts[i]; b=cuts[i+1][0]
            if b<=a: continue
            if severe and a<=now<b:
                title=f"{station} Severe Weather Coverage - {city}"; desc=f"Severe-weather alerts and local conditions for {city}."
            elif tag=="local":
                title=f"{station} Local Weather Update - {city}"; desc=f"Current conditions and the latest local forecast for {city}."
            else: title,desc=_daypart_title(a.hour,station,city)
            events.append((a,b,title,desc))
        cursor=hour_end
    return events


def channel_specs(settings: dict[str, Any]) -> list[dict[str, Any]]:
    cfg=settings.get("channels") or {}; locations=list(settings.get("locations") or []); primary_id=settings.get("primary_location_id"); raw=[]
    if cfg.get("per_zip_enabled",True):
        for loc in locations[:max(1,min(24,int(cfg.get("max_zip_channels",12))))]:
            postal=str(loc.get("postal_code") or loc.get("id") or "local")
            raw.append({"id":f"rwn.zip.{postal}","key":f"zip-{postal}","name":f"RWN Local - {loc.get('name') or postal}","mode":"local","location":loc})
    primary=next((x for x in locations if x.get("id")==primary_id),None)
    if primary and cfg.get("radar_enabled",True): raw.append({"id":"rwn.radar","key":"radar","name":"RWN Radar","mode":"radar","location":primary})
    if primary and cfg.get("severe_enabled",True): raw.append({"id":"rwn.severe","key":"severe","name":"RWN Severe Weather","mode":"severe","location":primary})

    lineup = cfg.get("lineup") if isinstance(cfg.get("lineup"), list) else []
    meta = {str(x.get("key")): x for x in lineup if isinstance(x, dict) and x.get("key")}
    specs=[]
    for idx,spec in enumerate(raw):
        row=meta.get(spec["key"], {})
        if row and not row.get("enabled", True):
            continue
        spec=dict(spec)
        spec["name"] = str(row.get("name") or spec["name"])
        spec["number"] = int(row.get("number") or (201+idx))
        spec["override"] = (cfg.get("overrides") or {}).get(spec["key"], {}) if isinstance(cfg.get("overrides"), dict) else {}
        specs.append(spec)
    specs.sort(key=lambda x:(int(x.get("number") or 9999), x["key"]))
    return specs


def generate_xmltv(settings: dict[str, Any], severe_by_location: dict[str,bool] | None = None, hours: int = 24) -> str:
    severe_by_location=severe_by_location or {}; specs=channel_specs(settings)
    lines=['<?xml version="1.0" encoding="UTF-8"?>','<tv generator-info-name="WeatherStream 0.2.5">']
    for spec in specs:
        lines += [f'  <channel id="{escape(spec["id"])}">',f'    <display-name>{escape(spec["name"])}</display-name>','  </channel>']
    for spec in specs:
        loc=spec.get("location") or {}; tz=_loc_tz(loc); now=dt.datetime.now(tz).replace(second=0,microsecond=0); start=now.replace(minute=0); end=start+dt.timedelta(hours=max(6,min(72,hours)))
        if spec["mode"]=="local":
            severe=bool(severe_by_location.get(str(loc.get("id"))))
            events=_local_events(settings,loc,severe,hours)
        elif spec["mode"]=="radar":
            events=[]; cursor=start
            while cursor<end:
                b=min(end,cursor+dt.timedelta(hours=1)); events.append((cursor,b,"RWN Radar","Continuous local, regional and wide-area radar from Roller Weather Network.")); cursor=b
        else:
            severe=bool(severe_by_location.get(str(loc.get("id"))))
            events=[]; cursor=start
            while cursor<end:
                b=min(end,cursor+dt.timedelta(hours=1))
                title="RWN Severe Weather Coverage" if severe and cursor<=now<b else "RWN Severe Weather Center"
                desc="Continuous warning details, alert radar and local severe-weather coverage." if "Coverage" in title else "SPC outlooks, storm potential, radar and severe-weather readiness."
                events.append((cursor,b,title,desc)); cursor=b
        for a,b,title,desc in events:
            lines += [f'  <programme start="{_xmltv_time(a)}" stop="{_xmltv_time(b)}" channel="{escape(spec["id"])}">',f'    <title>{escape(title)}</title>',f'    <desc>{escape(desc)}</desc>','    <category>Weather</category>','  </programme>']
    lines.append('</tv>'); return "\n".join(lines)+"\n"
