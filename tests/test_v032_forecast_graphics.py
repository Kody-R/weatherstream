from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ConfigStore, DEFAULT_SETTINGS
from app.renderer import WeatherRenderer
from app.studio import AVAILABLE_SLIDES


class _RevisionSource:
    def __init__(self, value): self.value = value
    def snapshot_if_changed(self, previous): return (1, None if previous == 1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


def fixture():
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    location = {"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings["locations"]=[location]; settings["primary_location_id"]="ruston"
    now=dt.datetime.now().replace(minute=0,second=0,microsecond=0)
    times=[(now+dt.timedelta(hours=i)).isoformat(timespec="minutes") for i in range(48)]
    dates=[(now.date()+dt.timedelta(days=i)).isoformat() for i in range(7)]
    pattern=lambda vals:[vals[i%len(vals)] for i in range(48)]
    primary={
        "location":location,"fetched_at":dt.datetime.now(dt.timezone.utc).isoformat(),
        "current":{"time":times[0],"temperature_2m":82,"relative_humidity_2m":74,"apparent_temperature":87,"is_day":1,"weather_code":2,"description":"Partly Cloudy","cloud_cover":55,"surface_pressure":1011,"wind_speed_10m":12,"wind_gusts_10m":30,"wind_cardinal":"S"},
        "hourly":{
            "time":times,
            "temperature_2m":pattern([82,84,86,87,85,81,78,75]),
            "apparent_temperature":pattern([87,89,91,92,89,84,80,77]),
            "relative_humidity_2m":pattern([74,70,67,65,72,80,86,90]),
            "cloud_cover":pattern([45,50,60,80,90,75,55,35]),
            "precipitation_probability":pattern([10,20,35,60,75,55,30,15]),
            "precipitation":pattern([0,0,.01,.05,.12,.08,.02,0]),
            "weather_code":pattern([2,2,3,61,95,63,61,2]),
            "wind_speed_10m":pattern([10,12,14,16,18,15,12,9]),
            "wind_gusts_10m":pattern([16,20,24,30,36,32,25,18]),
            "wind_direction_10m":pattern([160,170,180,190,200,210,220,230]),
        },
        "daily":{"time":dates,"weather_code":[95,61,2,1,63,2,0],"temperature_2m_max":[87,84,88,91,86,89,92],"temperature_2m_min":[71,69,70,72,68,69,71],"precipitation_probability_max":[75,70,20,10,60,25,10],"precipitation_sum":[.28,.35,0,0,.4,.02,0],"sunrise":[f"{d}T06:55" for d in dates],"sunset":[f"{d}T19:03" for d in dates]},
        "nws":{"office":"SHV","error":None,"periods":[
            {"name":"This Afternoon","temperature":87,"temperatureUnit":"F","windSpeed":"10 to 15 mph","windDirection":"S","shortForecast":"Showers and thunderstorms likely","detailedForecast":"Showers and thunderstorms likely, mainly after 3pm. Some storms could produce heavy rain and gusty winds.","precipitationProbability":70},
            {"name":"Tonight","temperature":71,"temperatureUnit":"F","windSpeed":"5 to 10 mph","windDirection":"SE","shortForecast":"Showers likely","detailedForecast":"Showers likely before midnight, then a chance of showers overnight.","precipitationProbability":60},
        ]},
    }
    snap={"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{"hourly":{"time":times,"thunderstorm_probability":pattern([10,15,25,45,55,35,20,10])}},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snap


class V032ForecastGraphicsTests(unittest.TestCase):
    def test_schema_21_defaults_and_studio_palette(self):
        self.assertEqual(DEFAULT_SETTINGS["version"],22)
        self.assertTrue(DEFAULT_SETTINGS["forecast_graphics"]["enabled"])
        for slide in ("day_ahead","humidity_outlook","wind_outlook","rain_accumulation"):
            self.assertIn(slide,AVAILABLE_SLIDES)
            self.assertIn(slide,DEFAULT_SETTINGS["slides"])

    def test_new_forecast_graphics_render(self):
        settings,snapshot=fixture()
        renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snapshot))
        current=renderer.render_preview("current",location_id="ruston")
        for slide in ("day_ahead","humidity_outlook","wind_outlook","rain_accumulation","nws_forecast"):
            with self.subTest(slide=slide):
                image=renderer.render_preview(slide,location_id="ruston")
                self.assertEqual(image.size,(1280,720))
                self.assertNotEqual(image.tobytes(), current.tobytes())

    def test_weather_story_promotes_rain_and_wind(self):
        settings,snapshot=fixture()
        renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snapshot))
        prepared_settings,prepared_snapshot=renderer._channel_context("ruston","local")
        wanted=renderer._smart_wanted(prepared_settings,prepared_snapshot,dt.datetime.now().timestamp())
        self.assertIn("precipitation",wanted)
        self.assertIn("rain_accumulation",wanted)
        self.assertIn("wind_outlook",wanted)
        self.assertLess(wanted.index("precipitation"),wanted.index("seven_day"))

    def test_schema_19_upgrade_inserts_new_graphics_without_replacing_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); settings_path=root/"settings.json"
            settings_path.write_text(json.dumps({"version":19,"presentation":{"sequence":["station_id","current","nws_forecast","hourly","precipitation","seven_day"]},"channels":{"zip_sequence":["current","nws_forecast","hourly","precipitation","seven_day"]}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",settings_path):
                upgraded=ConfigStore().get()
            self.assertEqual(upgraded["version"],22)
            self.assertEqual(upgraded["presentation"]["sequence"][:2],["station_id","current"])
            for slide in ("day_ahead","humidity_outlook","wind_outlook","rain_accumulation"):
                self.assertIn(slide,upgraded["presentation"]["sequence"])


if __name__ == "__main__":
    unittest.main()
