#!/usr/bin/env python3
"""Render deterministic v0.3.5 Local Observations & Climate QA boards."""
from __future__ import annotations
import copy
import datetime as dt
import tempfile
from pathlib import Path

from app.config import DEFAULT_SETTINGS
from app.history import HistoryStore
from app.renderer import WeatherRenderer


class _RevisionSource:
    def __init__(self, value): self.value = value
    def snapshot_if_changed(self, previous): return 1, None if previous == 1 else copy.deepcopy(self.value)
    def get(self): return copy.deepcopy(self.value)


def build_fixture():
    loc = {"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    now = dt.datetime.now(dt.timezone.utc)
    times = [(now + dt.timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)]
    dates = [(now.date() + dt.timedelta(days=i)).isoformat() for i in range(7)]
    primary = {
        "location": loc, "fetched_at": now.isoformat(),
        "observation":{"station_id":"KRSN","station_name":"Ruston Regional Airport","timestamp":now.isoformat()},
        "current":{"temperature_2m":82,"apparent_temperature":86,"relative_humidity_2m":66,"dewpoint_f":69,"is_day":1,"weather_code":2,"description":"Partly Cloudy","wind_speed_10m":9,"wind_gusts_10m":18,"wind_direction_10m":180,"wind_cardinal":"S","surface_pressure":1013.5,"visibility_miles":10,"cloud_cover":45,"station_id":"KRSN","station_name":"Ruston Regional Airport","observation_source":"NWS / MADIS observation"},
        "hourly":{"time":times,"temperature_2m":[82]*24,"precipitation_probability":[30]*24,"precipitation":[0.02]*24,"weather_code":[2]*24,"wind_speed_10m":[9]*24,"wind_gusts_10m":[18]*24,"relative_humidity_2m":[66]*24,"apparent_temperature":[86]*24,"wind_direction_10m":[180]*24,"cloud_cover":[45]*24},
        "daily":{"time":dates,"temperature_2m_max":[88,86,89,91,90,87,89],"temperature_2m_min":[69,68,70,72,71,69,70],"precipitation_probability_max":[35,60,20,10,20,40,15],"precipitation_sum":[0.08,0.25,0,0,0,0.1,0],"weather_code":[2,61,2,1,1,61,2]},
        "nws":{"periods":[],"office":"SHV"},
        "air_quality":{"aqi":42,"category":"GOOD","pm2_5":9.5,"pm10":18.2,"ozone":54.1,"nitrogen_dioxide":11.4,"primary_pollutant":"PM2.5","source":"Open-Meteo / CAMS","kind":"modeled_guidance","fetched_at":now.isoformat()},
        "rivers":{"gauges":[
            {"id":"USGS-07367005","number":"07367005","name":"OUACHITA RIVER AT MONROE LA","gage_height_ft":27.4,"streamflow_cfs":18200,"distance_miles":28.3,"provisional":True},
            {"id":"USGS-07365800","number":"07365800","name":"BAYOU D'ARBONNE NEAR FARMERVILLE LA","gage_height_ft":6.82,"streamflow_cfs":980,"distance_miles":22.1,"provisional":True},
            {"id":"USGS-07366350","number":"07366350","name":"OUACHITA RIVER NEAR CALHOUN LA","gage_height_ft":18.15,"streamflow_cfs":15400,"distance_miles":24.5,"provisional":True}],
            "primary_history":[{"time":(now-dt.timedelta(hours=6-i)).isoformat(),"value":25.6+i*.3} for i in range(7)],"source":"USGS Water Data","fetched_at":now.isoformat()},
        "climate":{"station_id":"USW00003951","station_name":"MONROE REGIONAL AIRPORT","normal_high_f":84,"normal_low_f":63,"normal_mean_f":73.5,"normal_mtd_precip_in":2.91,"source":"NOAA NCEI 1991-2020 Normals","fetched_at":now.isoformat()},
    }
    snap = {"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return loc, now, snap


def main():
    root = Path(__file__).resolve().parents[1]
    out = root / "previews"
    out.mkdir(exist_ok=True)
    loc, now, snap = build_fixture()
    with tempfile.TemporaryDirectory() as td:
        history = HistoryStore(Path(td)/"history.db")
        samples=[]
        for i in range(25):
            stamp = now-dt.timedelta(hours=24-i)
            local_hour = stamp.astimezone(dt.timezone(dt.timedelta(hours=-5))).hour
            temp = 70 + (12 if 7 <= local_hour <= 18 else 2) + (i%5)*0.7
            samples.append(("ruston",{"temperature_2m":temp,"apparent_temperature":temp+2,"relative_humidity_2m":72-(i%8),"surface_pressure":1017-i*0.18,"wind_speed_10m":6+(i%5),"wind_gusts_10m":11+(i%9)*1.5,"precipitation":0.0,"cloud_cover":45,"weather_code":2,"dewpoint_f":65,"visibility_miles":10,"observation_source":"NWS / KRSN","station_id":"KRSN"},stamp))
        history.record_many(samples)
        for width,height,label in ((1280,720,"720p"),(1920,1080,"1080p")):
            settings=copy.deepcopy(DEFAULT_SETTINGS); settings["locations"]=[loc]; settings["primary_location_id"]="ruston"; settings["video"]["width"]=width; settings["video"]["height"]=height
            renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snap),history_store=history)
            for slide in ("current","today_so_far","past_24_hours","air_quality","local_rivers","climate_context"):
                renderer.render_preview(slide,location_id="ruston").save(out/f"{slide}-{label}.png")


if __name__ == "__main__": main()
