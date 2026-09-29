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
from app.story import classify_story, compose_story_sequence
from app.studio import AVAILABLE_SLIDES


class _RevisionSource:
    def __init__(self, value): self.value=value
    def snapshot_if_changed(self, previous): return (1, None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


def fixture():
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    loc={"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings["locations"]=[loc]; settings["primary_location_id"]="ruston"; settings["_channel_mode"]="local"; settings["_render_location_id"]="ruston"
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
    snap={"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{"hourly":{}},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snap,primary


class V036StoryEngineTests(unittest.TestCase):
    def test_schema_24_defaults(self):
        self.assertEqual(DEFAULT_SETTINGS["version"],27)
        self.assertTrue(DEFAULT_SETTINGS["story_engine"]["enabled"])
        self.assertIn("story_brief", AVAILABLE_SLIDES)
        self.assertIn("story_brief", DEFAULT_SETTINGS["slides"])

    def test_schema_23_migration_adds_story_settings_without_reordering_custom_core(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); settings_path=root/"settings.json"
            settings_path.write_text(json.dumps({"version":23,"presentation":{"sequence":["station_id","current","hourly","seven_day"]},"channels":{"zip_sequence":["current","hourly","seven_day"]}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",settings_path): upgraded=ConfigStore().get()
        self.assertEqual(upgraded["version"],27)
        self.assertTrue(upgraded["story_engine"]["enabled"])
        core=[x for x in upgraded["presentation"]["sequence"] if x!="story_brief"]
        self.assertEqual(core[:4],["station_id","current","hourly","seven_day"])
        self.assertEqual(upgraded["presentation"]["sequence"][2],"story_brief")

    def test_story_classifier_major_modes(self):
        settings,snap,p=fixture()
        self.assertEqual(classify_story(settings,snap,p)["id"],"quiet")

        rainy=copy.deepcopy(p); rainy["hourly"]["precipitation_probability"]=[80]*24; rainy["hourly"]["precipitation"]=[0.05]*24
        self.assertEqual(classify_story(settings,snap,rainy)["id"],"rain")

        hot=copy.deepcopy(p); hot["current"]["apparent_temperature"]=103; hot["hourly"]["apparent_temperature"]=[103]*24
        self.assertEqual(classify_story(settings,snap,hot)["id"],"heat")

        windy=copy.deepcopy(p); windy["current"]["wind_gusts_10m"]=52; windy["hourly"]["wind_gusts_10m"]=[52]*24
        self.assertEqual(classify_story(settings,snap,windy)["id"],"wind")

        smoky=copy.deepcopy(p); smoky["air_quality"]={"aqi":165,"category":"UNHEALTHY","primary_pollutant":"PM2.5"}
        self.assertEqual(classify_story(settings,snap,smoky)["id"],"air_quality")

        severe=copy.deepcopy(snap); severe["alerts"]=[{"event":"Tornado Warning","severity":"Extreme"}]
        self.assertEqual(classify_story(settings,severe,p)["id"],"severe")

    def test_story_composition_has_now_next_later_context(self):
        settings,snap,p=fixture(); p["hourly"]["precipitation_probability"]=[70]*24; p["hourly"]["precipitation"]=[0.04]*24
        story=classify_story(settings,snap,p)
        seq,sections=compose_story_sequence(story,station_id=True,story_brief=True,include_context=True,max_slides=12)
        self.assertEqual(seq[:3],["station_id","story_brief","current"])
        self.assertIn("precipitation",seq)
        self.assertIn("now",sections); self.assertIn("next",sections); self.assertIn("later",sections); self.assertIn("context",sections)
        self.assertLessEqual(len(seq),12)

    def test_adaptive_rundown_and_manual_studio_override(self):
        settings,snap,p=fixture(); p["hourly"]["precipitation_probability"]=[75]*24; p["hourly"]["precipitation"]=[0.05]*24
        snap["locations"]["ruston"]=p
        renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snap))
        adaptive=[x for x,_ in renderer._sequence(settings,snap,dt.datetime.now().timestamp())]
        self.assertIn("story_brief",adaptive); self.assertIn("precipitation",adaptive)
        self.assertLess(adaptive.index("precipitation"),adaptive.index("seven_day"))

        manual=copy.deepcopy(settings); manual["studio"]["sequences"]={"local":["current","seven_day"]}
        manual_seq=[x for x,_ in renderer._sequence(manual,snap,dt.datetime.now().timestamp())]
        self.assertEqual(manual_seq,["current","seven_day"])

    def test_story_hold_stabilizes_ordinary_changes_but_not_official_high_impact_clear(self):
        settings,snap,p=fixture(); renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snap))
        rainy=copy.deepcopy(p); rainy["hourly"]["precipitation_probability"]=[80]*24; rainy["hourly"]["precipitation"]=[0.05]*24
        first=renderer._weather_story(settings,snap,rainy,1000)
        self.assertEqual(first["id"],"rain")
        held=renderer._weather_story(settings,snap,p,1030)
        self.assertEqual(held["id"],"rain"); self.assertTrue(held.get("held"))

        severe_snap=copy.deepcopy(snap); severe_snap["alerts"]=[{"event":"Tornado Warning"}]
        severe=renderer._weather_story(settings,severe_snap,p,2000)
        self.assertEqual(severe["id"],"severe")
        cleared=renderer._weather_story(settings,snap,p,2030)
        self.assertEqual(cleared["id"],"quiet")

    def test_story_brief_renders(self):
        settings,snap,p=fixture(); p["hourly"]["precipitation_probability"]=[65]*24; p["hourly"]["precipitation"]=[0.05]*24
        snap["locations"]["ruston"]=p
        renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snap))
        story=renderer.render_preview("story_brief",location_id="ruston")
        current=renderer.render_preview("current",location_id="ruston")
        self.assertEqual(story.size,(1280,720))
        self.assertNotEqual(story.tobytes(),current.tobytes())


if __name__=="__main__": unittest.main()
