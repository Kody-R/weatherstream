from __future__ import annotations

import datetime as dt
import unittest

from app.tropical_logic import evaluate_activation, hurricane_season_active, normalize_storm, outlook_development_max, parse_coordinate


class TropicalLogicTests(unittest.TestCase):
    def test_normalizes_official_nhc_status_fields(self) -> None:
        storm = normalize_storm({
            "id": "al112026", "name": "Alice", "classification": "HU",
            "intensity": 90, "pressure": 970, "latitude": "24.5N",
            "longitude": "86.2W", "movementDir": 315, "movementSpeed": 12,
        })
        self.assertEqual(storm["classification_name"], "Hurricane")
        self.assertEqual(storm["longitude"], -86.2)
        self.assertEqual(storm["intensity_mph"], 104)
        self.assertIsNone(normalize_storm({"id": "ep012026"}))

    def test_gulf_forecast_track_activates_watch(self) -> None:
        snapshot = {"systems": [{"id":"al012026","name":"Alice","latitude":18,"longitude":-76,"track":[[24,-86],[27,-90]]}], "outlook": {}}
        result = evaluate_activation(snapshot, {"latitude":32.5,"longitude":-92.6}, [], {"activation_radius_miles":300,"gulf_development_threshold":40})
        self.assertTrue(result["triggered"])
        self.assertIn("Gulf region", " ".join(result["reasons"]))

    def test_local_tropical_warning_activates_without_a_track(self) -> None:
        result = evaluate_activation({"systems":[],"outlook":{}}, None, [{"event":"Hurricane Warning"}], {})
        self.assertTrue(result["triggered"])
        self.assertEqual(result["local_alerts"], ["Hurricane Warning"])

    def test_gulf_outlook_threshold_and_season_boundaries(self) -> None:
        text = "Northern Gulf: Formation chance through 7 days...medium...40 percent."
        self.assertEqual(outlook_development_max(text), 40)
        result = evaluate_activation({"systems":[],"outlook":{"gulf_mentioned":True,"development_max":40}}, None, [], {"gulf_development_threshold":40})
        self.assertTrue(result["triggered"])
        self.assertTrue(hurricane_season_active(dt.datetime(2026,6,1,tzinfo=dt.timezone.utc)))
        self.assertTrue(hurricane_season_active(dt.datetime(2026,11,30,tzinfo=dt.timezone.utc)))
        self.assertFalse(hurricane_season_active(dt.datetime(2026,12,1,tzinfo=dt.timezone.utc)))

    def test_coordinate_parser_handles_compass_suffixes(self) -> None:
        self.assertEqual(parse_coordinate("19.2S"), -19.2)
        self.assertEqual(parse_coordinate("82.4W", longitude=True), -82.4)


if __name__ == "__main__":
    unittest.main()
