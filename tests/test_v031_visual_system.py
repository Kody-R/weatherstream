from __future__ import annotations

import copy
import datetime as dt
import unittest

from app.config import DEFAULT_SETTINGS
from app.renderer import WeatherRenderer


class _RevisionSource:
    def __init__(self, value):
        self.value = value

    def snapshot_if_changed(self, previous):
        return (1, None if previous == 1 else copy.deepcopy(self.value))

    def get(self):
        return copy.deepcopy(self.value)


def _fixture(width: int = 1280, height: int = 720):
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    location = {
        "id": "ruston", "postal_code": "71270", "name": "Ruston", "admin1": "LA",
        "latitude": 32.52, "longitude": -92.64, "timezone": "America/Chicago",
    }
    settings["locations"] = [location]
    settings["primary_location_id"] = "ruston"
    settings["video"]["width"] = width
    settings["video"]["height"] = height
    now = dt.datetime.now(dt.timezone.utc)
    times = [(now + dt.timedelta(hours=i)).replace(tzinfo=None).isoformat(timespec="minutes") for i in range(12)]
    dates = [(now.date() + dt.timedelta(days=i)).isoformat() for i in range(7)]
    primary = {
        "location": location,
        "fetched_at": now.isoformat(),
        "current": {
            "time": times[0], "temperature_2m": 82, "relative_humidity_2m": 70,
            "apparent_temperature": 86, "is_day": 1, "weather_code": 2,
            "description": "Partly Cloudy", "cloud_cover": 45, "surface_pressure": 1012,
            "wind_speed_10m": 9, "wind_gusts_10m": 20, "wind_cardinal": "S",
        },
        "hourly": {
            "time": times, "temperature_2m": [82,84,85,86,84,81,79,77,75,74,73,72],
            "precipitation_probability": [10,10,20,35,55,65,45,25,20,15,10,10],
            "precipitation": [0,0,0,.01,.08,.10,.03,0,0,0,0,0],
            "weather_code": [2,2,3,61,95,63,61,3,2,1,0,0],
            "wind_speed_10m": [9,10,11,12,13,14,12,10,9,8,7,7],
            "wind_gusts_10m": [14,16,18,22,29,32,27,22,18,15,14,13],
            "wind_direction_10m": [170,175,180,185,190,195,200,205,210,215,220,225],
            "relative_humidity_2m": [70,68,66,65,69,76,80,84,87,89,90,91],
            "apparent_temperature": [86,87,88,89,87,84,82,80,78,77,76,75],
            "cloud_cover": [45,50,65,75,90,85,70,55,45,30,20,10],
        },
        "daily": {
            "time": dates, "weather_code": [95,61,2,1,63,2,0],
            "temperature_2m_max": [87,84,88,91,86,89,92],
            "temperature_2m_min": [71,69,70,72,68,69,71],
            "precipitation_probability_max": [65,70,20,10,60,25,10],
            "precipitation_sum": [.25,.35,0,0,.4,.02,0],
            "sunrise": [f"{d}T06:55" for d in dates], "sunset": [f"{d}T19:03" for d in dates],
        },
        "nws": {"periods": [], "office": "SHV", "error": None},
    }
    snapshot = {
        "locations": {"ruston": primary}, "alerts": [], "alerts_by_location": {"ruston": []},
        "storm_guidance": {}, "storm_guidance_by_location": {}, "location_status": {}, "sources": {},
    }
    return settings, snapshot


class V031VisualSystemTests(unittest.TestCase):
    def test_visual_system_defaults_survive_schema_21(self):
        visual = DEFAULT_SETTINGS["presentation"]["visual_system"]
        self.assertEqual(DEFAULT_SETTINGS["version"], 22)
        self.assertTrue(visual["enabled"])
        self.assertEqual(visual["footer_mode"], "data_ribbon")

    def test_logical_canvas_scales_to_1080p(self):
        settings, snapshot = _fixture(1920, 1080)
        renderer = WeatherRenderer(_RevisionSource(settings), _RevisionSource(snapshot))
        image = renderer.render_preview("current", location_id="ruston")
        self.assertEqual(image.size, (1920, 1080))

    def test_redesigned_forecast_slides_render(self):
        settings, snapshot = _fixture()
        renderer = WeatherRenderer(_RevisionSource(settings), _RevisionSource(snapshot))
        for slide in ("hourly", "precipitation", "seven_day"):
            with self.subTest(slide=slide):
                self.assertEqual(renderer.render_preview(slide, location_id="ruston").size, (1280, 720))


if __name__ == "__main__":
    unittest.main()
