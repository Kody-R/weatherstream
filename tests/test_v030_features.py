from __future__ import annotations

import copy
import datetime as dt
import unittest

from app.config import DEFAULT_SETTINGS
from app.events import WeatherEventManager
from app.guide import channel_specs
from app.network import normalized_regions
from app.studio import active_sequence
from app.renderer import WeatherRenderer


def settings_fixture():
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    settings["locations"]=[
        {"id":"north","postal_code":"71270","name":"Ruston","timezone":"America/Chicago"},
        {"id":"coast","postal_code":"70001","name":"Metairie","timezone":"America/Chicago"},
    ]
    settings["primary_location_id"]="north"
    settings["regions"]={"enabled":True,"items":[
        {"id":"north-la","name":"North Louisiana","enabled":True,"location_ids":["north"],"primary_location_id":"north","callsign":"RWN-N","theme":"local-90s","branding_profile":"default"},
        {"id":"gulf","name":"Gulf Coast","enabled":True,"location_ids":["coast"],"primary_location_id":"coast","callsign":"RWN-G","theme":"classic-blue","branding_profile":"default"},
    ]}
    return settings


class _Config:
    def __init__(self, value): self.value=value
    def get(self): return copy.deepcopy(self.value)


class _Weather:
    def snapshot(self):
        return {"alerts_by_location":{"coast":[{"event":"Tornado Warning","headline":"Tornado Warning for the coast","severity":"Severe"}],"north":[]}}


class _RevisionSource:
    def __init__(self,value): self.value=value
    def snapshot_if_changed(self,previous): return (1,None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


class V030FeatureTests(unittest.TestCase):
    def test_multi_region_lineup_has_scoped_channels_and_event_standbys(self):
        settings=settings_fixture(); specs=channel_specs(settings)
        keys={row["key"] for row in specs}
        self.assertIn("north-la-zip-71270",keys); self.assertIn("gulf-radar",keys)
        self.assertIn("gulf-event-tornado",keys); self.assertIn("north-la-event-heat",keys)
        self.assertEqual(len(normalized_regions(settings)),2)

    def test_event_manager_matches_alerts_by_region(self):
        manager=WeatherEventManager(_Config(settings_fixture()),_Weather())
        self.assertTrue(manager.evaluate("gulf","tornado")["active"])
        self.assertFalse(manager.evaluate("north-la","tornado")["active"])

    def test_studio_schedule_overrides_mode_sequence(self):
        settings=settings_fixture(); settings["studio"]={"enabled":True,"sequences":{"local":["current"]},"schedules":[{"enabled":True,"region_id":"gulf","channel_mode":"local","days":[0],"start":"17:00","end":"22:00","sequence":["bumper:network-update","radar_local"]}]}
        when=dt.datetime(2026,8,31,18,0)  # Monday
        self.assertEqual(active_sequence(settings,"gulf","local",when),["bumper:network-update","radar_local"])
        self.assertEqual(active_sequence(settings,"north-la","local",when),["current"])

    def test_new_graphics_render_without_live_network_sources(self):
        settings=settings_fixture(); settings["video"]={**settings["video"],"width":640,"height":360}
        loc={"id":"coast","postal_code":"70001","name":"Metairie","admin1":"LA","latitude":29.98,"longitude":-90.16,"timezone":"America/Chicago"}
        primary={"location":loc,"current":{"temperature_2m":88,"description":"Partly Cloudy"},"daily":{},"hourly":{}}
        snapshot={"locations":{"coast":primary},"alerts_by_location":{"coast":[{"event":"Tornado Warning","headline":"Test warning","severity":"Severe","areaDesc":"Jefferson Parish"}]},"alerts":[],"storm_guidance_by_location":{},"sources":{}}
        renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snapshot))
        for slide,mode in (("event_summary","event_tornado"),("map_satellite","radar"),("bumper:network-update","local")):
            image=renderer.render_preview(slide,location_id="coast",channel_mode=mode)
            self.assertEqual(image.size,(640,360))


if __name__ == "__main__": unittest.main()
