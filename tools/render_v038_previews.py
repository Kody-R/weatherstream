from __future__ import annotations

import copy, datetime as dt
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from app.config import DEFAULT_SETTINGS
from app.renderer import WeatherRenderer
from app.broadcast_motion import DESK_TRANSITIONS

OUT=Path(__file__).resolve().parents[2] / 'weatherstream-v0.3.8-previews-work'
OUT.mkdir(parents=True, exist_ok=True)

class Source:
    def __init__(self, value): self.value=value
    def snapshot_if_changed(self, previous): return (1, None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)

def data():
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    loc={"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings['locations']=[loc]; settings['primary_location_id']='ruston'
    now=dt.datetime.now(dt.timezone.utc)
    times=[(now+dt.timedelta(hours=i)).isoformat(timespec='minutes') for i in range(24)]
    dates=[(now.date()+dt.timedelta(days=i)).isoformat() for i in range(7)]
    p={"location":loc,"fetched_at":now.isoformat(),
       "current":{"temperature_2m":82,"apparent_temperature":86,"relative_humidity_2m":66,"dewpoint_f":69,"weather_code":2,"description":"Partly Cloudy","wind_speed_10m":9,"wind_gusts_10m":18,"wind_cardinal":"S","surface_pressure":1014},
       "hourly":{"time":times,"temperature_2m":[82,84,86,87,86,83,80,77]+[75]*16,"apparent_temperature":[86]*24,"precipitation_probability":[10,15,20,30,45,55,50,35]+[20]*16,"precipitation":[0,.01,.01,.02,.05,.08,.04,.01]+[0]*16,"weather_code":[2,2,2,3,61,63,61,3]+[2]*16,"wind_speed_10m":[9,10,11,12,13,14,12,10]+[8]*16,"wind_gusts_10m":[18,20,22,24,27,29,24,20]+[15]*16,"relative_humidity_2m":[66]*24},
       "daily":{"time":dates,"temperature_2m_max":[87,84,88,91,86,89,92],"temperature_2m_min":[71,69,70,72,68,69,71],"precipitation_probability_max":[55,70,20,10,60,25,10],"precipitation_sum":[.18,.42,.03,0,.35,.05,0],"weather_code":[61,95,2,1,61,2,0]},
       "nws":{"periods":[{"name":"Today","temperature":87,"temperatureUnit":"F","shortForecast":"Showers and thunderstorms possible","detailedForecast":"Scattered showers and thunderstorms developing this afternoon.","probabilityOfPrecipitation":{"value":55},"windSpeed":"5 to 10 mph","windDirection":"S"}],"office":"SHV"},
       "air_quality":{"aqi":88,"category":"MODERATE","primary_pollutant":"PM2.5"},"rivers":{"gauges":[]}}
    snap={"locations":{"ruston":p},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{"hourly":{}},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snap

def save_gif(renderer,a,b,kind,name,settings):
    alphas=[i/11 for i in range(12)]
    frames=[renderer._transition(a,b,x,kind,settings=settings).convert('P',palette=Image.Palette.ADAPTIVE) for x in alphas]
    frames[0].save(OUT/name,save_all=True,append_images=frames[1:],duration=70,loop=0,optimize=False)

def main():
    settings,snap=data(); r=WeatherRenderer(Source(settings),Source(snap))
    mode_map={"severe":"severe","flood":"event_flood","winter":"event_winter","heat":"event_heat","wildfire":"event_wildfire","tropical":"tropics"}
    # Normal network transition.
    a=r.render_preview('current',location_id='ruston',channel_mode='local')
    b=r.render_preview('seven_day',location_id='ruston',channel_mode='local')
    save_gif(r,a,b,'rwn_wipe','network-rwn-wipe.gif',settings)
    r._transition(a,b,.5,'rwn_wipe',settings=settings).save(OUT/'network-rwn-wipe-midpoint.png')

    contact=Image.new('RGB',(1280,720),(5,15,32)); d=ImageDraw.Draw(contact)
    try: f=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',22)
    except: f=ImageFont.load_default()
    rows=[]
    for key,mode in mode_map.items():
        s=copy.deepcopy(settings); s['_channel_mode']=mode
        a=r.render_preview('current',location_id='ruston',channel_mode=mode)
        b=r.render_preview('nws_forecast',location_id='ruston',channel_mode=mode)
        kind=DESK_TRANSITIONS[key]
        save_gif(r,a,b,kind,f'{key}-{kind}.gif',s)
        mid=r._transition(a,b,.5,kind,settings=s); mid.save(OUT/f'{key}-{kind}-midpoint.png')
        thumb=mid.resize((385,217),Image.Resampling.LANCZOS); rows.append((key,kind,thumb))
    # 3x2 review board
    for i,(key,kind,thumb) in enumerate(rows):
        col=i%3; row=i//3; x=18+col*418; y=58+row*322
        contact.paste(thumb,(x,y)); d.text((x,y+226),f'{key.upper()}  •  {kind.replace("_"," ").upper()}',font=f,fill='white')
    d.text((18,16),'RWN v0.3.8 • BROADCAST MOTION DESK TRANSITIONS',font=f,fill=(220,240,255))
    contact.save(OUT/'event-motion-showcase.png')

    # Entry-motion sample from a normal current board.
    frames=[]
    for i in range(10):
        x=i/9
        frames.append(r._apply_entry_motion(a,x,'broadcast',settings).convert('P',palette=Image.Palette.ADAPTIVE))
    frames[0].save(OUT/'screen-entry-settle.gif',save_all=True,append_images=frames[1:],duration=65,loop=0,optimize=False)

if __name__=='__main__': main()
