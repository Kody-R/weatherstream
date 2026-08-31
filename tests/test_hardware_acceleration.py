from __future__ import annotations

from app import hardware
from app.streamer import ChannelWorker
import app.streamer as streamer_mod


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


def test_choose_encoder_uses_explicit_ready_render_node(monkeypatch):
    caps = {
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
    monkeypatch.setattr(hardware, "encoder_capabilities", lambda force=False: caps)
    assert hardware.choose_encoder("qsv", "/dev/dri/renderD129") == ("qsv", "/dev/dri/renderD129", None)
    assert hardware.choose_encoder("vaapi", "/dev/dri/renderD128") == ("vaapi", "/dev/dri/renderD128", None)


def test_failed_hardware_signature_falls_back_to_software(monkeypatch):
    caps = {
        "software": {"available": True, "compiled": True, "devices": []},
        "qsv": {"available": True, "compiled": True, "devices": [{"path": "/dev/dri/renderD129", "available": True}]},
        "vaapi": {"available": False, "compiled": True, "devices": []},
        "nvenc": {"available": False, "compiled": True, "devices": []},
    }
    monkeypatch.setattr(hardware, "encoder_capabilities", lambda force=False: caps)
    enc, device, reason = hardware.choose_encoder("qsv", "/dev/dri/renderD129", ("qsv", "/dev/dri/renderD129"))
    assert enc == "software"
    assert device is None
    assert "previous hardware" in reason


def test_qsv_command_uses_linux_child_device(monkeypatch):
    monkeypatch.setattr(streamer_mod, "choose_encoder", lambda *args, **kwargs: ("qsv", "/dev/dri/renderD129", None))
    worker = ChannelWorker(DummyConfig(), DummyRenderer(), DummyTTS(), "zip-test", "loc", "local")
    cmd = worker._ffmpeg_command(_settings("qsv", "/dev/dri/renderD129"), 7)
    joined = " ".join(cmd)
    assert "qsv=hw,child_device=/dev/dri/renderD129,child_device_type=vaapi" in joined
    assert "qsv=hw:/dev/dri/renderD128" not in joined


def test_vaapi_command_uses_selected_render_node(monkeypatch):
    monkeypatch.setattr(streamer_mod, "choose_encoder", lambda *args, **kwargs: ("vaapi", "/dev/dri/renderD129", None))
    worker = ChannelWorker(DummyConfig(), DummyRenderer(), DummyTTS(), "zip-test", "loc", "local")
    cmd = worker._ffmpeg_command(_settings("vaapi", "/dev/dri/renderD129"), 7)
    joined = " ".join(cmd)
    assert "vaapi=hw:/dev/dri/renderD129" in joined
    assert "-filter_hw_device hw" in joined


def test_render_node_setting_is_restricted():
    assert hardware.normalize_device_setting("auto") == "auto"
    assert hardware.normalize_device_setting("/dev/dri/renderD129") == "/dev/dri/renderD129"
    assert hardware.normalize_device_setting("/dev/sda") == "auto"
