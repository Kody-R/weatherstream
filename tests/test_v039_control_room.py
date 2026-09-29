import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import DEFAULT_SETTINGS, ConfigStore
from app.control_room import source_health


class V039ControlRoomTests(unittest.TestCase):
    def test_schema_27_defaults(self):
        self.assertEqual(DEFAULT_SETTINGS["version"], 27)
        control=DEFAULT_SETTINGS["studio"]["control_room"]
        self.assertTrue(control["safe_area"])
        self.assertEqual(control["auto_refresh_seconds"], 3)
        self.assertTrue(control["show_thumbnails"])
        self.assertNotIn("manual_takeovers", DEFAULT_SETTINGS["studio"])

    def test_schema_26_migrates_control_room_without_replacing_rundown(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/"settings.json"
            path.write_text(json.dumps({"version":26,"studio":{"sequences":{"local":["current","hourly","seven_day"]}}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",path):
                upgraded=ConfigStore().get()
        self.assertEqual(upgraded["version"],27)
        self.assertEqual(upgraded["studio"]["sequences"]["local"],["current","hourly","seven_day"])
        self.assertTrue(upgraded["studio"]["control_room"]["show_source_health"])

    def test_source_health_prioritizes_errors_and_stale_data(self):
        now=time.time()
        rows=source_health({
            "radar":{"last_success":now-60},
            "nws_alerts":{"last_success":now-3600},
            "satellite":{"last_error":"offline"},
            "air_quality":{"enabled":False},
        },now=now)
        states={x["id"]:x["state"] for x in rows}
        self.assertEqual(states["radar"],"HEALTHY")
        self.assertEqual(states["nws_alerts"],"STALE")
        self.assertEqual(states["satellite"],"ERROR")
        self.assertEqual(states["air_quality"],"DISABLED")
        self.assertEqual(rows[0]["id"],"satellite")

    def test_source_health_marks_cached_fallback(self):
        now=time.time()
        rows=source_health({"radar":{"last_success":now-120,"last_error":"upstream unavailable"}},now=now)
        self.assertEqual(rows[0]["state"],"CACHED")

if __name__ == '__main__': unittest.main()
