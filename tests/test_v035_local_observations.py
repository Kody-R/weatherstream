from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ConfigStore, DEFAULT_SETTINGS
from app.history import HistoryStore
from app.localdata import _aqi_category, _haversine_miles
from app.renderer import WeatherRenderer
from app.studio import AVAILABLE_SLIDES
from app.weather import merge_observation_into_current


class _RevisionSource:
    def __init__(self, value): self.value=value
    def snapshot_if_changed(self, previous): return (1, None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


def fixture():
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    loc={"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings["locations"]=[loc]; settings["primary_location_id"]="ruston"
    now=dt.datetime.now(dt.timezone.utc)
    times=[(now+dt.timedelta(hours=i)).isoformat(timespec="minutes") for i in range(24)]
    dates=[(now.date()+dt.timedelta(days=i)).isoformat() for i in range(7)]
    primary={
        "location":loc,"fetched_at":now.isoformat(),
        "observation":{"station_id":"KRSN","station_name":"Ruston Regional Airport","timestamp":now.isoformat()},
        "current":{"temperature_2m":82,"apparent_temperature":85,"relative_humidity_2m":68,"dewpoint_f":70,"weather_code":2,"description":"Partly Cloudy","wind_speed_10m":9,"wind_gusts_10m":18,"wind_cardinal":"S","surface_pressure":1013,"visibility_miles":10,"station_id":"KRSN","observation_source":"NWS / KRSN"},
        "hourly":{"time":times,"temperature_2m":[82]*24,"precipitation_probability":[25]*24,"precipitation":[0.02]*24,"weather_code":[2]*24,"wind_speed_10m":[9]*24,"wind_gusts_10m":[18]*24,"relative_humidity_2m":[68]*24,"apparent_temperature":[85]*24,"wind_direction_10m":[180]*24,"cloud_cover":[45]*24},
        "daily":{"time":dates,"temperature_2m_max":[88]*7,"temperature_2m_min":[69]*7,"precipitation_probability_max":[35]*7,"precipitation_sum":[0.08]*7,"weather_code":[2]*7},
        "nws":{"periods":[],"office":"SHV"},
        "air_quality":{"aqi":42,"category":"GOOD","pm2_5":9.5,"pm10":18,"ozone":54,"primary_pollutant":"PM2.5","source":"Open-Meteo / CAMS","kind":"modeled_guidance","fetched_at":now.isoformat()},
        "rivers":{"gauges":[{"id":"USGS-07367005","number":"07367005","name":"OUACHITA RIVER AT MONROE LA","gage_height_ft":27.4,"streamflow_cfs":18200,"distance_miles":28.3,"provisional":True}],"primary_history":[{"time":(now-dt.timedelta(hours=6)).isoformat(),"value":25.6},{"time":now.isoformat(),"value":27.4}],"source":"USGS Water Data","fetched_at":now.isoformat()},
        "climate":{"station_id":"USW00003951","station_name":"TEST STATION","normal_high_f":84,"normal_low_f":63,"normal_mean_f":73.5,"normal_mtd_precip_in":2.9,"source":"NOAA NCEI 1991-2020 Normals","fetched_at":now.isoformat()},
    }
    snap={"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snap


class V035LocalObservationTests(unittest.TestCase):
    def test_schema_23_defaults_and_slides(self):
        self.assertEqual(DEFAULT_SETTINGS["version"],27)
        self.assertTrue(DEFAULT_SETTINGS["local_data"]["observations"]["enabled"])
        self.assertTrue(DEFAULT_SETTINGS["local_data"]["air_quality"]["enabled"])
        for slide in ("today_so_far","past_24_hours","air_quality","local_rivers","climate_context"):
            self.assertIn(slide, AVAILABLE_SLIDES)
            self.assertIn(slide, DEFAULT_SETTINGS["slides"])

    def test_schema_22_migration_preserves_custom_sequence_and_adds_local_products(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); settings_path=root/"settings.json"
            settings_path.write_text(json.dumps({"version":22,"presentation":{"sequence":["station_id","current","hourly","seven_day"]},"channels":{"zip_sequence":["current","hourly","seven_day"]}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",settings_path): upgraded=ConfigStore().get()
        self.assertEqual(upgraded["version"],27)
        self.assertEqual(upgraded["presentation"]["sequence"][:2],["station_id","current"])
        self.assertIn("today_so_far",upgraded["presentation"]["sequence"])
        self.assertIn("air_quality",upgraded["channels"]["zip_sequence"])

    def test_nws_observation_merge_preserves_model_fallback_fields(self):
        model={"temperature_2m":80,"weather_code":2,"cloud_cover":45,"description":"Partly Cloudy","wind_speed_10m":7}
        obs={"station_id":"KRSN","station_name":"Ruston Regional Airport","timestamp":"2026-09-29T16:00:00+00:00","temperature_f":82,"humidity":68,"visibility_miles":10.0,"pressure_hpa":1013.2}
        merged=merge_observation_into_current(model,obs)
        self.assertEqual(merged["temperature_2m"],82)
        self.assertEqual(merged["weather_code"],2)
        self.assertEqual(merged["cloud_cover"],45)
        self.assertEqual(merged["visibility_miles"],10.0)
        self.assertEqual(merged["station_id"],"KRSN")

    def test_history_deduplicates_station_observation_and_builds_today_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            store=HistoryStore(Path(folder)/"history.db")
            when=dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=5)
            current={"temperature_2m":82,"apparent_temperature":84,"relative_humidity_2m":65,"surface_pressure":1012,"wind_speed_10m":8,"wind_gusts_10m":19,"precipitation":0.1,"weather_code":2,"dewpoint_f":69,"visibility_miles":10,"observation_source":"NWS / KRSN","station_id":"KRSN"}
            self.assertEqual(store.record_many([("ruston",current,when)]),1)
            self.assertEqual(store.record_many([("ruston",current,when)]),0)
            summary=store.today_summary("ruston","America/Chicago")
            self.assertEqual(summary["samples"],1)
            self.assertEqual(summary["high"],82)
            self.assertEqual(summary["station_id"],"KRSN")

    def test_aqi_categories_and_distance_helpers(self):
        self.assertEqual(_aqi_category(42),"GOOD")
        self.assertEqual(_aqi_category(125),"UNHEALTHY FOR SENSITIVE GROUPS")
        self.assertLess(_haversine_miles(32.52,-92.64,32.51,-92.65),2)

    def test_new_local_data_slides_render(self):
        settings,snapshot=fixture()
        with tempfile.TemporaryDirectory() as folder:
            history=HistoryStore(Path(folder)/"history.db")
            now=dt.datetime.now(dt.timezone.utc)
            samples=[]
            for i in range(12):
                samples.append(("ruston",{"temperature_2m":68+i,"apparent_temperature":68+i,"relative_humidity_2m":70-i,"surface_pressure":1015-i*0.3,"wind_speed_10m":6+i/2,"wind_gusts_10m":10+i,"precipitation":0.1,"weather_code":2,"dewpoint_f":62,"visibility_miles":10,"observation_source":"NWS / KRSN","station_id":"KRSN"},now-dt.timedelta(hours=11-i)))
            history.record_many(samples)
            renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snapshot),history_store=history)
            baseline=renderer.render_preview("current",location_id="ruston")
            for slide in ("today_so_far","past_24_hours","air_quality","local_rivers","climate_context"):
                with self.subTest(slide=slide):
                    image=renderer.render_preview(slide,location_id="ruston")
                    self.assertEqual(image.size,(1280,720))
                    self.assertNotEqual(image.tobytes(),baseline.tobytes())


if __name__=="__main__": unittest.main()
