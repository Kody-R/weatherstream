from __future__ import annotations

import unittest
from unittest.mock import patch

from app import hardware
from app.streamer import ChannelWorker


class DummyConfig:
    def get(self):
        return {}


class DummyRenderer:
    pass


class DummyTTS:
    pass


def _settings(encoder="software", device="auto"):
    return {
        "video": {
            "width": 1280,
            "height": 720,
            "render_fps": 5,
            "content_fps": 3,
            "output_fps": 15,
            "hls_segment_seconds": 3,
            "hls_list_size": 10,
            "bitrate": "2000k",
            "encoder_preset": "superfast",
            "encoder": encoder,
            "encoder_device": device,
        },
        "music": {"enabled": False, "volume": 0.3},
        "tts": {"enabled": False, "duck_music": True},
    }


class HardwareAccelerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.caps = {
            "software": {"available": True, "compiled": True, "devices": []},
            "qsv": {"available": True, "compiled": True, "devices": [
                {"path": "/dev/dri/renderD128", "available": True},
                {"path": "/dev/dri/renderD129", "available": True},
            ]},
            "vaapi": {"available": True, "compiled": True, "devices": [
                {"path": "/dev/dri/renderD128", "available": True},
                {"path": "/dev/dri/renderD129", "available": True},
            ]},
            "nvenc": {"available": False, "compiled": True, "devices": []},
        }

    def test_choose_encoder_uses_explicit_ready_render_node(self) -> None:
        with patch.object(hardware, "encoder_capabilities", return_value=self.caps):
            self.assertEqual(hardware.choose_encoder("qsv", "/dev/dri/renderD129"), ("qsv", "/dev/dri/renderD129", None))
            self.assertEqual(hardware.choose_encoder("vaapi", "/dev/dri/renderD128"), ("vaapi", "/dev/dri/renderD128", None))

    def test_failed_hardware_signature_falls_back_to_software(self) -> None:
        with patch.object(hardware, "encoder_capabilities", return_value=self.caps):
            encoder, device, reason = hardware.choose_encoder("qsv", "/dev/dri/renderD129", ("qsv", "/dev/dri/renderD129"))
        self.assertEqual(encoder, "software")
        self.assertIsNone(device)
        self.assertIn("previous hardware", reason)

    def test_qsv_command_uses_linux_child_device(self) -> None:
        with patch("app.streamer.choose_encoder", return_value=("qsv", "/dev/dri/renderD129", None)):
            worker = ChannelWorker(DummyConfig(), DummyRenderer(), DummyTTS(), "zip-test", "loc", "local")
            command = " ".join(worker._ffmpeg_command(_settings("qsv", "/dev/dri/renderD129"), 7))
        self.assertIn("qsv=hw,child_device=/dev/dri/renderD129,child_device_type=vaapi", command)
        self.assertNotIn("qsv=hw:/dev/dri/renderD128", command)

    def test_vaapi_command_uses_selected_render_node(self) -> None:
        with patch("app.streamer.choose_encoder", return_value=("vaapi", "/dev/dri/renderD129", None)):
            worker = ChannelWorker(DummyConfig(), DummyRenderer(), DummyTTS(), "zip-test", "loc", "local")
            command = " ".join(worker._ffmpeg_command(_settings("vaapi", "/dev/dri/renderD129"), 7))
        self.assertIn("vaapi=hw:/dev/dri/renderD129", command)
        self.assertIn("-filter_hw_device hw", command)

    def test_render_node_setting_is_restricted(self) -> None:
        self.assertEqual(hardware.normalize_device_setting("auto"), "auto")
        self.assertEqual(hardware.normalize_device_setting("/dev/dri/renderD129"), "/dev/dri/renderD129")
        self.assertEqual(hardware.normalize_device_setting("/dev/sda"), "auto")


if __name__ == "__main__":
    unittest.main()
