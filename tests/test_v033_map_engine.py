from __future__ import annotations

import copy
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from app.config import ConfigStore, DEFAULT_SETTINGS
from app.radar import RadarManager, OFFICIAL_PRODUCTS
from app.renderer import WeatherRenderer
from app.studio import AVAILABLE_SLIDES


class _RevisionSource:
    def __init__(self, value): self.value = value
    def snapshot_if_changed(self, previous): return (1, None if previous == 1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


class _FakeRadar:
    def __init__(self):
        self.base=Image.new("RGB",(1180,500),"#20394e")
        d=ImageDraw.Draw(self.base)
        for x in range(0,1180,100): d.line((x,0,x,500),fill="#36566c",width=1)
        for y in range(0,500,100): d.line((0,y,1180,y),fill="#36566c",width=1)
        self.frame=Image.new("RGB",(1180,500),"#18374b")
    def resized_map(self, view, width, height, location_id=None): return self.base.resize((width,height))
    def official_product(self, product, location_id=None, copy_image=False):
        ov=Image.new("RGBA",(1180,500),(0,0,0,0)); d=ImageDraw.Draw(ov)
        if product=="spc_day1":
            d.ellipse((250,120,850,390),fill=(220,190,40,100),outline=(255,220,60,255),width=5)
        elif product=="wpc_surface":
            d.line((80,310,350,220,650,300,980,170),fill=(20,130,240,255),width=8)
            d.text((700,80),"H",fill=(40,120,255,255))
            d.text((920,330),"L",fill=(255,60,60,255))
        else:
            d.rectangle((140,120,980,390),fill=(40,180,100,85),outline=(80,240,150,255),width=4)
        return {"image":ov,"source":"NOAA TEST","view":"wide","last_update":1,"last_error":None}
    def snapshot(self, view="local", copy_images=False, location_id=None):
        return {"frames":[{"time":int(dt.datetime.now().timestamp()),"image":self.frame}],"last_error":None}
    def resized_frame(self, view, frame, width, height): return frame["image"].resize((width,height))


def fixture():
    settings=copy.deepcopy(DEFAULT_SETTINGS)
    loc={"id":"ruston","postal_code":"71270","name":"Ruston","admin1":"LA","latitude":32.52,"longitude":-92.64,"timezone":"America/Chicago"}
    settings["locations"]=[loc]; settings["primary_location_id"]="ruston"
    settings["_spc_outlook"]={"day1":{"risk":"SLGT","name":"Slight Risk","rank":3}}
    now=dt.datetime.now(dt.timezone.utc)
    primary={"location":loc,"fetched_at":now.isoformat(),"current":{"temperature_2m":82,"relative_humidity_2m":70,"weather_code":2,"description":"Partly Cloudy","wind_speed_10m":10,"wind_gusts_10m":18,"wind_cardinal":"S"},"hourly":{},"daily":{},"nws":{"periods":[]}}
    alert={"event":"Tornado Warning","headline":"Tornado Warning","areaDesc":"Lincoln Parish","geometry":{"type":"Polygon","coordinates":[[[-92.9,32.4],[-92.4,32.4],[-92.4,32.7],[-92.9,32.7],[-92.9,32.4]]]}}
    snapshot={"locations":{"ruston":primary},"alerts":[alert],"alerts_by_location":{"ruston":[alert]},"storm_guidance":{},"storm_guidance_by_location":{},"location_status":{},"sources":{}}
    return settings,snapshot


class _Resp:
    def __init__(self, data): self.content=data
    def raise_for_status(self): return None


class _Client:
    def __init__(self, data): self.data=data; self.calls=[]
    def get(self,url,params=None): self.calls.append((url,params)); return _Resp(self.data)


class V033MapEngineTests(unittest.TestCase):
    def test_schema_21_defaults_and_studio_palette(self):
        self.assertEqual(DEFAULT_SETTINGS["version"],22)
        self.assertTrue(DEFAULT_SETTINGS["maps"]["engine3"]["enabled"])
        for slide in ("spc_map","spc_hazards","surface_map","qpf_map","hazard_map"):
            self.assertIn(slide,AVAILABLE_SLIDES)
            self.assertIn(slide,DEFAULT_SETTINGS["slides"])

    def test_schema_20_migrates_engine2_to_engine3_and_preserves_custom_radar_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/"settings.json"
            path.write_text(json.dumps({"version":20,"maps":{"engine2":{"enabled":False,"layers":{"radar":False,"alerts":True}}},"channels":{"radar_sequence":["station_id","radar_local","radar_wide"]}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",path): upgraded=ConfigStore().get()
        self.assertEqual(upgraded["version"],22)
        self.assertFalse(upgraded["maps"]["engine3"]["enabled"])
        self.assertFalse(upgraded["maps"]["engine3"]["layers"]["radar"])
        self.assertEqual(upgraded["channels"]["radar_sequence"][0:2],["station_id","radar_local"])
        for slide in ("hazard_map","spc_map","spc_hazards","surface_map","qpf_map"): self.assertIn(slide,upgraded["channels"]["radar_sequence"])

    def test_official_export_uses_web_mercator_viewport(self):
        buf=io.BytesIO(); Image.new("RGBA",(1180,500),(255,0,0,64)).save(buf,"PNG")
        client=_Client(buf.getvalue())
        manager=RadarManager(_RevisionSource(DEFAULT_SETTINGS))
        try:
            image=manager._build_official_overlay(client,OFFICIAL_PRODUCTS["spc_day1"]["url"],"1",{"latitude":32.52,"longitude":-92.64},5,1180,500)
        finally:
            manager.stop()
        self.assertEqual(image.size,(1180,500))
        self.assertEqual(client.calls[0][1]["bboxSR"],"3857")
        self.assertEqual(client.calls[0][1]["imageSR"],"3857")
        self.assertEqual(client.calls[0][1]["layers"],"show:1")

    def test_new_map_products_render(self):
        settings,snapshot=fixture()
        renderer=WeatherRenderer(_RevisionSource(settings),_RevisionSource(snapshot),radar_manager=_FakeRadar())
        baseline=renderer.render_preview("current",location_id="ruston")
        for slide in ("spc_map","spc_hazards","surface_map","qpf_map","hazard_map"):
            with self.subTest(slide=slide):
                image=renderer.render_preview(slide,location_id="ruston")
                self.assertEqual(image.size,(1280,720))
                self.assertNotEqual(image.tobytes(),baseline.tobytes())


if __name__ == "__main__": unittest.main()
