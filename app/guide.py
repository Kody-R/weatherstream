from __future__ import annotations

import datetime as dt
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from app.events import EVENT_TYPES
from app.network import normalized_regions


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


def channel_specs(settings: dict[str, Any], include_disabled: bool = False) -> list[dict[str, Any]]:
    cfg=settings.get("channels") or {}; locations=list(settings.get("locations") or []); by_id={str(x.get("id")):x for x in locations}; raw=[]
    regions=normalized_regions(settings); multi=len(regions)>1; remaining=max(1,min(24,int(cfg.get("max_zip_channels",12))))
    event_cfg=settings.get("event_channels") or {}
    for region_index, region in enumerate(regions):
        prefix=f"{region['id']}-" if multi else ""; id_prefix=f"{region['id']}." if multi else ""
        callsign=region.get("callsign") or settings.get("station_callsign") or "RWN"
        for location_id in region.get("location_ids",[]):
            if remaining <= 0: break
            loc=by_id.get(str(location_id))
            if not loc: continue
            postal=str(loc.get("postal_code") or loc.get("id") or "local")
            raw.append({"id":f"rwn.{id_prefix}zip.{postal}","key":f"{prefix}zip-{postal}","name":f"{callsign} Local - {loc.get('name') or postal}","mode":"local","location":loc,"region":region,"master_enabled":bool(cfg.get("per_zip_enabled",True))})
            remaining-=1
        primary=by_id.get(str(region.get("primary_location_id")))
        if not primary: continue
        raw.append({"id":f"rwn.{id_prefix}radar","key":f"{prefix}radar","name":f"{callsign} Radar — {region.get('name')}","mode":"radar","location":primary,"region":region,"master_enabled":bool(cfg.get("radar_enabled",True))})
        raw.append({"id":f"rwn.{id_prefix}severe","key":f"{prefix}severe","name":f"{callsign} Severe Weather — {region.get('name')}","mode":"severe","location":primary,"region":region,"master_enabled":bool(cfg.get("severe_enabled",True))})
        raw.append({"id":f"rwn.{id_prefix}tropics","key":f"{prefix}tropics","name":f"{callsign} Tropics Watch — {region.get('name')}","mode":"tropics","location":primary,"region":region,"master_enabled":bool(cfg.get("tropics_enabled",True))})
        for event_type, definition in EVENT_TYPES.items():
            raw.append({"id":f"rwn.{id_prefix}event.{event_type}","key":f"{prefix}event-{event_type}","name":f"{callsign} {definition['name']} — {region.get('name')}","mode":f"event_{event_type}","event_type":event_type,"location":primary,"region":region,"master_enabled":bool(event_cfg.get("enabled",True) and (event_cfg.get("types") or {}).get(event_type,True))})

    lineup = cfg.get("lineup") if isinstance(cfg.get("lineup"), list) else []
    meta = {str(x.get("key")): x for x in lineup if isinstance(x, dict) and x.get("key")}
    specs=[]
    for idx,spec in enumerate(raw):
        row=meta.get(spec["key"], {})
        enabled=bool(spec.get("master_enabled",True) and row.get("enabled", True))
        if not include_disabled and not enabled:
            continue
        spec=dict(spec)
        spec["enabled"] = enabled
        spec["name"] = str(row.get("name") or spec["name"])
        spec["number"] = int(row.get("number") or (201+idx))
        spec["override"] = (cfg.get("overrides") or {}).get(spec["key"], {}) if isinstance(cfg.get("overrides"), dict) else {}
        spec["branding_profile"] = spec["override"].get("branding_profile") or (spec.get("region") or {}).get("branding_profile") or "default"
        specs.append(spec)
    specs.sort(key=lambda x:(int(x.get("number") or 9999), x["key"]))
    return specs


def generate_xmltv(settings: dict[str, Any], severe_by_location: dict[str,bool] | None = None, hours: int = 24, tropical_status: dict[str,Any] | None = None) -> str:
    severe_by_location=severe_by_location or {}; tropical_status=tropical_status or {}; specs=channel_specs(settings)
    lines=['<?xml version="1.0" encoding="UTF-8"?>','<tv generator-info-name="WeatherStream 0.3.0">']
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
        elif spec["mode"]=="severe":
            severe=bool(severe_by_location.get(str(loc.get("id"))))
            events=[]; cursor=start
            while cursor<end:
                b=min(end,cursor+dt.timedelta(hours=1))
                title="RWN Severe Weather Coverage" if severe and cursor<=now<b else "RWN Severe Weather Center"
                desc="Continuous warning details, alert radar and local severe-weather coverage." if "Coverage" in title else "SPC outlooks, storm potential, radar and severe-weather readiness."
                events.append((cursor,b,title,desc)); cursor=b
        elif spec["mode"]=="tropics":
            systems=tropical_status.get("systems") or []; names=", ".join(str(x.get("name") or "System") for x in systems[:3])
            events=[]; cursor=start
            while cursor<end:
                b=min(end,cursor+dt.timedelta(hours=1))
                title=f"RWN Tropics Watch — {names}" if names else "RWN Tropics Watch — Atlantic & Gulf Update"
                desc="Official NHC system status, forecast tracks, Gulf proximity, and local tropical alerts." if systems else "Atlantic hurricane-season monitoring and the latest official NHC Tropical Weather Outlook."
                events.append((cursor,b,title,desc)); cursor=b
        else:
            event_type=str(spec.get("event_type") or spec["mode"].removeprefix("event_")); label=(EVENT_TYPES.get(event_type) or {}).get("name","Weather Event")
            events=[]; cursor=start
            while cursor<end:
                b=min(end,cursor+dt.timedelta(hours=1)); events.append((cursor,b,f"{spec['name']}",f"Automatic {label.lower()} coverage with official alerts, forecasts, and regional maps.")); cursor=b
        for a,b,title,desc in events:
            lines += [f'  <programme start="{_xmltv_time(a)}" stop="{_xmltv_time(b)}" channel="{escape(spec["id"])}">',f'    <title>{escape(title)}</title>',f'    <desc>{escape(desc)}</desc>','    <category>Weather</category>','  </programme>']
    lines.append('</tv>'); return "\n".join(lines)+"\n"
