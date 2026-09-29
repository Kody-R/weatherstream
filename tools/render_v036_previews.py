from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path

from app.config import DEFAULT_SETTINGS
from app.renderer import WeatherRenderer


class RevisionSource:
    def __init__(self, value): self.value=value
    def snapshot_if_changed(self, previous): return (1, None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


def base_fixture(width=1280,height=720):
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    settings["video"]["width"]=width; settings["video"]["height"]=height
    loc={"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings["locations"]=[loc]; settings["primary_location_id"]="ruston"
    now=dt.datetime.now(dt.timezone.utc)
    times=[(now+dt.timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)]
    dates=[(now.date()+dt.timedelta(days=i)).isoformat() for i in range(7)]
    primary={
        "location":loc,"fetched_at":now.isoformat(),
        "current":{"temperature_2m":78,"apparent_temperature":79,"relative_humidity_2m":58,"dewpoint_f":62,"weather_code":1,"description":"Mostly Clear","wind_speed_10m":7,"wind_gusts_10m":12,"wind_cardinal":"S","surface_pressure":1015},
        "hourly":{"time":times,"temperature_2m":[78]*24,"apparent_temperature":[79]*24,"precipitation_probability":[5]*24,"precipitation":[0]*24,"weather_code":[1]*24,"wind_speed_10m":[7]*24,"wind_gusts_10m":[12]*24,"relative_humidity_2m":[58]*24},
        "daily":{"time":dates,"temperature_2m_max":[84]*7,"temperature_2m_min":[61]*7,"precipitation_probability_max":[10]*7,"precipitation_sum":[0]*7,"weather_code":[1]*7},
        "nws":{"periods":[],"office":"SHV"},
        "air_quality":{"aqi":42,"category":"GOOD","primary_pollutant":"PM2.5"},
        "rivers":{"gauges":[]},
    }
    snapshot={"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{"hourly":{}},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snapshot,primary


def scenario(name,width,height):
    settings,snap,p=base_fixture(width,height)
    if name=="rain":
        p["current"].update({"description":"Rain Developing","weather_code":61})
        p["hourly"]["precipitation_probability"]=[75]*24; p["hourly"]["precipitation"]=[0.05]*24
    elif name=="storms":
        p["current"].update({"description":"Thunderstorms Nearby","weather_code":95,"wind_gusts_10m":30})
        p["hourly"]["precipitation_probability"]=[65]*24; p["hourly"]["precipitation"]=[0.04]*24; p["hourly"]["wind_gusts_10m"]=[30]*24
        snap["storm_guidance"]={"hourly":{"time":p["hourly"]["time"],"thunderstorm_probability":[55]*24}}
        settings["_spc_outlook"]={"day1":{"rank":3,"name":"Slight Risk"}}
    elif name=="severe":
        p["current"].update({"description":"Severe Thunderstorms","weather_code":95,"wind_gusts_10m":46})
        p["hourly"]["precipitation_probability"]=[90]*24; p["hourly"]["wind_gusts_10m"]=[46]*24
        snap["alerts"]=[{"event":"Tornado Warning","areaDesc":"Lincoln Parish","severity":"Extreme"}]; snap["alerts_by_location"]["ruston"]=snap["alerts"]
    elif name=="heat":
        p["current"].update({"temperature_2m":96,"apparent_temperature":104,"relative_humidity_2m":64,"dewpoint_f":72,"description":"Hot and Humid","weather_code":0})
        p["hourly"]["temperature_2m"]=[96]*24; p["hourly"]["apparent_temperature"]=[104]*24; p["daily"]["temperature_2m_max"]=[101]*7
    elif name=="wind":
        p["current"].update({"wind_speed_10m":24,"wind_gusts_10m":52,"description":"Windy","weather_code":3})
        p["hourly"]["wind_speed_10m"]=[24]*24; p["hourly"]["wind_gusts_10m"]=[52]*24
    elif name=="air_quality":
        p["current"].update({"description":"Hazy","weather_code":45})
        p["air_quality"]={"aqi":165,"category":"UNHEALTHY","primary_pollutant":"PM2.5","source":"Open-Meteo / CAMS"}
    snap["locations"]["ruston"]=p
    return settings,snap


def main():
    out=Path("previews"); out.mkdir(exist_ok=True)
    for width,height,label in ((1280,720,"720p"),(1920,1080,"1080p")):
        for name in ("quiet","rain","storms","severe","heat","wind","air_quality"):
            settings,snap=scenario(name,width,height)
            renderer=WeatherRenderer(RevisionSource(settings),RevisionSource(snap))
            renderer.render_preview("story_brief",location_id="ruston").save(out/f"story-{name}-{label}.png")
        settings,snap=scenario("rain",width,height)
        renderer=WeatherRenderer(RevisionSource(settings),RevisionSource(snap))
        renderer.render_preview("current",location_id="ruston").save(out/f"current-story-ribbon-{label}.png")


if __name__=="__main__": main()
