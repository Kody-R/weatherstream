from __future__ import annotations

import unittest

from app.config import DEFAULT_SETTINGS
from app.guide import channel_specs, generate_xmltv


class TropicalLineupTests(unittest.TestCase):
    def _settings(self):
        settings = {**DEFAULT_SETTINGS}
        location = {"id":"alpha","postal_code":"71270","name":"Ruston","timezone":"America/Chicago"}
        settings["locations"] = [location]
        settings["primary_location_id"] = "alpha"
        return settings

    def test_tropics_channel_remains_in_stable_lineup(self) -> None:
        specs = channel_specs(self._settings())
        tropics = next(item for item in specs if item["key"] == "tropics")
        self.assertEqual(tropics["id"], "rwn.tropics")
        self.assertEqual(tropics["mode"], "tropics")

    def test_xmltv_uses_active_system_names(self) -> None:
        xml = generate_xmltv(self._settings(), tropical_status={"systems":[{"name":"Alice"}]}, hours=6)
        self.assertIn("RWN Tropics Watch — Alice", xml)
        self.assertIn('channel="rwn.tropics"', xml)


if __name__ == "__main__":
    unittest.main()
