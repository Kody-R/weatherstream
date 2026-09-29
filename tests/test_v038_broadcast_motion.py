from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.broadcast_motion import DESK_TRANSITIONS, resolved_transition, transition_seconds
from app.config import ConfigStore, DEFAULT_SETTINGS
from app.renderer import WeatherRenderer


class _Source:
    def __init__(self, value): self.value=value
    def snapshot_if_changed(self, previous): return (1, None if previous==1 else copy.deepcopy(self.value))
    def get(self): return copy.deepcopy(self.value)


def renderer():
    return WeatherRenderer(_Source(DEFAULT_SETTINGS), _Source({"locations":{},"alerts":[]}))


class V038BroadcastMotionTests(unittest.TestCase):
    def test_schema_26_defaults(self):
        self.assertEqual(DEFAULT_SETTINGS["version"],28)
        motion=DEFAULT_SETTINGS["presentation"]["broadcast_motion"]
        self.assertTrue(motion["enabled"])
        self.assertTrue(motion["auto_transitions"])
        self.assertTrue(motion["desk_transitions"])
        self.assertTrue(motion["emergency_hard_cut"])

    def test_schema_25_migration_preserves_fixed_transition(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); settings_path=root/"settings.json"
            settings_path.write_text(json.dumps({"version":25,"presentation":{"transition":"crt_fade","transition_seconds":1.1}}),encoding="utf-8")
            with patch("app.config.CONFIG_DIR",root),patch("app.config.SETTINGS_PATH",settings_path):
                upgraded=ConfigStore().get()
        self.assertEqual(upgraded["version"],28)
        self.assertEqual(upgraded["presentation"]["transition"],"crt_fade")
        self.assertTrue(upgraded["presentation"]["broadcast_motion"]["enabled"])

    def test_auto_resolver_uses_network_and_desk_signatures(self):
        settings=copy.deepcopy(DEFAULT_SETTINGS)
        self.assertEqual(resolved_transition(settings,None),"rwn_wipe")
        for desk,transition in DESK_TRANSITIONS.items():
            with self.subTest(desk=desk): self.assertEqual(resolved_transition(settings,desk),transition)
        settings["presentation"]["broadcast_motion"]["auto_transitions"]=False
        settings["presentation"]["transition"]="crt_fade"
        self.assertEqual(resolved_transition(settings,"severe"),"crt_fade")

    def test_emergency_motion_duration_is_zero(self):
        self.assertEqual(transition_seconds(DEFAULT_SETTINGS,urgent=True),0.0)

    def test_signature_transitions_render_distinct_midframes(self):
        r=renderer(); a=Image.new("RGB",(320,180),(10,30,80)); b=Image.new("RGB",(320,180),(220,80,30))
        kinds=["rwn_wipe","panel_push","angular_wipe","waterline_wipe","ice_shards","heatwave","smoke_dissolve","radar_sweep"]
        frames=[]
        for kind in kinds:
            with self.subTest(kind=kind):
                out=r._transition(a,b,0.5,kind,settings=copy.deepcopy(DEFAULT_SETTINGS))
                self.assertEqual(out.size,a.size)
                self.assertNotEqual(out.tobytes(),a.tobytes())
                self.assertNotEqual(out.tobytes(),b.tobytes())
                frames.append(hash(out.tobytes()))
        self.assertGreaterEqual(len(set(frames)),7)

    def test_entry_motion_settles_to_exact_frame(self):
        r=renderer(); frame=Image.new("RGB",(320,180),(40,100,160)); settings=copy.deepcopy(DEFAULT_SETTINGS)
        for style in ("broadcast","snap","glide","soft"):
            with self.subTest(style=style):
                early=r._apply_entry_motion(frame,0.2,style,settings)
                final=r._apply_entry_motion(frame,1.0,style,settings)
                self.assertNotEqual(early.tobytes(),frame.tobytes())
                self.assertEqual(final.tobytes(),frame.tobytes())

if __name__ == "__main__": unittest.main()
