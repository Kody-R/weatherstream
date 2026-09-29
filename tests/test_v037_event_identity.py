from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ConfigStore, DEFAULT_SETTINGS
from app.event_identity import EVENT_IDENTITIES, identity_key
from app.renderer import WeatherRenderer


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
        "current":{"temperature_2m":91,"apparent_temperature":101,"relative_humidity_2m":61,"dewpoint_f":72,"weather_code":2,"description":"Partly Cloudy","wind_speed_10m":14,"wind_gusts_10m":27,"wind_cardinal":"S","surface_pressure":1009},
        "hourly":{"time":times,"temperature_2m":[91]*24,"apparent_temperature":[101]*24,"precipitation_probability":[55]*24,"precipitation":[0.04]*24,"weather_code":[2]*24,"wind_speed_10m":[14]*24,"wind_gusts_10m":[27]*24,"relative_humidity_2m":[61]*24},
        "daily":{"time":dates,"temperature_2m_max":[96]*7,"temperature_2m_min":[75]*7,"precipitation_probability_max":[60]*7,"precipitation_sum":[0.2]*7,"weather_code":[2]*7},
        "nws":{"periods":[],"office":"SHV"},
        "air_quality":{"aqi":88,"category":"MODERATE","primary_pollutant":"PM2.5"},
        "rivers":{"gauges":[{"name":"TEST RIVER","gage_height_ft":12.1}]},
    }
    snap={"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{"hourly":{}},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snap


class V037EventIdentityTests(unittest.TestCase):
    def test_schema_25_defaults(self):
        self.assertEqual(DEFAULT_SETTINGS["version"],27)
        self.assertTrue(DEFAULT_SETTINGS["event_identity"]["enabled"])
        self.assertEqual(set(DEFAULT_SETTINGS["event_identity"]["desks"]),{"severe","flood","winter","heat","wildfire","tropical"})
        self.assertEqual(set(EVENT_IDENTITIES),{"severe","flood","winter","heat","wildfire","tropical"})

    def test_schema_24_migration_preserves_event_sequences(self):
        custom=["event_summary","current","nws_forecast"]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); settings_path=root/"settings.json"
            settings_path.write_text(json.dumps({"version":24,"theme":"cable-gold","event_channels":{"sequences":{"heat":custom}}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",settings_path): upgraded=ConfigStore().get()
        self.assertEqual(upgraded["version"],27)
        self.assertEqual(upgraded["theme"],"cable-gold")
        self.assertEqual(upgraded["event_channels"]["sequences"]["heat"],custom)
        self.assertTrue(upgraded["event_identity"]["desks"]["heat"])

    def test_identity_resolves_only_specialty_channels(self):
        settings,_=fixture()
        settings["_channel_mode"]="local"
        self.assertIsNone(identity_key(settings))
        expected={"severe":"severe","event_tornado":"severe","event_flood":"flood","event_winter":"winter","event_heat":"heat","event_wildfire":"wildfire","tropics":"tropical"}
        for mode,key in expected.items():
            with self.subTest(mode=mode):
                settings["_channel_mode"]=mode
                self.assertEqual(identity_key(settings),key)

    def test_desks_have_distinct_rendered_current_conditions(self):
        settings,snap=fixture(); renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snap))
        modes=["severe","event_flood","event_winter","event_heat","event_wildfire","tropics"]
        fingerprints=[]
        for mode in modes:
            with self.subTest(mode=mode):
                image=renderer.render_preview("current",location_id="ruston",channel_mode=mode)
                self.assertEqual(image.size,(1280,720))
                fingerprints.append(hash(image.tobytes()))
        self.assertEqual(len(set(fingerprints)),len(modes))

    def test_local_current_keeps_base_theme(self):
        settings,snap=fixture(); renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snap))
        local=renderer.render_preview("current",location_id="ruston",channel_mode="local")
        severe=renderer.render_preview("current",location_id="ruston",channel_mode="severe")
        self.assertNotEqual(local.tobytes(),severe.tobytes())


if __name__=="__main__": unittest.main()
