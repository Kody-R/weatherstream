from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json

from app.config import ConfigStore
from app.history import HistoryStore


class RevisionedSettingsTests(unittest.TestCase):
    def test_snapshot_is_only_copied_after_revision_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch("app.config.CONFIG_DIR", root), patch("app.config.SETTINGS_PATH", root / "settings.json"):
                store = ConfigStore()
                revision, snapshot = store.snapshot_if_changed(None)
                self.assertIsNotNone(snapshot)
                same_revision, unchanged = store.snapshot_if_changed(revision)
                self.assertEqual(same_revision, revision)
                self.assertIsNone(unchanged)
                store.update_general({"station_name": "Revision Test"})
                new_revision, changed = store.snapshot_if_changed(revision)
                self.assertGreater(new_revision, revision)
                self.assertEqual(changed["station_name"], "Revision Test")

    def test_v14_migrates_notification_defaults_through_schema_16(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings_path = root / "settings.json"
            settings_path.write_text(json.dumps({"version": 14, "station_name": "Existing Station"}), encoding="utf-8")
            with patch("app.config.CONFIG_DIR", root), patch("app.config.SETTINGS_PATH", settings_path):
                settings = ConfigStore().get()
            self.assertEqual(settings["version"], 16)
            self.assertEqual(settings["station_name"], "Existing Station")
            self.assertFalse(settings["notifications"]["enabled"])
            self.assertEqual(settings["notifications"]["events"], ["severe", "source", "stream"])

    def test_v15_adds_auto_encoder_device_for_schema_16(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings_path = root / "settings.json"
            settings_path.write_text(json.dumps({"version": 15, "video": {"encoder": "qsv"}}), encoding="utf-8")
            with patch("app.config.CONFIG_DIR", root), patch("app.config.SETTINGS_PATH", settings_path):
                settings = ConfigStore().get()
            self.assertEqual(settings["version"], 16)
            self.assertEqual(settings["video"]["encoder_device"], "auto")


class BatchedHistoryTests(unittest.TestCase):
    def test_batch_insert_updates_count_and_query_caches(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.db")
            now = dt.datetime.now(dt.timezone.utc)
            inserted = store.record_many([
                ("alpha", {"temperature_2m": 70, "surface_pressure": 1010}, now - dt.timedelta(hours=1)),
                ("alpha", {"temperature_2m": 74, "surface_pressure": 1012}, now),
            ])
            self.assertEqual(inserted, 2)
            self.assertEqual(store.status("alpha")["rows"], 2)
            first = store.recent("alpha", 24)
            second = store.recent("alpha", 24)
            self.assertEqual(first, second)
            self.assertTrue(store._recent_cache)
            summary = store.summary("alpha", 24)
            self.assertEqual(summary["high"], 74)
            self.assertEqual(summary["low"], 70)
            self.assertEqual(summary["pressure_trend"], "RISING")

    def test_cleanup_runs_at_most_once_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.db")
            store.record("alpha", {"temperature_2m": 65}, dt.datetime.now(dt.timezone.utc))
            store.cleanup_if_due(90)
            self.assertEqual(store.cleanup_if_due(90), 0)


if __name__ == "__main__":
    unittest.main()
