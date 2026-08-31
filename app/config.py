from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("WEATHERSTREAM_CONFIG", "/config"))
SETTINGS_PATH = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 15,
    "station_name": "Roller Weather Network",
    "station_callsign": "RWN",
    "station_slogan": "Local Weather • Radar • Alerts • 24 Hours",
    "service_area": "",
    "public_base_url": "",
    "theme": "local-90s",
    "primary_location_id": None,
    "locations": [],
    "branding": {
        "logo_enabled": True,
        "use_builtin_logo": True,
        "logo_position": "station_id_only",
        "logo_max_width": 260,
    },
    "channels": {
        "per_zip_enabled": True,
        "radar_enabled": True,
        "severe_enabled": True,
        "zip_sequence": [
            "station_id", "current", "condition_focus", "today", "nws_forecast",
            "temperature_trend", "hourly", "precipitation", "storm_outlook",
            "spc_outlook", "seven_day", "weather_history", "almanac"
        ],
        "radar_sequence": [
            "station_id", "radar_local", "radar_regional", "regional_map",
            "radar_wide", "storm_outlook", "spc_outlook"
        ],
        "severe_idle_sequence": [
            "station_id", "current", "spc_outlook", "storm_outlook",
            "radar_local", "regional_map", "radar_regional"
        ],
        "max_zip_channels": 12,
        # Fresh on-demand releases default to on-demand encoding. Upgrades from
        # v0.2.0 preserve their prior always-on behavior until changed in Admin.
        "streaming_mode": "on_demand",
        "idle_timeout_seconds": 90,
        "startup_timeout_seconds": 12,
        "severe_auto_start": True,
        "lineup": [],
        "overrides": {},
    },
    "maps": {
        "auto_city_labels": True,
        "city_min_population": 5000,
        "city_max_labels": 10,
        "city_radius_miles": 180,
        "regional_map_enabled": True,
        "regional_map_view": "regional",
    },
    "storm_guidance": {
        "enabled": True,
        "refresh_seconds": 900,
    },
    "spc": {
        "enabled": True,
        "refresh_seconds": 900,
        "minimum_smart_risk": "MRGL",
    },
    "history": {
        "enabled": True,
        "retention_days": 90,
    },
    "smart_programming": {
        "enabled": True,
        "rain_threshold": 20,
        "storm_threshold": 15,
        "heat_threshold": 95,
        "cold_threshold": 32,
    },
    "dayparts": {
        "enabled": True,
        "morning_start": 5,
        "daytime_start": 10,
        "evening_start": 17,
        "overnight_start": 22,
        "sequences": {
            "morning": ["station_id", "current", "condition_focus", "today", "temperature_trend", "hourly", "radar_local", "regional_map", "seven_day", "weather_history", "almanac"],
            "daytime": ["station_id", "current", "condition_focus", "today", "temperature_trend", "hourly", "precipitation", "storm_outlook", "spc_outlook", "radar_local", "nws_forecast", "regional_map", "seven_day", "weather_history"],
            "evening": ["station_id", "current", "condition_focus", "nws_forecast", "hourly", "precipitation", "radar_local", "seven_day", "regional_map", "weather_history", "almanac"],
            "overnight": ["station_id", "current", "condition_focus", "nws_forecast", "radar_local", "seven_day", "regional_map", "weather_history", "almanac"],
        },
    },
    "cache": {
        "retention_hours": 48,
        "auto_cleanup": True,
    },
    "weather_refresh_seconds": 600,
    "alert_refresh_seconds": 60,
    "nws_user_agent": "WeatherStream/0.2.5 (Roller Weather Network local weather display)",
    "radar": {
        "enabled": True,
        "frame_count": 8,
        "frame_seconds": 0.8,
        "refresh_seconds": 300,
        "opacity": 0.82,
        "contrast": 1.25,
        "show_boundaries": True,
        "show_city_markers": True,
        "show_range_rings": True,
        "range_rings_miles": [25, 50, 100],
        "sweep_enabled": True,
        "sweep_seconds": 6.0,
        "views": {
            "local": {"enabled": True, "zoom": 7},
            "regional": {"enabled": True, "zoom": 6},
            "wide": {"enabled": True, "zoom": 5},
        },
    },
    "alerts": {
        "show_polygons": True,
        "takeover_enabled": True,
        "takeover_min_severity": "Severe",
        "ticker_takeover": True,
        "chime_enabled": True,
        "chime_volume": 0.65,
        "takeover_sequence": ["alert", "alert_radar", "current", "nws_forecast", "alert_radar"],
    },
    "music": {
        "enabled": True,
        "volume": 0.30,
        "shuffle": True,
    },
    "tts": {
        "enabled": False,
        "provider": "piper",
        "voice": "en_US-lessac-medium",
        "auto_download_voice": True,
        "local_on_8s": True,
        "severe_alerts": True,
        "volume": 0.92,
        "speed": 1.0,
        "duck_music": True,
        "cache_items": 64,
    },
    "video": {
        "width": 1280,
        "height": 720,
        "render_fps": 5,
        "content_fps": 3,
        "output_fps": 15,
        "encoder_preset": "superfast",
        "bitrate": "2000k",
        "hls_segment_seconds": 3,
        "hls_list_size": 10,
        "preview_interval_seconds": 5,
        "encoder": "software",
    },
    "performance": {
        "mode": "adaptive",
        "stall_recovery_enabled": True,
        "stall_seconds": 15,
        "adaptive_bad_seconds": 18,
        "adaptive_recover_seconds": 120,
        "adaptive_disable_retro": True,
        "adaptive_transition": "cut",
        "adaptive_content_fps": 2,
    },
    "custom_profiles": {},
    "notifications": {
        "enabled": False,
        "webhook_url": "",
        "events": ["severe", "source", "stream"],
        "minimum_interval_seconds": 30,
        "allow_private_targets": False,
    },
    "presentation": {
        "transition": "crossfade",
        "transition_seconds": 0.75,
        "show_station_id": True,
        "station_id_seconds": 6,
        "show_slide_labels": True,
        "background_motion": True,
        "retro_effects": {
            "enabled": False,
            "scanlines": 0.14,
            "noise": 0.025,
            "bloom": 0.10,
            "soft_edges": 0.12,
            "color_bleed_px": 1,
            "horizontal_jitter_px": 1,
        },
        "scheduled_updates": {
            "enabled": False,
            "minute_marks": [8, 18, 28, 38, 48, 58],
            # In v0.2.2.1 this is a trigger grace period, not the duration of the
            # Local on the 8s block. Once started, the block runs every phase to
            # completion unless a qualifying severe-weather takeover preempts it.
            "window_seconds": 120,
            "intro_enabled": True,
            "phase_lead_seconds": 0.8,
            "phase_tail_seconds": 1.0,
            "tts_wait_seconds": 15,
            "max_phase_seconds": 75,
            "sequence": ["current", "today", "hourly", "radar_local", "seven_day"],
        },
        "sequence": [
            "station_id", "current", "condition_focus", "today", "nws_forecast", "temperature_trend", "hourly", "precipitation",
            "storm_outlook", "spc_outlook", "radar_local", "seven_day", "regional_map", "regional", "radar_regional", "weather_history", "almanac", "radar_wide",
        ],
    },
    "slides": {
        "station_id": 6,
        "current": 12,
        "today": 12,
        "nws_forecast": 16,
        "hourly": 15,
        "precipitation": 12,
        "temperature_trend": 14,
        "storm_outlook": 14,
        "regional_map": 16,
        "seven_day": 15,
        "regional": 12,
        "almanac": 12,
        "radar": 16,
        "radar_local": 16,
        "radar_regional": 16,
        "radar_wide": 16,
        "alert": 14,
        "alert_radar": 18,
        "condition_focus": 10,
        "weather_history": 14,
        "spc_outlook": 13,
    },
}


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in incoming.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except Exception:
        return default


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except Exception:
        return default


class ConfigStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._revision = 0
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_PATH.exists():
            self._settings = copy.deepcopy(DEFAULT_SETTINGS)
            self._save_locked()
        else:
            self._settings = self._load()
            self._save_locked()

    def _load(self) -> dict[str, Any]:
        try:
            with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
            previous_version = int(raw.get("version", 1) or 1)
            merged = _deep_merge(DEFAULT_SETTINGS, raw)

            # v0.1.2 used a single radar zoom and a single radar slide.
            if previous_version < 4:
                old_radar = raw.get("radar", {}) if isinstance(raw.get("radar"), dict) else {}
                if "zoom" in old_radar:
                    old_zoom = _clamp_int(old_radar.get("zoom"), 3, 7, 6)
                    merged["radar"]["views"]["local"]["zoom"] = old_zoom
                    merged["radar"]["views"]["regional"]["zoom"] = max(3, old_zoom - 1)
                    merged["radar"]["views"]["wide"]["zoom"] = max(3, old_zoom - 2)

                old_sequence = (raw.get("presentation") or {}).get("sequence")
                if isinstance(old_sequence, list) and old_sequence:
                    expanded: list[str] = []
                    for item in old_sequence:
                        if item == "radar":
                            expanded.extend(["radar_local", "radar_regional", "radar_wide"])
                        else:
                            expanded.append(item)
                    merged["presentation"]["sequence"] = expanded

                old_radar_duration = (raw.get("slides") or {}).get("radar")
                if old_radar_duration is not None:
                    duration = _clamp_int(old_radar_duration, 3, 60, 16)
                    for key in ("radar_local", "radar_regional", "radar_wide"):
                        merged["slides"][key] = duration

            # v0.1.4 inserts the new forecast/precipitation slides without replacing a
            # user's custom order. Fresh installs get the expanded default sequence.
            if previous_version < 5:
                old_sequence = (raw.get("presentation") or {}).get("sequence")
                if isinstance(old_sequence, list) and old_sequence:
                    seq = list(merged["presentation"]["sequence"])
                    if "nws_forecast" not in seq:
                        insert_at = seq.index("today") + 1 if "today" in seq else min(3, len(seq))
                        seq.insert(insert_at, "nws_forecast")
                    if "precipitation" not in seq:
                        insert_at = seq.index("hourly") + 1 if "hourly" in seq else min(5, len(seq))
                        seq.insert(insert_at, "precipitation")
                    merged["presentation"]["sequence"] = seq

            if previous_version < 7:
                old_sequence = (raw.get("presentation") or {}).get("sequence")
                if isinstance(old_sequence, list) and old_sequence:
                    seq = list(merged["presentation"]["sequence"])
                    if "temperature_trend" not in seq:
                        insert_at = seq.index("hourly") if "hourly" in seq else min(5, len(seq))
                        seq.insert(insert_at, "temperature_trend")
                    if "storm_outlook" not in seq:
                        insert_at = seq.index("radar_local") if "radar_local" in seq else min(8, len(seq))
                        seq.insert(insert_at, "storm_outlook")
                    if "regional_map" not in seq:
                        insert_at = seq.index("regional") if "regional" in seq else min(10, len(seq))
                        seq.insert(insert_at, "regional_map")
                    merged["presentation"]["sequence"] = seq


            if previous_version < 8:
                # Carry v0.1.6.1 settings forward while adopting RWN defaults only when
                # the old untouched WeatherStream defaults were still in use.
                if str(raw.get("station_name") or "").strip() in {"", "WeatherStream Local"}:
                    merged["station_name"] = "Roller Weather Network"
                if not str(raw.get("station_callsign") or "").strip():
                    merged["station_callsign"] = "RWN"
                if str(raw.get("station_slogan") or "").strip() in {"", "Your Local Weather Source"}:
                    merged["station_slogan"] = "Local Weather • Radar • Alerts • 24 Hours"
                old_sequence = (raw.get("presentation") or {}).get("sequence")
                if isinstance(old_sequence, list) and old_sequence:
                    seq = list(merged["presentation"]["sequence"])
                    additions = [("condition_focus", "today"), ("spc_outlook", "radar_local"), ("weather_history", "almanac")]
                    for item, before in additions:
                        if item not in seq:
                            idx = seq.index(before) if before in seq else len(seq)
                            seq.insert(idx, item)
                    merged["presentation"]["sequence"] = seq

            if previous_version < 9:
                # v0.1.8 introduces automatic one-channel-per-ZIP output plus shared Radar/Severe channels.
                merged["channels"] = _deep_merge(DEFAULT_SETTINGS["channels"], raw.get("channels") or {})

            if previous_version < 10:
                # v0.1.8.1 is a realtime-performance migration. Preserve explicit custom
                # video tuning, but move untouched v0.1.8 defaults to the lighter profile.
                old_video = raw.get("video") if isinstance(raw.get("video"), dict) else {}
                compatibility_defaults = {
                    "render_fps": 10, "output_fps": 30, "bitrate": "2500k",
                    "hls_segment_seconds": 2, "hls_list_size": 6,
                }
                for key, old_default in compatibility_defaults.items():
                    if key not in old_video or old_video.get(key) == old_default:
                        merged["video"][key] = DEFAULT_SETTINGS["video"][key]
                for key in ("content_fps", "encoder_preset", "preview_interval_seconds"):
                    if key not in old_video:
                        merged["video"][key] = DEFAULT_SETTINGS["video"][key]

            if previous_version < 11:
                merged["channels"] = _deep_merge(DEFAULT_SETTINGS["channels"], raw.get("channels") or {})
                merged["performance"] = _deep_merge(DEFAULT_SETTINGS["performance"], raw.get("performance") or {})
                merged["custom_profiles"] = raw.get("custom_profiles") if isinstance(raw.get("custom_profiles"), dict) else {}
                old_video = raw.get("video") if isinstance(raw.get("video"), dict) else {}
                if "encoder" not in old_video:
                    merged["video"]["encoder"] = "software"

            if previous_version < 12:
                # The prior release introduced optional on-demand channel encoding. Existing
                # v0.2.0 installations retain always-on behavior until the user
                # explicitly selects On Demand in the Channel Lineup page.
                old_channels = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
                merged["channels"] = _deep_merge(DEFAULT_SETTINGS["channels"], old_channels)
                if "streaming_mode" not in old_channels:
                    merged["channels"]["streaming_mode"] = "always_on"

            if previous_version < 13:
                # v0.2.2 adds optional Piper narration. It is disabled on upgrade
                # so existing channels keep their exact audio behavior until enabled.
                merged["tts"] = _deep_merge(DEFAULT_SETTINGS["tts"], raw.get("tts") or {})

            if previous_version < 14:
                # v0.2.2.1 turns Local on the 8s into a real programming block.
                # The old default included a generic station ID and had no hourly
                # phase. Replace only that untouched default; custom phase choices
                # are retained where they map to a supported Local on the 8s phase.
                old_scheduled = ((raw.get("presentation") or {}).get("scheduled_updates") or {})
                old_seq = old_scheduled.get("sequence") if isinstance(old_scheduled, dict) else None
                if old_seq == ["station_id", "current", "radar_local", "today", "seven_day"]:
                    merged["presentation"]["scheduled_updates"]["sequence"] = copy.deepcopy(DEFAULT_SETTINGS["presentation"]["scheduled_updates"]["sequence"])

            if previous_version < 15:
                merged["notifications"] = _deep_merge(DEFAULT_SETTINGS["notifications"], raw.get("notifications") or {})

            merged["version"] = 15
            return merged
        except Exception:
            return copy.deepcopy(DEFAULT_SETTINGS)

    def _save_locked(self) -> None:
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._settings, fh, indent=2)
        tmp.replace(SETTINGS_PATH)
        self._revision += 1

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._settings)

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def snapshot_if_changed(self, previous_revision: int | None = None) -> tuple[int, dict[str, Any] | None]:
        """Return one isolated settings snapshot only when the revision changed."""
        with self._lock:
            revision = self._revision
            if previous_revision == revision:
                return revision, None
            return revision, copy.deepcopy(self._settings)

    def reload(self) -> dict[str, Any]:
        with self._lock:
            self._settings = self._load()
            self._save_locked()
            return copy.deepcopy(self._settings)

    def replace(self, settings: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(settings, dict):
            raise ValueError("settings payload must be an object")
        with self._lock:
            self._settings = _deep_merge(DEFAULT_SETTINGS, settings)
            self._settings["version"] = 15
            self._save_locked()
        return self.update_general({k:v for k,v in self._settings.items() if k != "locations"})

    def update_general(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "station_name", "station_callsign", "station_slogan", "service_area",
            "public_base_url", "theme", "weather_refresh_seconds",
            "alert_refresh_seconds", "nws_user_agent", "music", "radar",
            "alerts", "presentation", "slides", "branding", "maps", "storm_guidance",
            "spc", "history", "smart_programming", "dayparts", "cache", "channels", "video",
            "performance", "custom_profiles", "tts", "notifications",
        }
        with self._lock:
            for key in allowed:
                if key not in payload:
                    continue
                if key in {"music", "radar", "alerts", "presentation", "slides", "branding", "maps", "storm_guidance", "spc", "history", "smart_programming", "dayparts", "cache", "channels", "video", "performance", "custom_profiles", "tts", "notifications"} and isinstance(payload[key], dict):
                    self._settings[key] = _deep_merge(self._settings[key], payload[key])
                else:
                    self._settings[key] = payload[key]

            self._settings["version"] = 15
            self._settings["station_name"] = str(self._settings.get("station_name") or "Roller Weather Network")[:40]
            self._settings["station_callsign"] = str(self._settings.get("station_callsign") or "")[:12]
            self._settings["station_slogan"] = str(self._settings.get("station_slogan") or "")[:64]
            self._settings["service_area"] = str(self._settings.get("service_area") or "")[:64]
            self._settings["theme"] = self._settings["theme"] if self._settings["theme"] in {"classic-blue", "local-90s", "retro-2000", "terminal-80s", "cable-gold"} else "local-90s"
            self._settings["weather_refresh_seconds"] = max(120, int(self._settings["weather_refresh_seconds"]))
            self._settings["alert_refresh_seconds"] = max(30, int(self._settings["alert_refresh_seconds"]))
            self._settings["music"]["volume"] = _clamp_float(self._settings["music"].get("volume"), 0.0, 1.0, 0.30)

            tts = _deep_merge(DEFAULT_SETTINGS["tts"], self._settings.get("tts") or {})
            tts["enabled"] = bool(tts.get("enabled", False))
            tts["provider"] = "piper"
            voice = str(tts.get("voice") or "en_US-lessac-medium").strip()[:96]
            if not voice or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in voice):
                voice = "en_US-lessac-medium"
            tts["voice"] = voice
            tts["auto_download_voice"] = bool(tts.get("auto_download_voice", True))
            tts["local_on_8s"] = bool(tts.get("local_on_8s", True))
            tts["severe_alerts"] = bool(tts.get("severe_alerts", True))
            tts["volume"] = _clamp_float(tts.get("volume"), 0.0, 1.0, 0.92)
            tts["speed"] = _clamp_float(tts.get("speed"), 0.70, 1.35, 1.0)
            tts["duck_music"] = bool(tts.get("duck_music", True))
            tts["cache_items"] = _clamp_int(tts.get("cache_items"), 8, 256, 64)
            self._settings["tts"] = tts

            video = _deep_merge(DEFAULT_SETTINGS["video"], self._settings.get("video") or {})
            video["width"] = _clamp_int(video.get("width"), 640, 1920, 1280)
            video["height"] = _clamp_int(video.get("height"), 360, 1080, 720)
            video["render_fps"] = _clamp_int(video.get("render_fps"), 2, 15, 5)
            video["content_fps"] = _clamp_int(video.get("content_fps"), 1, video["render_fps"], 3)
            video["output_fps"] = _clamp_int(video.get("output_fps"), video["render_fps"], 30, 15)
            video["hls_segment_seconds"] = _clamp_int(video.get("hls_segment_seconds"), 2, 10, 3)
            video["hls_list_size"] = _clamp_int(video.get("hls_list_size"), 4, 30, 10)
            video["preview_interval_seconds"] = _clamp_int(video.get("preview_interval_seconds"), 1, 30, 5)
            if video.get("encoder_preset") not in {"ultrafast", "superfast", "veryfast", "faster", "fast"}:
                video["encoder_preset"] = "superfast"
            bitrate = str(video.get("bitrate") or "2000k").strip().lower()
            if not bitrate.endswith("k") or not bitrate[:-1].isdigit(): bitrate = "2000k"
            video["bitrate"] = bitrate
            if video.get("encoder") not in {"auto", "software", "nvenc", "qsv", "vaapi"}:
                video["encoder"] = "software"
            self._settings["video"] = video

            radar = self._settings["radar"]
            radar["frame_count"] = _clamp_int(radar.get("frame_count"), 3, 12, 8)
            radar["frame_seconds"] = _clamp_float(radar.get("frame_seconds"), 0.25, 3.0, 0.8)
            radar["refresh_seconds"] = max(120, int(radar.get("refresh_seconds", 300)))
            radar["opacity"] = _clamp_float(radar.get("opacity"), 0.10, 1.0, 0.82)
            radar["contrast"] = _clamp_float(radar.get("contrast"), 0.50, 2.50, 1.25)
            radar["sweep_seconds"] = _clamp_float(radar.get("sweep_seconds"), 2.0, 20.0, 6.0)
            rings = radar.get("range_rings_miles") or [25, 50, 100]
            clean_rings = []
            for value in rings if isinstance(rings, list) else [25, 50, 100]:
                ring = _clamp_int(value, 5, 500, 25)
                if ring not in clean_rings:
                    clean_rings.append(ring)
            radar["range_rings_miles"] = sorted(clean_rings)[:5] or [25, 50, 100]
            if not isinstance(radar.get("views"), dict):
                radar["views"] = copy.deepcopy(DEFAULT_SETTINGS["radar"]["views"])
            for name, default in DEFAULT_SETTINGS["radar"]["views"].items():
                if name not in radar["views"] or not isinstance(radar["views"][name], dict):
                    radar["views"][name] = copy.deepcopy(default)
                radar["views"][name]["enabled"] = bool(radar["views"][name].get("enabled", True))
                radar["views"][name]["zoom"] = _clamp_int(radar["views"][name].get("zoom"), 3, 7, default["zoom"])

            alerts = self._settings["alerts"]
            alerts["chime_volume"] = _clamp_float(alerts.get("chime_volume"), 0.0, 1.0, 0.65)
            if alerts.get("takeover_min_severity") not in {"Extreme", "Severe", "Moderate", "Minor", "Unknown"}:
                alerts["takeover_min_severity"] = "Severe"
            valid_takeover = {"alert", "alert_radar", "current", "nws_forecast", "radar_local", "today", "hourly"}
            takeover_seq = [x for x in (alerts.get("takeover_sequence") or []) if x in valid_takeover]
            alerts["takeover_sequence"] = takeover_seq or copy.deepcopy(DEFAULT_SETTINGS["alerts"]["takeover_sequence"])

            branding = self._settings.get("branding") or {}
            branding["logo_enabled"] = bool(branding.get("logo_enabled", True))
            branding["use_builtin_logo"] = bool(branding.get("use_builtin_logo", True))
            if branding.get("logo_position") not in {"station_id_only", "top_left", "top_right", "all"}:
                branding["logo_position"] = "station_id_only"
            branding["logo_max_width"] = _clamp_int(branding.get("logo_max_width"), 64, 320, 170)
            self._settings["branding"] = branding

            maps = self._settings.get("maps") or {}
            maps["auto_city_labels"] = bool(maps.get("auto_city_labels", True))
            maps["city_min_population"] = _clamp_int(maps.get("city_min_population"), 1000, 1000000, 5000)
            maps["city_max_labels"] = _clamp_int(maps.get("city_max_labels"), 2, 20, 10)
            maps["city_radius_miles"] = _clamp_int(maps.get("city_radius_miles"), 25, 500, 180)
            maps["regional_map_enabled"] = bool(maps.get("regional_map_enabled", True))
            if maps.get("regional_map_view") not in {"local", "regional", "wide"}:
                maps["regional_map_view"] = "regional"
            self._settings["maps"] = maps

            storm = self._settings.get("storm_guidance") or {}
            storm["enabled"] = bool(storm.get("enabled", True))
            storm["refresh_seconds"] = _clamp_int(storm.get("refresh_seconds"), 300, 3600, 900)
            self._settings["storm_guidance"] = storm

            spc = self._settings.get("spc") or {}
            spc["enabled"] = bool(spc.get("enabled", True))
            spc["refresh_seconds"] = _clamp_int(spc.get("refresh_seconds"), 300, 3600, 900)
            if spc.get("minimum_smart_risk") not in {"TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"}: spc["minimum_smart_risk"] = "MRGL"
            self._settings["spc"] = spc

            history = self._settings.get("history") or {}
            history["enabled"] = bool(history.get("enabled", True))
            history["retention_days"] = _clamp_int(history.get("retention_days"), 1, 3650, 90)
            self._settings["history"] = history

            smart = self._settings.get("smart_programming") or {}
            smart["enabled"] = bool(smart.get("enabled", True))
            smart["rain_threshold"] = _clamp_int(smart.get("rain_threshold"), 0, 100, 20)
            smart["storm_threshold"] = _clamp_int(smart.get("storm_threshold"), 0, 100, 15)
            smart["heat_threshold"] = _clamp_int(smart.get("heat_threshold"), 70, 130, 95)
            smart["cold_threshold"] = _clamp_int(smart.get("cold_threshold"), -40, 60, 32)
            self._settings["smart_programming"] = smart

            dayparts = self._settings.get("dayparts") or {}
            dayparts = _deep_merge(DEFAULT_SETTINGS["dayparts"], dayparts)
            dayparts["enabled"] = bool(dayparts.get("enabled", True))
            for k, default in (("morning_start",5),("daytime_start",10),("evening_start",17),("overnight_start",22)):
                dayparts[k] = _clamp_int(dayparts.get(k), 0, 23, default)
            self._settings["dayparts"] = dayparts

            cache = self._settings.get("cache") or {}
            cache["retention_hours"] = _clamp_int(cache.get("retention_hours"), 1, 720, 48)
            cache["auto_cleanup"] = bool(cache.get("auto_cleanup", True))
            self._settings["cache"] = cache

            channels = _deep_merge(DEFAULT_SETTINGS["channels"], self._settings.get("channels") or {})
            channels["per_zip_enabled"] = bool(channels.get("per_zip_enabled", True))
            channels["radar_enabled"] = bool(channels.get("radar_enabled", True))
            channels["severe_enabled"] = bool(channels.get("severe_enabled", True))
            channels["max_zip_channels"] = _clamp_int(channels.get("max_zip_channels"), 1, 24, 12)
            if channels.get("streaming_mode") not in {"always_on", "on_demand"}:
                channels["streaming_mode"] = "on_demand"
            channels["idle_timeout_seconds"] = _clamp_int(channels.get("idle_timeout_seconds"), 15, 3600, 90)
            channels["startup_timeout_seconds"] = _clamp_int(channels.get("startup_timeout_seconds"), 5, 60, 12)
            channels["severe_auto_start"] = bool(channels.get("severe_auto_start", True))
            lineup = channels.get("lineup") if isinstance(channels.get("lineup"), list) else []
            clean_lineup = []
            seen_keys = set()
            for item in lineup[:64]:
                if not isinstance(item, dict): continue
                key = str(item.get("key") or "").strip()[:64]
                if not key or key in seen_keys: continue
                seen_keys.add(key)
                clean_lineup.append({
                    "key": key,
                    "enabled": bool(item.get("enabled", True)),
                    "number": _clamp_int(item.get("number"), 1, 9999, 200 + len(clean_lineup) + 1),
                    "name": str(item.get("name") or "")[:64],
                })
            channels["lineup"] = clean_lineup
            overrides = channels.get("overrides") if isinstance(channels.get("overrides"), dict) else {}
            clean_overrides = {}
            for key, item in list(overrides.items())[:64]:
                if not isinstance(item, dict): continue
                row = {}
                theme = item.get("theme")
                if theme in {"classic-blue", "local-90s", "retro-2000", "terminal-80s", "cable-gold"}: row["theme"] = theme
                for b in ("music_enabled", "retro_enabled"):
                    if b in item: row[b] = bool(item.get(b))
                if "music_volume" in item: row["music_volume"] = _clamp_float(item.get("music_volume"), 0, 1, 0.30)
                if item.get("transition") in {"cut", "crossfade", "wipe", "wipe_vertical", "slide_left", "slide_up", "venetian", "dissolve", "pixel_dissolve", "crt_fade"}: row["transition"] = item.get("transition")
                if item.get("encoder") in {"auto", "software", "nvenc", "qsv", "vaapi"}: row["encoder"] = item.get("encoder")
                if item.get("streaming_mode") in {"always_on", "on_demand"}: row["streaming_mode"] = item.get("streaming_mode")
                if "output_fps" in item: row["output_fps"] = _clamp_int(item.get("output_fps"), 5, 30, 15)
                if "content_fps" in item: row["content_fps"] = _clamp_int(item.get("content_fps"), 1, 10, 3)
                if "bitrate" in item:
                    br = str(item.get("bitrate") or "").lower().strip()
                    if br.endswith("k") and br[:-1].isdigit(): row["bitrate"] = br
                clean_overrides[str(key)[:64]] = row
            channels["overrides"] = clean_overrides
            self._settings["channels"] = channels

            performance = _deep_merge(DEFAULT_SETTINGS["performance"], self._settings.get("performance") or {})
            if performance.get("mode") not in {"manual", "adaptive", "maximum_quality", "balanced", "low_cpu"}: performance["mode"] = "adaptive"
            performance["stall_recovery_enabled"] = bool(performance.get("stall_recovery_enabled", True))
            performance["stall_seconds"] = _clamp_int(performance.get("stall_seconds"), 8, 120, 15)
            performance["adaptive_bad_seconds"] = _clamp_int(performance.get("adaptive_bad_seconds"), 6, 120, 18)
            performance["adaptive_recover_seconds"] = _clamp_int(performance.get("adaptive_recover_seconds"), 30, 900, 120)
            performance["adaptive_disable_retro"] = bool(performance.get("adaptive_disable_retro", True))
            if performance.get("adaptive_transition") not in {"cut", "crossfade"}: performance["adaptive_transition"] = "cut"
            performance["adaptive_content_fps"] = _clamp_int(performance.get("adaptive_content_fps"), 1, 5, 2)
            self._settings["performance"] = performance

            self._settings["custom_profiles"] = self._settings.get("custom_profiles") if isinstance(self._settings.get("custom_profiles"), dict) else {}

            notifications = _deep_merge(DEFAULT_SETTINGS["notifications"], self._settings.get("notifications") or {})
            notifications["enabled"] = bool(notifications.get("enabled", False))
            webhook_url = str(notifications.get("webhook_url") or "").strip()[:1000]
            notifications["webhook_url"] = webhook_url if webhook_url.startswith(("http://", "https://")) else ""
            allowed_events = {"severe", "source", "stream", "settings", "lifecycle", "refresh"}
            notifications["events"] = [str(x) for x in (notifications.get("events") or []) if str(x) in allowed_events] or ["severe", "source", "stream"]
            notifications["minimum_interval_seconds"] = _clamp_int(notifications.get("minimum_interval_seconds"), 0, 3600, 30)
            notifications["allow_private_targets"] = bool(notifications.get("allow_private_targets", False))
            self._settings["notifications"] = notifications

            valid_slides = {
                "station_id", "current", "today", "nws_forecast", "hourly", "precipitation",
                "radar", "radar_local", "radar_regional", "radar_wide", "seven_day", "regional", "almanac",
                "alert", "alert_radar", "temperature_trend", "storm_outlook", "regional_map",
                "condition_focus", "weather_history", "spc_outlook",
            }
            channels = self._settings.get("channels") or {}
            local_valid = valid_slides - {"alert", "alert_radar", "radar", "radar_local", "radar_regional", "radar_wide", "regional_map"}
            radar_valid = {"station_id", "radar_local", "radar_regional", "radar_wide", "regional_map", "storm_outlook", "spc_outlook", "current", "alert", "alert_radar"}
            severe_valid = {"station_id", "current", "spc_outlook", "storm_outlook", "radar_local", "radar_regional", "radar_wide", "regional_map", "alert", "alert_radar", "nws_forecast"}
            channels["zip_sequence"] = [x for x in (channels.get("zip_sequence") or []) if x in local_valid] or copy.deepcopy(DEFAULT_SETTINGS["channels"]["zip_sequence"])
            channels["radar_sequence"] = [x for x in (channels.get("radar_sequence") or []) if x in radar_valid] or copy.deepcopy(DEFAULT_SETTINGS["channels"]["radar_sequence"])
            channels["severe_idle_sequence"] = [x for x in (channels.get("severe_idle_sequence") or []) if x in severe_valid] or copy.deepcopy(DEFAULT_SETTINGS["channels"]["severe_idle_sequence"])
            self._settings["channels"] = channels
            dayparts = self._settings.get("dayparts") or {}
            sequences = dayparts.get("sequences") if isinstance(dayparts.get("sequences"), dict) else {}
            clean_sequences = {}
            for part in ("morning", "daytime", "evening", "overnight"):
                seq = [x for x in (sequences.get(part) or []) if x in valid_slides - {"alert", "alert_radar"}]
                clean_sequences[part] = seq or copy.deepcopy(DEFAULT_SETTINGS["dayparts"]["sequences"][part])
            dayparts["sequences"] = clean_sequences
            self._settings["dayparts"] = dayparts

            pres = self._settings["presentation"]
            valid_transitions = {
                "cut", "crossfade", "wipe", "wipe_vertical", "slide_left", "slide_up",
                "venetian", "dissolve", "pixel_dissolve", "crt_fade",
            }
            pres["transition"] = pres.get("transition", "crossfade") if pres.get("transition") in valid_transitions else "crossfade"
            pres["transition_seconds"] = _clamp_float(pres.get("transition_seconds"), 0.0, 2.5, 0.75)

            effects = pres.get("retro_effects") if isinstance(pres.get("retro_effects"), dict) else {}
            effects = _deep_merge(DEFAULT_SETTINGS["presentation"]["retro_effects"], effects)
            effects["enabled"] = bool(effects.get("enabled", False))
            effects["scanlines"] = _clamp_float(effects.get("scanlines"), 0.0, 0.65, 0.14)
            effects["noise"] = _clamp_float(effects.get("noise"), 0.0, 0.25, 0.025)
            effects["bloom"] = _clamp_float(effects.get("bloom"), 0.0, 0.50, 0.10)
            effects["soft_edges"] = _clamp_float(effects.get("soft_edges"), 0.0, 0.60, 0.12)
            effects["color_bleed_px"] = _clamp_int(effects.get("color_bleed_px"), 0, 6, 1)
            effects["horizontal_jitter_px"] = _clamp_int(effects.get("horizontal_jitter_px"), 0, 5, 1)
            pres["retro_effects"] = effects

            scheduled = pres.get("scheduled_updates") if isinstance(pres.get("scheduled_updates"), dict) else {}
            scheduled = _deep_merge(DEFAULT_SETTINGS["presentation"]["scheduled_updates"], scheduled)
            scheduled["enabled"] = bool(scheduled.get("enabled", False))
            clean_marks = []
            for mark in scheduled.get("minute_marks", []):
                m = _clamp_int(mark, 0, 59, 8)
                if m not in clean_marks:
                    clean_marks.append(m)
            scheduled["minute_marks"] = sorted(clean_marks)[:12] or [8, 18, 28, 38, 48, 58]
            scheduled["window_seconds"] = _clamp_int(scheduled.get("window_seconds"), 30, 300, 120)
            scheduled["intro_enabled"] = bool(scheduled.get("intro_enabled", True))
            scheduled["phase_lead_seconds"] = _clamp_float(scheduled.get("phase_lead_seconds"), 0.0, 3.0, 0.8)
            scheduled["phase_tail_seconds"] = _clamp_float(scheduled.get("phase_tail_seconds"), 0.0, 5.0, 1.0)
            scheduled["tts_wait_seconds"] = _clamp_int(scheduled.get("tts_wait_seconds"), 3, 45, 15)
            scheduled["max_phase_seconds"] = _clamp_int(scheduled.get("max_phase_seconds"), 15, 120, 75)
            # Local on the 8s is a purpose-built block in v0.2.2.1. Keep its
            # configurable phase list intentionally small so each phase has a
            # screen-accurate narration contract.
            local8_valid = {"current", "today", "hourly", "radar_local", "seven_day"}
            schedule_seq = [x for x in scheduled.get("sequence", []) if x in local8_valid]
            scheduled["sequence"] = schedule_seq or copy.deepcopy(DEFAULT_SETTINGS["presentation"]["scheduled_updates"]["sequence"])
            pres["scheduled_updates"] = scheduled

            seq = [x for x in pres.get("sequence", []) if x in valid_slides - {"alert", "alert_radar"}]
            pres["sequence"] = seq or copy.deepcopy(DEFAULT_SETTINGS["presentation"]["sequence"])

            for key in valid_slides:
                if key in self._settings["slides"]:
                    self._settings["slides"][key] = _clamp_int(self._settings["slides"][key], 3, 60, 10)
            self._settings["slides"]["station_id"] = _clamp_int(
                pres.get("station_id_seconds", self._settings["slides"].get("station_id", 6)), 3, 20, 6
            )

            self._save_locked()
            return copy.deepcopy(self._settings)

    def add_location(self, location: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            postal_code = str(location["postal_code"])
            for existing in self._settings["locations"]:
                if existing["postal_code"] == postal_code:
                    return copy.deepcopy(existing)
            entry = {
                "id": uuid.uuid4().hex[:10],
                "postal_code": postal_code,
                "name": location["name"],
                "admin1": location.get("admin1", ""),
                "country_code": location.get("country_code", "US"),
                "latitude": float(location["latitude"]),
                "longitude": float(location["longitude"]),
                "timezone": location.get("timezone", "auto"),
            }
            self._settings["locations"].append(entry)
            if not self._settings.get("primary_location_id"):
                self._settings["primary_location_id"] = entry["id"]
            self._save_locked()
            return copy.deepcopy(entry)

    def remove_location(self, location_id: str) -> bool:
        with self._lock:
            before = len(self._settings["locations"])
            self._settings["locations"] = [x for x in self._settings["locations"] if x["id"] != location_id]
            if len(self._settings["locations"]) == before:
                return False
            if self._settings.get("primary_location_id") == location_id:
                self._settings["primary_location_id"] = self._settings["locations"][0]["id"] if self._settings["locations"] else None
            self._save_locked()
            return True

    def set_primary(self, location_id: str) -> bool:
        with self._lock:
            if not any(x["id"] == location_id for x in self._settings["locations"]):
                return False
            self._settings["primary_location_id"] = location_id
            self._save_locked()
            return True
