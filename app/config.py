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
    "version": 10,
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
    "nws_user_agent": "WeatherStream/0.1.8.1 (Roller Weather Network local weather display)",
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
            "window_seconds": 120,
            "sequence": ["station_id", "current", "radar_local", "today", "seven_day"],
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

            merged["version"] = 10
            return merged
        except Exception:
            return copy.deepcopy(DEFAULT_SETTINGS)

    def _save_locked(self) -> None:
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._settings, fh, indent=2)
        tmp.replace(SETTINGS_PATH)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._settings)

    def update_general(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "station_name", "station_callsign", "station_slogan", "service_area",
            "public_base_url", "theme", "weather_refresh_seconds",
            "alert_refresh_seconds", "nws_user_agent", "music", "radar",
            "alerts", "presentation", "slides", "branding", "maps", "storm_guidance",
            "spc", "history", "smart_programming", "dayparts", "cache", "channels", "video",
        }
        with self._lock:
            for key in allowed:
                if key not in payload:
                    continue
                if key in {"music", "radar", "alerts", "presentation", "slides", "branding", "maps", "storm_guidance", "spc", "history", "smart_programming", "dayparts", "cache", "channels", "video"} and isinstance(payload[key], dict):
                    self._settings[key] = _deep_merge(self._settings[key], payload[key])
                else:
                    self._settings[key] = payload[key]

            self._settings["version"] = 10
            self._settings["station_name"] = str(self._settings.get("station_name") or "Roller Weather Network")[:40]
            self._settings["station_callsign"] = str(self._settings.get("station_callsign") or "")[:12]
            self._settings["station_slogan"] = str(self._settings.get("station_slogan") or "")[:64]
            self._settings["service_area"] = str(self._settings.get("service_area") or "")[:64]
            self._settings["theme"] = self._settings["theme"] if self._settings["theme"] in {"classic-blue", "local-90s", "retro-2000", "terminal-80s", "cable-gold"} else "local-90s"
            self._settings["weather_refresh_seconds"] = max(120, int(self._settings["weather_refresh_seconds"]))
            self._settings["alert_refresh_seconds"] = max(30, int(self._settings["alert_refresh_seconds"]))
            self._settings["music"]["volume"] = _clamp_float(self._settings["music"].get("volume"), 0.0, 1.0, 0.30)

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
            self._settings["channels"] = channels

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
            schedule_seq = [x for x in scheduled.get("sequence", []) if x in valid_slides - {"alert", "alert_radar"}]
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
