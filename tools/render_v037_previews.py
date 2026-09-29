from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from app.config import DEFAULT_SETTINGS
from app.renderer import WeatherRenderer, font


class RevisionSource:
    def __init__(self, value): self.value=value
    def snapshot_if_changed(self, previous): return (1, None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


def fixture():
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    loc={"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings["locations"]=[loc]; settings["primary_location_id"]="ruston"; settings["station_callsign"]="RWN"
    now=dt.datetime.now(dt.timezone.utc)
    times=[(now+dt.timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)]
    dates=[(now.date()+dt.timedelta(days=i)).isoformat() for i in range(7)]
    primary={
        "location":loc,"fetched_at":now.isoformat(),
        "current":{"temperature_2m":91,"apparent_temperature":103,"relative_humidity_2m":61,"dewpoint_f":72,"weather_code":2,"description":"Partly Cloudy","wind_speed_10m":14,"wind_gusts_10m":31,"wind_cardinal":"S","surface_pressure":1009,"visibility_miles":10},
        "hourly":{"time":times,"temperature_2m":[91,93,95,96,95,92,88,84]+[82]*16,"apparent_temperature":[103]*24,"precipitation_probability":[20,25,30,45,60,70,55,35]+[20]*16,"precipitation":[0,0,0.01,0.03,0.12,0.2,0.08,0.02]+[0]*16,"weather_code":[2,2,2,80,95,95,81,3]+[2]*16,"wind_speed_10m":[14]*24,"wind_gusts_10m":[31]*24,"relative_humidity_2m":[61]*24},
        "daily":{"time":dates,"temperature_2m_max":[96,91,88,90,92,94,95],"temperature_2m_min":[75,72,69,70,71,72,73],"precipitation_probability_max":[70,45,20,15,20,15,10],"precipitation_sum":[0.46,0.15,0.02,0,0,0,0],"weather_code":[95,80,2,1,1,0,0]},
        "nws":{"periods":[],"office":"SHV"},
        "air_quality":{"aqi":118,"category":"UNHEALTHY FOR SENSITIVE GROUPS","primary_pollutant":"PM2.5"},
        "rivers":{"gauges":[{"name":"OUACHITA RIVER AT MONROE LA","gage_height_ft":27.4,"distance_miles":28.3}],"primary_history":[]},
    }
    snap={"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{"hourly":{}},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snap,primary


EVENTS={
    "event_tornado":("tornado","Tornado Warning","Lincoln Parish, Union Parish","A severe thunderstorm capable of producing a tornado is moving through the warned area."),
    "event_flood":("flood","Flood Watch","Lincoln Parish and surrounding areas","Multiple rounds of heavy rain may lead to flooding of low-lying and poor-drainage areas."),
    "event_winter":("winter","Winter Storm Warning","North Louisiana","Heavy snow and ice are expected. Travel may become very difficult."),
    "event_heat":("heat","Heat Advisory","North Louisiana","Heat index values up to 110 degrees are expected during the afternoon."),
    "event_wildfire":("wildfire","Red Flag Warning","North Louisiana","Low humidity and gusty winds can support rapid fire growth."),
}


def preview_mark(img: Image.Image) -> None:
    d=ImageDraw.Draw(img)
    d.rounded_rectangle((img.width-315,94,img.width-28,124),8,fill="#050b12dd",outline="#ffffff55")
    d.text((img.width-42,109),"DESIGN PREVIEW • SAMPLE DATA",font=font(11,bold=True,mono=True),fill="#ffffff",anchor="rm")


def main(out_dir: str):
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    settings,snap,primary=fixture(); renderer=WeatherRenderer(RevisionSource(settings),RevisionSource(snap))
    showcase=[]
    for width,height in ((1280,720),(1920,1080)):
        settings["video"]["width"]=width; settings["video"]["height"]=height
        renderer=WeatherRenderer(RevisionSource(settings),RevisionSource(snap))
        for mode,label in [("severe","severe"),("event_flood","flood"),("event_winter","winter"),("event_heat","heat"),("event_wildfire","wildfire"),("tropics","tropical")]:
            img=renderer.render_preview("current",location_id="ruston",channel_mode=mode); preview_mark(img)
            path=out/f"{label}-current-{height}p.png"; img.save(path)
            if height==720: showcase.append((label.upper(),img.copy()))
        for mode,(etype,event,area,desc) in EVENTS.items():
            scoped,snapshot=renderer._channel_context("ruston",mode); p=renderer._primary(scoped,snapshot)
            alert={"event":event,"areaDesc":area,"headline":desc,"description":desc,"severity":"Severe","urgency":"Expected","expires":(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=2)).isoformat()}
            scoped["_event"]={"event_type":etype,"active":True,"cooldown_active":False,"alerts":[alert]}; snapshot=dict(snapshot); snapshot["alerts"]=[alert]
            img=renderer._render_slide("event_summary",scoped,snapshot,p,dt.datetime.now().timestamp(),0.35); preview_mark(img)
            img.save(out/f"{etype}-desk-{height}p.png")
        scoped,snapshot=renderer._channel_context("ruston","tropics"); p=renderer._primary(scoped,snapshot)
        scoped["_tropical"]={"systems":[{"name":"FRANCINE","classification_name":"Tropical Storm","intensity_mph":60,"movement_degrees":315,"movement_mph":12,"pressure_mb":992}],"outlook":{"areas":[]},"activation":{"active":True,"reasons":["Tropical system is within the configured Gulf/local monitoring radius."]},"segment_active":True,"channel_active":True}
        img=renderer._render_slide("tropical_update",scoped,snapshot,p,dt.datetime.now().timestamp(),0.35); preview_mark(img)
        img.save(out/f"tropical-desk-{height}p.png")

    # One side-by-side review board using the 720p Current Conditions views.
    board=Image.new("RGB",(1920,1080),"#060d16"); bd=ImageDraw.Draw(board)
    bd.text((960,38),"RWN v0.3.7 • EVENT CHANNEL IDENTITY",font=font(38,bold=True),fill="#ffffff",anchor="mm")
    bd.text((960,82),"SIX DEDICATED DESKS • ONE RWN VISUAL SYSTEM",font=font(15,bold=True,mono=True),fill="#9fb8ca",anchor="mm")
    cell_w,cell_h=600,390; margin_x=30; top=120
    for i,(label,img) in enumerate(showcase):
        col=i%3; row=i//3; x=margin_x+col*630; y=top+row*455
        thumb=img.resize((600,338),Image.Resampling.LANCZOS); board.paste(thumb,(x,y))
        bd.text((x+300,y+363),label,font=font(22,bold=True,mono=True),fill="#ffffff",anchor="mm")
    board.save(out/"event-desks-showcase.png")


if __name__=="__main__":
    import sys
    main(sys.argv[1] if len(sys.argv)>1 else "previews-v037")
