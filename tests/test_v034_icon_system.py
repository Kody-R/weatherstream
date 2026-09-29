from __future__ import annotations

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import ConfigStore, DEFAULT_SETTINGS
from app.iconography import ICON_ROOT, condition_icon, condition_name, metric_icon, alert_icon
from app.renderer import WeatherRenderer


class _RevisionSource:
    def __init__(self, value): self.value=value
    def snapshot_if_changed(self, previous): return (1, None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


def fixture():
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    location={"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings["locations"]=[location]; settings["primary_location_id"]="ruston"
    now=dt.datetime.now().replace(minute=0,second=0,microsecond=0)
    times=[(now+dt.timedelta(hours=i)).isoformat(timespec="minutes") for i in range(16)]
    dates=[(now.date()+dt.timedelta(days=i)).isoformat() for i in range(7)]
    primary={
      "location":location,"fetched_at":dt.datetime.now(dt.timezone.utc).isoformat(),
      "current":{"time":times[0],"temperature_2m":82,"relative_humidity_2m":72,"apparent_temperature":87,"is_day":1,"weather_code":95,"description":"Thunderstorms","cloud_cover":85,"surface_pressure":1010,"wind_speed_10m":14,"wind_gusts_10m":31,"wind_cardinal":"S"},
      "hourly":{"time":times,"temperature_2m":[82+i%4 for i in range(16)],"precipitation_probability":[60]*16,"precipitation":[.05]*16,"weather_code":[95,95,63,61,2,1,0,2]*2,"wind_speed_10m":[14]*16,"wind_gusts_10m":[31]*16,"relative_humidity_2m":[72]*16,"apparent_temperature":[87]*16,"wind_direction_10m":[180]*16,"cloud_cover":[85]*16},
      "daily":{"time":dates,"weather_code":[95,61,2,1,63,2,0],"temperature_2m_max":[87,84,88,91,86,89,92],"temperature_2m_min":[71,69,70,72,68,69,71],"precipitation_probability_max":[70,60,20,10,50,20,10],"precipitation_sum":[.4,.2,0,0,.3,0,0],"sunrise":[f"{d}T06:55" for d in dates],"sunset":[f"{d}T19:03" for d in dates]},
      "nws":{"periods":[],"office":"SHV","error":None},
    }
    snap={"locations":{"ruston":primary},"alerts":[],"alerts_by_location":{"ruston":[]},"storm_guidance":{},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snap


class V034IconSystemTests(unittest.TestCase):
    def test_schema_22_defaults(self):
        self.assertEqual(DEFAULT_SETTINGS["version"],22)
        icons=DEFAULT_SETTINGS["icon_system"]
        self.assertTrue(icons["enabled"])
        self.assertTrue(icons["animation_enabled"])
        self.assertTrue(icons["metric_icons"])
        self.assertTrue(icons["alert_icons"])

    def test_asset_library_is_bundled(self):
        assets=list(ICON_ROOT.rglob("*.png"))
        self.assertGreaterEqual(len(assets),200)
        for path in [
            ICON_ROOT/"conditions"/"hero"/"partly_cloudy_day.png",
            ICON_ROOT/"conditions"/"compact"/"thunderstorm.png",
            ICON_ROOT/"metrics"/"humidity.png",
            ICON_ROOT/"alerts"/"tornado.png",
            ICON_ROOT/"animated"/"hero"/"rain-0.png",
        ]:
            self.assertTrue(path.exists(),str(path))

    def test_resolver_day_night_and_sizes(self):
        self.assertEqual(condition_name(0,is_night=False),"clear_day")
        self.assertEqual(condition_name(0,is_night=True),"clear_night")
        self.assertEqual(condition_name(95,is_night=True),"thunderstorm")
        self.assertEqual(condition_icon(2,pixels=180,is_night=False,animate=False).size,(180,180))
        self.assertEqual(condition_icon(61,pixels=64,is_night=False,animate=False).size,(64,64))
        self.assertEqual(metric_icon("wind",30).size,(30,30))
        self.assertEqual(alert_icon("Tornado Warning",96).size,(96,96))

    def test_current_hourly_and_alert_render_with_icon_system(self):
        settings,snapshot=fixture(); renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snapshot))
        current=renderer.render_preview("current",location_id="ruston")
        hourly=renderer.render_preview("hourly",location_id="ruston")
        alert=renderer.render_preview("alert",test_alert=True,location_id="ruston")
        for image in (current,hourly,alert): self.assertEqual(image.size,(1280,720))
        self.assertNotEqual(current.tobytes(),hourly.tobytes())

    def test_schema_21_upgrade_enables_icon_system_without_changing_sequences(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); settings_path=root/"settings.json"
            settings_path.write_text(json.dumps({"version":21,"presentation":{"sequence":["station_id","current","hourly","seven_day"]},"channels":{"zip_sequence":["current","hourly","seven_day"]}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",settings_path): upgraded=ConfigStore().get()
            self.assertEqual(upgraded["version"],22)
            self.assertTrue(upgraded["icon_system"]["enabled"])
            self.assertEqual(upgraded["presentation"]["sequence"][:4],["station_id","current","hourly","seven_day"])


if __name__=="__main__": unittest.main()
