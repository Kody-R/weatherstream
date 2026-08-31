from __future__ import annotations

import math
from array import array
from collections import deque
import os
import random
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app.hardware import choose_encoder, device_info, encoder_capabilities, normalize_device_setting
from app.observability import observability

LIVE_DIR = Path(os.environ.get("WEATHERSTREAM_LIVE", "/tmp/weatherstream/live"))
MUSIC_DIR = Path(os.environ.get("WEATHERSTREAM_MUSIC", "/music"))
PREVIEW_PATH = Path(os.environ.get("WEATHERSTREAM_PREVIEW", "/tmp/weatherstream/preview.jpg"))
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"}

def location_channel_key(location: dict[str, Any]) -> str:
    postal = "".join(ch for ch in str(location.get("postal_code") or "local") if ch.isdigit())[:5]
    return f"zip-{postal or str(location.get('id') or 'local')[:10]}"


class ChannelWorker:
    """One H.264/AAC HLS encoder for a logical RWN channel."""

    def __init__(self, config_store, renderer, tts_manager, key: str, location_id: str, mode: str, primary_preview: bool = False) -> None:
        self.config_store = config_store
        self.renderer = renderer
        self.tts_manager = tts_manager
        self.key = key
        self.location_id = location_id
        self.mode = mode
        self.primary_preview = primary_preview
        self.output_dir = LIVE_DIR / key
        self.preview_path = self.output_dir / "preview.jpg"
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._lifecycle_lock = threading.RLock()
        self.last_error: str | None = None
        self.started_at: float | None = None
        self.last_chime_alert_id: str | None = None
        self.last_tts_alert_id: str | None = None
        self.last_tts_local_block_id: str | None = None
        self._stderr_lines = deque(maxlen=60)
        self._frames_sent = 0
        self._frames_rendered = 0
        self._render_seconds_total = 0.0
        self._late_frames = 0
        self._media_sequence: int | None = None
        self._latest_segment: int | None = None
        self._playlist_mtime: float | None = None
        self._hls_prev_seq: int | None = None
        self._hls_prev_time: float | None = None
        self._realtime_ratio: float | None = None
        self._adaptive_degraded = False
        self._adaptive_bad_since: float | None = None
        self._adaptive_good_since: float | None = None
        self._restart_count = 0
        self._recovery_count = 0
        self._last_restart_reason: str | None = None
        self._last_recovery_at: float | None = None
        self._hardware_failed_signature: tuple[str, str] | None = None
        self._hardware_fallback_count = 0
        self._hardware_fallback_active = False
        self._active_encoder = "software"
        self._active_encoder_device: str | None = None
        self._encoder_fallback_reason: str | None = None
        self.last_viewer_activity: float | None = None
        self.last_idle_at: float | None = None
        self.activation_count = 0

        # v0.2.2.1 Local on the 8s state. This is a real programming block owned
        # by the channel worker instead of a clock-selected alternate renderer
        # sequence. The video and announcement threads coordinate through this lock.
        self._local8_lock = threading.RLock()
        self._local8_active = False
        self._local8_block_id: str | None = None
        self._local8_phases: list[str] = []
        self._local8_phase_index = 0
        self._local8_phase_started_mono: float | None = None
        self._local8_phase_text = ""
        self._local8_phase_token = 0
        self._local8_audio_queued = False
        self._local8_audio_started_mono: float | None = None
        self._local8_audio_duration = 0.0
        self._local8_last_handled_block_id: str | None = None
        self._local8_last_abort_reason: str | None = None
        self._local8_next_context_check = 0.0

    @property
    def spec(self) -> tuple[str, str, str]:
        return (self.key, self.location_id, self.mode)

    def start(self) -> None:
        """Start this channel encoder if it is not already active.

        v0.2.2.1 workers are reusable: an on-demand worker can stop after its idle
        timeout and later be started again by a new HLS playlist request.
        """
        with self._lifecycle_lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.preview_path.parent.mkdir(parents=True, exist_ok=True)
            if self._thread and self._thread.is_alive():
                if not self._stop.is_set():
                    return
                self._thread.join(timeout=4.0)
                if self._thread.is_alive():
                    return
            self._stop.clear()
            self._restart.clear()
            self._media_sequence = None
            self._latest_segment = None
            self._playlist_mtime = None
            self._hls_prev_seq = None
            self._hls_prev_time = None
            self._realtime_ratio = None
            # Remove stale HLS before returning control to an on-demand request.
            self._clean_live()
            self.activation_count += 1
            self._thread = threading.Thread(target=self._run, name=f"stream-{self.key}", daemon=True)
            self._thread.start()
            observability.event("stream", "Channel encoder starting", channel=self.key, mode=self.mode, activation=self.activation_count)

    def stop(self, idle: bool = False) -> None:
        with self._lifecycle_lock:
            was_active = self.thread_active()
            self._stop.set()
            if idle:
                self.last_idle_at = time.time()
            self._terminate_process()
            if was_active:
                observability.event("stream", "Channel encoder stopped", channel=self.key, mode=self.mode, reason="idle timeout" if idle else "stop requested")

    def join(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))

    def note_viewer_activity(self) -> None:
        self.last_viewer_activity = time.time()

    def thread_active(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def request_restart(self, reason: str = "manual", recovery: bool = False) -> None:
        # A deliberate restart retries the configured device; automatic stall
        # recovery does not repeatedly hammer a device that already failed.
        if not recovery:
            self._hardware_failed_signature = None
            self._hardware_fallback_active = False
        self._last_restart_reason = reason
        self._restart_count += 1
        if recovery:
            self._recovery_count += 1
            self._last_recovery_at = time.time()
        self._restart.set()
        self._terminate_process()
        observability.event("stream", "Channel encoder restart requested", channel=self.key, mode=self.mode, reason=reason, recovery=recovery)

    def _channel_override(self, settings: dict[str, Any]) -> dict[str, Any]:
        overrides = ((settings.get("channels") or {}).get("overrides") or {})
        return dict(overrides.get(self.key) or {}) if isinstance(overrides, dict) else {}

    def lifecycle_mode(self, settings: dict[str, Any] | None = None) -> str:
        settings = settings or self.config_store.get()
        channels = settings.get("channels") or {}
        override = self._channel_override(settings)
        mode = override.get("streaming_mode") or channels.get("streaming_mode", "on_demand")
        return mode if mode in {"always_on", "on_demand"} else "on_demand"

    def _effective_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        override = self._channel_override(settings)
        profile_id = str(override.get("branding_profile") or "")
        profile = (settings.get("branding_profiles") or {}).get(profile_id) if profile_id else None
        if isinstance(profile, dict) and profile.get("music_folder"):
            settings.setdefault("music", {})["folder"] = str(profile["music_folder"])
        if override.get("theme"):
            settings["theme"] = override["theme"]
        if "music_enabled" in override:
            settings.setdefault("music", {})["enabled"] = bool(override["music_enabled"])
        if "music_volume" in override:
            settings.setdefault("music", {})["volume"] = float(override["music_volume"])
        if "retro_enabled" in override:
            settings.setdefault("presentation", {}).setdefault("retro_effects", {})["enabled"] = bool(override["retro_enabled"])
        if override.get("transition"):
            settings.setdefault("presentation", {})["transition"] = override["transition"]
        video = settings.setdefault("video", {})
        for key in ("encoder", "encoder_device", "output_fps", "content_fps", "bitrate"):
            if key in override:
                video[key] = override[key]
        return settings

    def _runtime_render_overrides(self, settings: dict[str, Any]) -> dict[str, Any]:
        override = self._channel_override(settings)
        runtime = {k: override[k] for k in ("theme", "retro_enabled", "transition", "branding_profile") if k in override}
        if self._adaptive_degraded:
            runtime["performance_degraded"] = True
        return runtime

    def _local8_cfg(self, settings: dict[str, Any]) -> dict[str, Any]:
        return (((settings.get("presentation") or {}).get("scheduled_updates") or {}))

    def _local8_phase_min_seconds(self, phase: str, settings: dict[str, Any]) -> float:
        if phase == "intro":
            return 4.0
        slides = settings.get("slides") or {}
        fallback = 16 if phase == "radar_local" else 10
        try:
            return float(max(3, min(60, int(slides.get(phase, fallback)))))
        except Exception:
            return float(fallback)

    def _local8_prepare_phase(self, settings: dict[str, Any], primary: dict[str, Any], mono: float) -> None:
        with self._local8_lock:
            if not self._local8_active or not self._local8_phases:
                return
            phase = self._local8_phases[self._local8_phase_index]
            self._local8_phase_started_mono = mono
            self._local8_phase_token += 1
            self._local8_audio_queued = False
            self._local8_audio_started_mono = None
            self._local8_audio_duration = 0.0
            self._local8_phase_text = self.tts_manager.local_on_8s_phase_text(phase, primary, settings)

    def _start_local8(self, block_id: str, settings: dict[str, Any], primary: dict[str, Any], mono: float) -> None:
        cfg = self._local8_cfg(settings)
        allowed = {"current", "today", "hourly", "radar_local", "seven_day"}
        phases = [str(x) for x in (cfg.get("sequence") or []) if str(x) in allowed]
        if not phases:
            phases = ["current", "today", "hourly", "radar_local", "seven_day"]
        if bool(cfg.get("intro_enabled", True)):
            phases.insert(0, "intro")
        with self._local8_lock:
            self._local8_active = True
            self._local8_block_id = str(block_id)
            self._local8_phases = phases
            self._local8_phase_index = 0
            self._local8_last_abort_reason = None
        self._local8_prepare_phase(settings, primary, mono)

    def _abort_local8(self, reason: str = "preempted") -> None:
        with self._local8_lock:
            if not self._local8_active:
                return
            # Mark the interrupted block handled so clearing a severe warning does
            # not restart the same :08/:18/... block mid-window.
            self._local8_last_handled_block_id = self._local8_block_id
            self.last_tts_local_block_id = self._local8_block_id
            self._local8_last_abort_reason = reason
            self._local8_active = False
            self._local8_phases = []
            self._local8_phase_text = ""
            self._local8_audio_queued = False
            self._local8_audio_started_mono = None
            self._local8_audio_duration = 0.0

    def _finish_local8(self) -> None:
        with self._local8_lock:
            block_id = self._local8_block_id
            self._local8_last_handled_block_id = block_id
            self.last_tts_local_block_id = block_id
            self._local8_active = False
            self._local8_phases = []
            self._local8_phase_text = ""
            self._local8_audio_queued = False
            self._local8_audio_started_mono = None
            self._local8_audio_duration = 0.0

    def _advance_local8_phase(self, settings: dict[str, Any], primary: dict[str, Any], mono: float) -> None:
        with self._local8_lock:
            if not self._local8_active:
                return
            next_index = self._local8_phase_index + 1
            if next_index >= len(self._local8_phases):
                finish = True
            else:
                self._local8_phase_index = next_index
                finish = False
        if finish:
            self._finish_local8()
        else:
            self._local8_prepare_phase(settings, primary, mono)

    def _local8_tick(self, now_wall: float) -> dict[str, Any]:
        """Advance/trigger the Local on the 8s block and return renderer overrides."""
        if self.mode != "local":
            return {}
        mono = time.monotonic()
        context = None
        # Trigger/severe checks need not run at video frame rate. The renderer data
        # itself remains live, while this control check runs about twice per second.
        if mono >= self._local8_next_context_check:
            context = self.renderer.narration_context_for(self.location_id, "local", now_wall)
            self._local8_next_context_check = mono + 0.5
            if context.get("takeover_alert"):
                self._abort_local8("severe weather takeover")
            else:
                settings = context.get("settings") or self._effective_settings(self.config_store.get())
                cfg = self._local8_cfg(settings)
                block_id = context.get("scheduled_block_id")
                with self._local8_lock:
                    active = self._local8_active
                    handled = self._local8_last_handled_block_id
                if bool(cfg.get("enabled", False)) and context.get("scheduled_update_active") and block_id and not active and block_id != handled:
                    self._start_local8(str(block_id), settings, context.get("primary") or {}, mono)

        with self._local8_lock:
            if not self._local8_active or not self._local8_phases:
                return {}
            phase = self._local8_phases[self._local8_phase_index]
            phase_index = self._local8_phase_index
            phase_count = len(self._local8_phases)
            phase_started = self._local8_phase_started_mono or mono
            audio_queued = self._local8_audio_queued
            audio_started = self._local8_audio_started_mono
            audio_duration = self._local8_audio_duration

        # Use fresh settings only when a block is active; this keeps the hot normal
        # video path from re-copying configuration solely for scheduler bookkeeping.
        settings = (context or self.renderer.narration_context_for(self.location_id, "local", now_wall)).get("settings") or self._effective_settings(self.config_store.get())
        cfg = self._local8_cfg(settings)
        elapsed = max(0.0, mono - phase_started)
        minimum = self._local8_phase_min_seconds(phase, settings)
        tts_cfg = settings.get("tts") or {}
        narration_expected = bool(tts_cfg.get("enabled", False) and tts_cfg.get("local_on_8s", True) and self._local8_phase_text)
        tail = max(0.0, min(5.0, float(cfg.get("phase_tail_seconds", 1.0))))
        wait = max(3.0, min(45.0, float(cfg.get("tts_wait_seconds", 15))))
        max_phase = max(15.0, min(120.0, float(cfg.get("max_phase_seconds", 75))))
        advance = False
        if narration_expected:
            if audio_queued and audio_started is not None:
                required = max(minimum, (audio_started - phase_started) + audio_duration + tail)
                advance = elapsed >= required
            elif elapsed >= max(minimum, wait):
                # A missing voice/synthesis failure must never wedge a live channel.
                advance = True
        else:
            advance = elapsed >= minimum
        if not audio_queued and elapsed >= max_phase:
            advance = True

        if advance:
            fresh = self.renderer.narration_context_for(self.location_id, "local", now_wall)
            self._advance_local8_phase(fresh.get("settings") or settings, fresh.get("primary") or {}, mono)
            with self._local8_lock:
                if not self._local8_active or not self._local8_phases:
                    return {}
                phase = self._local8_phases[self._local8_phase_index]
                phase_index = self._local8_phase_index
                phase_count = len(self._local8_phases)
                phase_started = self._local8_phase_started_mono or mono
                elapsed = max(0.0, mono - phase_started)
                minimum = self._local8_phase_min_seconds(phase, fresh.get("settings") or settings)

        slide = "local8_intro" if phase == "intro" else phase
        return {
            "local8_active": True,
            "local8_phase": phase,
            "local8_phase_index": phase_index,
            "local8_phase_count": phase_count,
            "force_slide": slide,
            "force_progress": min(1.0, elapsed / max(0.1, minimum)),
        }

    def _local8_audio_snapshot(self) -> dict[str, Any]:
        with self._local8_lock:
            if not self._local8_active or not self._local8_phases:
                return {"active": False}
            return {
                "active": True,
                "block_id": self._local8_block_id,
                "phase": self._local8_phases[self._local8_phase_index],
                "phase_index": self._local8_phase_index,
                "phase_count": len(self._local8_phases),
                "phase_started_mono": self._local8_phase_started_mono,
                "phase_text": self._local8_phase_text,
                "phase_token": self._local8_phase_token,
                "audio_queued": self._local8_audio_queued,
            }

    def _mark_local8_audio_queued(self, token: int, duration: float) -> bool:
        with self._local8_lock:
            if not self._local8_active or token != self._local8_phase_token or self._local8_audio_queued:
                return False
            self._local8_audio_queued = True
            self._local8_audio_started_mono = time.monotonic()
            self._local8_audio_duration = max(0.0, float(duration))
            return True

    def _local8_status(self) -> dict[str, Any]:
        with self._local8_lock:
            phase = self._local8_phases[self._local8_phase_index] if self._local8_active and self._local8_phases else None
            elapsed = (time.monotonic() - self._local8_phase_started_mono) if self._local8_active and self._local8_phase_started_mono else None
            return {
                "active": self._local8_active,
                "block_id": self._local8_block_id if self._local8_active else None,
                "phase": phase,
                "phase_index": self._local8_phase_index + 1 if phase else None,
                "phase_count": len(self._local8_phases) if self._local8_active else 0,
                "phase_elapsed_seconds": round(elapsed, 2) if elapsed is not None else None,
                "narration_queued": self._local8_audio_queued if self._local8_active else False,
                "last_completed_or_handled_block": self._local8_last_handled_block_id,
                "last_abort_reason": self._local8_last_abort_reason,
            }

    def start_local8_test(self) -> bool:
        """Start one complete Local on the 8s block immediately for testing."""
        if self.mode != "local":
            return False
        context = self.renderer.narration_context_for(self.location_id, "local", time.time())
        if context.get("takeover_alert"):
            return False
        settings = context.get("settings") or self._effective_settings(self.config_store.get())
        block_id = f"TEST-{int(time.time())}"
        self._start_local8(block_id, settings, context.get("primary") or {}, time.monotonic())
        self.note_viewer_activity()
        return True

    def _maybe_adapt(self, settings: dict[str, Any]) -> None:
        perf = settings.get("performance") or {}
        mode = perf.get("mode", "adaptive")
        if mode == "low_cpu":
            self._adaptive_degraded = True
            return
        if mode in {"manual", "maximum_quality"}:
            self._adaptive_degraded = False
            self._adaptive_bad_since = self._adaptive_good_since = None
            return
        if mode not in {"adaptive", "balanced"}:
            return
        now = time.monotonic()
        seg = max(1, int((settings.get("video") or {}).get("hls_segment_seconds", 3)))
        age = (time.time() - self._playlist_mtime) if self._playlist_mtime else None
        bad = (self._realtime_ratio is not None and self._realtime_ratio < 0.88) or (age is not None and age > seg * 2.5)
        good = self._realtime_ratio is not None and self._realtime_ratio >= 0.96 and (age is None or age < seg * 2.0)
        if bad:
            self._adaptive_good_since = None
            self._adaptive_bad_since = self._adaptive_bad_since or now
            if now - self._adaptive_bad_since >= int(perf.get("adaptive_bad_seconds", 18)):
                self._adaptive_degraded = True
        else:
            self._adaptive_bad_since = None
        if self._adaptive_degraded and good:
            self._adaptive_good_since = self._adaptive_good_since or now
            if now - self._adaptive_good_since >= int(perf.get("adaptive_recover_seconds", 120)):
                self._adaptive_degraded = False
                self._adaptive_good_since = None
        elif not good:
            self._adaptive_good_since = None

    def status(self) -> dict[str, Any]:
        proc = self._process
        self._update_hls_metrics(force=False)
        now = time.time()
        playlist_age = (now - self._playlist_mtime) if self._playlist_mtime else None
        settings = self._effective_settings(self.config_store.get()); video = settings.get("video") or {}
        lifecycle_mode = self.lifecycle_mode(settings)
        seg = max(1, int(video.get("hls_segment_seconds", 3)))
        ratio = self._realtime_ratio
        process_running = bool(proc and proc.poll() is None)
        thread_active = self.thread_active()
        if not process_running and not thread_active:
            realtime_state = "IDLE"
        elif playlist_age is not None and playlist_age > seg * 4:
            realtime_state = "STALLED"
        elif ratio is None:
            realtime_state = "WARMING UP"
        elif ratio >= 0.90:
            realtime_state = "REALTIME"
        elif ratio >= 0.70:
            realtime_state = "DEGRADED"
        else:
            realtime_state = "FALLING BEHIND"
        avg_render_ms = (self._render_seconds_total / self._frames_rendered * 1000.0) if self._frames_rendered else None
        return {
            "key": self.key,
            "mode": self.mode,
            "location_id": self.location_id,
            "streaming_mode": lifecycle_mode,
            "lifecycle_state": "ON_AIR" if process_running else "STARTING" if thread_active else "IDLE",
            "last_viewer_activity": self.last_viewer_activity,
            "viewer_activity_age_seconds": round(now - self.last_viewer_activity, 2) if self.last_viewer_activity else None,
            "last_idle_at": self.last_idle_at,
            "activation_count": self.activation_count,
            "running": process_running,
            "pid": proc.pid if proc and proc.poll() is None else None,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "playlist_ready": bool(process_running and (self.output_dir / "index.m3u8").exists()),
            "path": f"/live/{self.key}/index.m3u8",
            "last_chime_alert_id": self.last_chime_alert_id,
            "last_tts_alert_id": self.last_tts_alert_id,
            "last_tts_local_block_id": self.last_tts_local_block_id,
            "local_on_8s": self._local8_status(),
            "encoder": self._active_encoder,
            "encoder_device": self._active_encoder_device,
            "encoder_device_info": device_info(self._active_encoder, self._active_encoder_device),
            "requested_encoder": str(video.get("encoder", "software")),
            "requested_encoder_device": normalize_device_setting(video.get("encoder_device", "auto")),
            "encoder_fallback_reason": self._encoder_fallback_reason,
            "hardware_fallback_count": self._hardware_fallback_count,
            "hardware_fallback_active": self._hardware_fallback_active,
            "adaptive_state": "DEGRADED" if self._adaptive_degraded else "NORMAL",
            "restart_count": self._restart_count,
            "recovery_count": self._recovery_count,
            "last_restart_reason": self._last_restart_reason,
            "last_recovery_at": self._last_recovery_at,
            "media_sequence": self._media_sequence,
            "latest_segment": self._latest_segment,
            "playlist_age_seconds": round(playlist_age, 2) if playlist_age is not None else None,
            "realtime_ratio": round(ratio, 3) if ratio is not None else None,
            "realtime_state": realtime_state,
            "producer": {
                "render_fps": int(video.get("render_fps", 5)),
                "content_fps": int(video.get("content_fps", 3)),
                "effective_content_fps": min(int(video.get("content_fps", 3)), int((settings.get("performance") or {}).get("adaptive_content_fps",2))) if self._adaptive_degraded else int(video.get("content_fps",3)),
                "output_fps": int(video.get("output_fps", 15)),
                "frames_sent": self._frames_sent,
                "frames_rendered": self._frames_rendered,
                "late_frames": self._late_frames,
                "average_render_ms": round(avg_render_ms, 1) if avg_render_ms is not None else None,
            },
        }

    def diagnostics_text(self) -> str:
        device = f" device={self._active_encoder_device}" if self._active_encoder_device else ""
        lines = [f"[{self.key}] encoder={self._active_encoder}{device} state={'DEGRADED' if self._adaptive_degraded else 'NORMAL'}"]
        if self.last_error: lines.append(f"last_error: {self.last_error}")
        lines.extend(self._stderr_lines)
        return "\n".join(lines)

    def _parse_playlist_metrics(self) -> tuple[int | None, int | None]:
        playlist = self.output_dir / "index.m3u8"
        media_seq = None; latest = None
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                line=line.strip()
                if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                    media_seq = int(line.split(":", 1)[1].strip())
                elif line.endswith(".ts") and "segment_" in line:
                    stem=Path(line).stem
                    try: latest=max(latest if latest is not None else -1, int(stem.rsplit("_",1)[1]))
                    except Exception: pass
        except Exception:
            return None, None
        return media_seq, latest

    def _parse_media_sequence(self) -> int | None:
        return self._parse_playlist_metrics()[0]

    def _update_hls_metrics(self, force: bool = False) -> None:
        playlist = self.output_dir / "index.m3u8"
        try:
            self._playlist_mtime = playlist.stat().st_mtime
            if self._active_encoder == "software" and self._hardware_fallback_active:
                self._hardware_fallback_active = False
        except OSError:
            return
        now = time.monotonic()
        if not force and self._hls_prev_time is not None and now - self._hls_prev_time < 3.0:
            return
        seq, latest = self._parse_playlist_metrics()
        if seq is None and latest is None:
            return
        self._media_sequence = seq
        self._latest_segment = latest
        settings = self.config_store.get(); seg = max(1, int((settings.get("video") or {}).get("hls_segment_seconds", 3)))
        sample_index = latest if latest is not None else seq
        if sample_index is None:
            return
        if self._hls_prev_seq is not None and self._hls_prev_time is not None:
            elapsed = now - self._hls_prev_time
            if elapsed >= max(9.0, seg * 3.0):
                advance = max(0, sample_index - self._hls_prev_seq)
                self._realtime_ratio = min(2.0, (advance * seg) / elapsed)
                self._hls_prev_seq = sample_index; self._hls_prev_time = now
        else:
            self._hls_prev_seq = sample_index; self._hls_prev_time = now

    def _stderr_loop(self, proc: subprocess.Popen) -> None:
        if not proc.stderr:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                if not raw:
                    break
                self._stderr_lines.append(raw.decode("utf-8", errors="replace").rstrip())
        except Exception:
            pass

    def _terminate_process(self) -> None:
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate(); proc.wait(timeout=3)
            except Exception:
                try: proc.kill()
                except Exception: pass

    def _music_playlist(self, settings: dict[str, Any]) -> Path | None:
        music = settings.get("music", {})
        if not music.get("enabled", True) or not MUSIC_DIR.exists():
            return None
        folder = str(music.get("folder") or "").strip()
        source = MUSIC_DIR / folder if folder and (MUSIC_DIR / folder).resolve().parent == MUSIC_DIR.resolve() else MUSIC_DIR
        if not source.exists() or not source.is_dir(): source = MUSIC_DIR
        files = [p for p in source.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
        if not files:
            return None
        if music.get("shuffle", True): random.shuffle(files)
        playlist = LIVE_DIR.parent / f"music-{self.key}.ffconcat"
        with playlist.open("w", encoding="utf-8") as fh:
            fh.write("ffconcat version 1.0\n")
            for path in files:
                escaped = str(path).replace("'", "'\\''")
                fh.write(f"file '{escaped}'\n")
        return playlist

    def _ffmpeg_command(self, settings: dict[str, Any], alert_audio_fd: int) -> list[str]:
        video = settings["video"]
        w, h = int(video["width"]), int(video["height"])
        render_fps = int(video.get("render_fps", 5)); output_fps = int(video.get("output_fps", 15))
        seg = int(video.get("hls_segment_seconds", 3)); list_size = int(video.get("hls_list_size", 10))
        bitrate = str(video.get("bitrate", "2000k")); preset = str(video.get("encoder_preset", "superfast")); music_playlist = self._music_playlist(settings)
        requested_encoder = str(video.get("encoder", "software"))
        requested_device = normalize_device_setting(video.get("encoder_device", "auto"))
        self._active_encoder, self._active_encoder_device, self._encoder_fallback_reason = choose_encoder(
            requested_encoder, requested_device, self._hardware_failed_signature
        )
        cmd = ["ffmpeg","-hide_banner","-loglevel","warning","-y"]
        if self._active_encoder == "vaapi" and self._active_encoder_device:
            cmd += ["-init_hw_device", f"vaapi=hw:{self._active_encoder_device}", "-filter_hw_device", "hw"]
        elif self._active_encoder == "qsv" and self._active_encoder_device:
            cmd += [
                "-init_hw_device",
                f"qsv=hw,child_device={self._active_encoder_device},child_device_type=vaapi",
                "-filter_hw_device", "hw",
            ]
        cmd += ["-f","rawvideo","-pix_fmt","rgb24","-s",f"{w}x{h}","-r",str(render_fps),"-i","pipe:0"]
        if music_playlist:
            cmd += ["-stream_loop","-1","-f","concat","-safe","0","-i",str(music_playlist)]
        else:
            cmd += ["-f","lavfi","-i","anullsrc=r=44100:cl=stereo"]
        cmd += ["-thread_queue_size","256","-f","s16le","-ar","44100","-ac","2","-i",f"pipe:{alert_audio_fd}"]
        gop = output_fps * seg
        music_volume = float(settings.get("music",{}).get("volume",0.30)) if music_playlist else 0.0
        tts_cfg = settings.get("tts") or {}
        # The announcement pipe carries pre-scaled chimes/TTS plus silence. When
        # narration is present, optionally side-chain the music so spoken weather
        # remains intelligible without permanently lowering the soundtrack.
        if bool(tts_cfg.get("enabled", False)) and bool(tts_cfg.get("duck_music", True)):
            filt = (
                f"[1:a]volume={music_volume:.3f}[bg];"
                "[2:a]asplit=2[ducksrc][ann];"
                "[bg][ducksrc]sidechaincompress=threshold=0.008:ratio=12:attack=15:release=650[ducked];"
                "[ducked][ann]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
            )
        else:
            filt = f"[1:a]volume={music_volume:.3f}[bg];[bg][2:a]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
        if self._active_encoder == "nvenc":
            video_args = ["-vf",f"fps={output_fps},format=yuv420p","-c:v","h264_nvenc","-preset","p3","-b:v",bitrate,"-maxrate",bitrate,"-bufsize","4000k"]
        elif self._active_encoder == "vaapi":
            video_args = ["-vf",f"fps={output_fps},format=nv12,hwupload","-c:v","h264_vaapi","-b:v",bitrate,"-maxrate",bitrate,"-bufsize","4000k"]
        elif self._active_encoder == "qsv":
            video_args = ["-vf",f"fps={output_fps},format=nv12,hwupload=extra_hw_frames=64","-c:v","h264_qsv","-preset","veryfast","-b:v",bitrate,"-maxrate",bitrate,"-bufsize","4000k"]
        else:
            video_args = ["-vf",f"fps={output_fps},format=yuv420p","-c:v","libx264","-preset",preset,"-tune","stillimage","-b:v",bitrate,"-maxrate",bitrate,"-bufsize","4000k"]
        cmd += [
            "-filter_complex",filt,"-map","0:v:0","-map","[aout]",
            *video_args,"-g",str(gop),"-keyint_min",str(gop),"-sc_threshold","0",
            "-c:a","aac","-b:a","128k","-ar","44100","-ac","2",
            "-f","hls","-hls_time",str(seg),"-hls_list_size",str(list_size),"-hls_flags","delete_segments+omit_endlist+independent_segments",
            "-hls_segment_filename",str(self.output_dir / "segment_%06d.ts"),str(self.output_dir / "index.m3u8")
        ]
        return cmd

    def _make_chime(self, sample_rate: int = 44100) -> bytes:
        sequence = [(880.0,.28),(0.0,.08),(660.0,.34),(0.0,.10),(880.0,.24)]
        out=bytearray(); amplitude=10500; phase=0.0
        for freq,duration in sequence:
            frames=int(sample_rate*duration)
            for i in range(frames):
                if freq<=0: sample=0
                else:
                    edge=max(1,int(sample_rate*.015)); envelope=min(1.0,i/edge,(frames-i-1)/edge)
                    sample=int(amplitude*max(0.0,envelope)*math.sin(phase)); phase += 2*math.pi*freq/sample_rate
                packed=struct.pack("<h",sample); out.extend(packed); out.extend(packed)
        return bytes(out)

    def _scale_pcm(self, data: bytes, gain: float) -> bytes:
        gain = max(0.0, min(1.5, float(gain)))
        if gain == 1.0 or not data:
            return data
        samples = array("h")
        samples.frombytes(data)
        for i, value in enumerate(samples):
            samples[i] = max(-32768, min(32767, int(value * gain)))
        return samples.tobytes()

    def _alert_audio_loop(self, write_fd: int, proc: subprocess.Popen) -> None:
        """Feed chimes and narration into the auxiliary PCM announcement bus.

        Local on the 8s narration is phase-aware in v0.2.2.1: the current phase
        remains on screen while its exact screen-derived sentence is synthesized
        and spoken. A qualifying severe alert immediately preempts any remaining
        Local on the 8s audio and owns the bus.
        """
        chunk_frames = 2205  # 50 ms at 44.1 kHz
        silence = b"\x00\x00\x00\x00" * chunk_frames
        gap = b"\x00\x00\x00\x00" * int(44100 * 0.30)
        bytes_per_second = 44100 * 2 * 2
        raw_chime = self._make_chime()
        pending = b""
        pending_kind: str | None = None
        last_chime_signature = self.last_chime_alert_id
        next_check = 0.0
        try:
            with os.fdopen(write_fd, "wb", buffering=0) as pipe:
                while not self._stop.is_set() and not self._restart.is_set() and proc.poll() is None:
                    mono = time.monotonic()
                    if mono >= next_check:
                        settings = self._effective_settings(self.config_store.get())
                        alerts_cfg = settings.get("alerts") or {}
                        tts_cfg = settings.get("tts") or {}
                        active = self.renderer.takeover_alert_for(self.location_id)
                        signature = str(active.get("id") or active.get("headline") or active.get("event")) if active else None

                        if active:
                            # Severe weather is the highest-priority programming and
                            # audio state. Stop a Local on the 8s sentence at the next
                            # 50 ms PCM chunk rather than letting it finish first.
                            self._abort_local8("severe weather takeover")
                            if pending_kind == "local8":
                                pending = b""
                                pending_kind = None

                        if active and bool(alerts_cfg.get("chime_enabled", True)) and signature != last_chime_signature:
                            chime = self._scale_pcm(raw_chime, float(alerts_cfg.get("chime_volume", 0.65)))
                            pending += chime
                            pending_kind = "severe"
                            last_chime_signature = signature
                            self.last_chime_alert_id = signature

                        if (
                            active
                            and bool(tts_cfg.get("enabled", False))
                            and bool(tts_cfg.get("severe_alerts", True))
                            and signature != self.last_tts_alert_id
                        ):
                            text = self.tts_manager.severe_alert_text(active, settings)
                            speech = self.tts_manager.request_pcm(text, settings, "severe")
                            if speech:
                                pending += gap + speech
                                pending_kind = "severe"
                                self.last_tts_alert_id = signature

                        if (
                            not active
                            and self.mode == "local"
                            and bool(tts_cfg.get("enabled", False))
                            and bool(tts_cfg.get("local_on_8s", True))
                            and not pending
                        ):
                            state = self._local8_audio_snapshot()
                            if state.get("active") and not state.get("audio_queued"):
                                phase_started = state.get("phase_started_mono")
                                cfg = self._local8_cfg(settings)
                                lead = max(0.0, min(3.0, float(cfg.get("phase_lead_seconds", 0.8))))
                                if phase_started is not None and mono - float(phase_started) >= lead:
                                    text = str(state.get("phase_text") or "").strip()
                                    if text:
                                        phase = str(state.get("phase") or "phase")
                                        token = int(state.get("phase_token") or 0)
                                        speech = self.tts_manager.request_pcm(text, settings, f"local8_{phase}")
                                        if speech:
                                            duration = len(speech) / float(bytes_per_second)
                                            if self._mark_local8_audio_queued(token, duration):
                                                pending = speech
                                                pending_kind = "local8"

                        next_check = mono + 0.5

                    need = len(silence)
                    if pending:
                        block = pending[:need]
                        pending = pending[need:]
                        if len(block) < need:
                            block += silence[:need-len(block)]
                        if not pending:
                            pending_kind = None
                    else:
                        block = silence
                    try:
                        pipe.write(block)
                    except (BrokenPipeError, OSError):
                        break
        except OSError:
            pass

    def _clean_live(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for p in self.output_dir.glob("segment_*.ts"):
            try: p.unlink()
            except Exception: pass
        for name in ("index.m3u8",):
            try: (self.output_dir/name).unlink()
            except FileNotFoundError: pass

    def _write_preview(self, image) -> None:
        tmp=self.preview_path.with_suffix(".tmp.jpg"); image.save(tmp,format="JPEG",quality=82); tmp.replace(self.preview_path)
        if self.primary_preview:
            PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
            global_tmp=PREVIEW_PATH.with_suffix(".tmp.jpg")
            try:
                shutil.copyfile(self.preview_path, global_tmp); global_tmp.replace(PREVIEW_PATH)
            except Exception:
                pass

    def _mark_hardware_failed(self, requested_encoder: str, requested_device: str, detail: str) -> None:
        if self._active_encoder == "software":
            return
        self._hardware_failed_signature = (requested_encoder, normalize_device_setting(requested_device))
        self._hardware_fallback_count += 1
        self._hardware_fallback_active = True
        reason = detail.strip() or f"{self._active_encoder} initialization failed"
        self.last_error = f"{reason} | hardware encoder failed; immediately falling back to libx264"
        observability.event(
            "stream", "Hardware encoder failed; immediate software fallback",
            channel=self.key, encoder=self._active_encoder, device=self._active_encoder_device,
            requested_encoder=requested_encoder, requested_device=normalize_device_setting(requested_device),
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            immediate_retry = False
            settings=self._effective_settings(self.config_store.get())
            video_settings = settings.get("video") or {}
            requested_encoder = str(video_settings.get("encoder", "software"))
            requested_device = normalize_device_setting(video_settings.get("encoder_device", "auto"))
            self._clean_live(); read_fd,write_fd=os.pipe(); cmd=self._ffmpeg_command(settings,read_fd); self._restart.clear(); audio_thread=None
            try:
                self._stderr_lines.clear()
                self._process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(read_fd,)); os.close(read_fd); read_fd=-1
                self.started_at=time.time(); self.last_error=None
                stderr_thread=threading.Thread(target=self._stderr_loop,args=(self._process,),name=f"stderr-{self.key}",daemon=True); stderr_thread.start()
                audio_thread=threading.Thread(target=self._alert_audio_loop,args=(write_fd,self._process),name=f"audio-{self.key}",daemon=True); audio_thread.start(); write_fd=-1
                video=settings.get("video") or {}
                render_fps=max(1,int(video.get("render_fps",5)))
                content_fps=max(1,min(render_fps,int(video.get("content_fps",3))))
                preview_interval=max(1,int(video.get("preview_interval_seconds",5)))
                frame_interval=1.0/render_fps; content_interval=1.0/content_fps
                next_frame=time.perf_counter(); next_content=0.0; next_preview=0.0
                frame_bytes=None; image=None
                while not self._stop.is_set() and not self._restart.is_set() and self._process.poll() is None:
                    tick=time.perf_counter()
                    self._update_hls_metrics(force=False)
                    self._maybe_adapt(settings)
                    effective_content_fps = content_fps
                    if self._adaptive_degraded:
                        effective_content_fps = max(1, min(content_fps, int((settings.get("performance") or {}).get("adaptive_content_fps", 2))))
                    content_interval = 1.0 / effective_content_fps
                    if frame_bytes is None or tick >= next_content:
                        started=time.perf_counter(); now=time.time()
                        runtime = self._runtime_render_overrides(settings)
                        runtime.update(self._local8_tick(now))
                        image=self.renderer.render_channel(now, self.location_id, self.mode, runtime_overrides=runtime); frame_bytes=image.tobytes()
                        self._frames_rendered += 1; self._render_seconds_total += time.perf_counter()-started
                        next_content = tick + content_interval
                    if self._process.stdin is None:
                        break
                    self._process.stdin.write(frame_bytes); self._frames_sent += 1
                    if image is not None and tick >= next_preview:
                        self._write_preview(image); next_preview=tick+preview_interval
                    next_frame += frame_interval; delay=next_frame-time.perf_counter()
                    if delay>0:
                        time.sleep(delay)
                    else:
                        if delay < -frame_interval:
                            self._late_frames += 1
                        next_frame=time.perf_counter()
                if self._process.poll() is not None and not self._stop.is_set() and not self._restart.is_set():
                    tail="\n".join(self._stderr_lines)[-3000:]
                    self.last_error=tail.strip() or f"FFmpeg exited with code {self._process.returncode}"
                    if self._active_encoder != "software":
                        self._mark_hardware_failed(requested_encoder, requested_device, self.last_error)
                        immediate_retry = True
            except BrokenPipeError:
                tail="\n".join(self._stderr_lines)[-3000:]
                self.last_error=tail.strip() or "FFmpeg pipe closed."
                if self._active_encoder != "software":
                    self._mark_hardware_failed(requested_encoder, requested_device, self.last_error)
                    immediate_retry = True
            except Exception as exc:
                self.last_error=str(exc)
                if self._active_encoder != "software":
                    self._mark_hardware_failed(requested_encoder, requested_device, self.last_error)
                    immediate_retry = True
            finally:
                if read_fd>=0:
                    try: os.close(read_fd)
                    except OSError: pass
                if write_fd>=0:
                    try: os.close(write_fd)
                    except OSError: pass
                self._terminate_process(); self._process=None
            if not self._stop.is_set():
                if immediate_retry:
                    # Preserve the original on-demand tune request while the same
                    # worker rebuilds FFmpeg with libx264.
                    continue
                time.sleep(2)


class Streamer:
    """Multi-channel supervisor with optional on-demand encoders.

    Data services remain continuously available. Channel workers are kept in a
    catalog, but an on-demand worker only starts its Pillow/FFmpeg pipeline when
    the HLS playlist is requested. It shuts down after the configured idle
    timeout once viewer requests stop.
    """

    def __init__(self, config_store, renderer, tts_manager, tropical_manager=None, event_manager=None) -> None:
        self.config_store = config_store
        self.renderer = renderer
        self.tts_manager = tts_manager
        self.tropical_manager = tropical_manager
        self.event_manager = event_manager
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._workers: dict[str, ChannelWorker] = {}
        self.last_error: str | None = None

    def start(self) -> None:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="multi-channel-supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.join(timeout=5.0)
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5.0)

    def request_restart(self, reason: str = "manual") -> None:
        """Restart active workers only; idle on-demand channels remain idle."""
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            if worker.thread_active() or (worker._process and worker._process.poll() is None):
                worker.request_restart(reason=reason)
        self._wake.set()

    def restart_channel(self, key: str, reason: str = "manual") -> bool:
        """Restart an active channel, or activate an idle on-demand channel."""
        self._reconcile()
        with self._lock:
            worker = self._workers.get(key)
            if not worker:
                return False
            running = worker.thread_active() or bool(worker._process and worker._process.poll() is None)
            worker.note_viewer_activity()
            if running:
                worker.request_restart(reason=reason)
            else:
                worker.start()
        self._wake.set()
        return True

    def start_local8_test(self, key: str) -> bool:
        """Activate a Local channel if needed and start its test block."""
        worker = self.activate_channel(key)
        if worker is None or worker.mode != "local":
            return False
        return worker.start_local8_test()

    def stop_channel(self, key: str) -> bool:
        """Manually stop an on-demand channel. Always-on channels reject this."""
        self._reconcile()
        with self._lock:
            worker = self._workers.get(key)
            if not worker:
                return False
            if worker.lifecycle_mode() == "always_on":
                return False
            worker.stop(idle=True)
        self._wake.set()
        return True

    def is_channel_running(self, key: str) -> bool:
        with self._lock:
            worker = self._workers.get(key)
            return bool(worker and worker._process and worker._process.poll() is None)

    def request_reconfigure(self) -> None:
        self._wake.set()

    def _expected(self) -> dict[str, tuple[str, str, bool]]:
        from app.guide import channel_specs
        settings = self.config_store.get()
        primary_id = settings.get("primary_location_id")
        expected: dict[str, tuple[str, str, bool]] = {}
        for spec in channel_specs(settings):
            loc = spec.get("location") or {}
            lid = loc.get("id")
            if lid:
                expected[spec["key"]] = (lid, spec["mode"], spec["mode"] == "local" and lid == primary_id)
        return expected

    def _reconcile(self) -> None:
        expected = self._expected()
        settings = self.config_store.get()
        channels = settings.get("channels") or {}
        severe_auto = bool(channels.get("severe_auto_start", True))
        tropical_auto = bool((settings.get("tropical") or {}).get("auto_start", True))
        event_auto = bool((settings.get("event_channels") or {}).get("auto_start", True))
        with self._lock:
            for key in list(self._workers):
                worker = self._workers[key]
                spec = expected.get(key)
                if spec is None or worker.location_id != spec[0] or worker.mode != spec[1] or worker.primary_preview != spec[2]:
                    worker.stop()
                    del self._workers[key]
            for key, (lid, mode, primary_preview) in expected.items():
                if key not in self._workers:
                    self._workers[key] = ChannelWorker(self.config_store, self.renderer, self.tts_manager, key, lid, mode, primary_preview)

            workers = list(self._workers.values())

        # Start only workers whose lifecycle policy requires it. Severe can be
        # configured to wake automatically when a qualifying alert exists.
        for worker in workers:
            lifecycle = worker.lifecycle_mode(settings)
            should_run = lifecycle == "always_on"
            if worker.mode == "severe" and severe_auto and self.renderer.takeover_alert_for(worker.location_id) is not None:
                should_run = True
                worker.note_viewer_activity()
            if worker.mode == "tropics" and tropical_auto and self._tropical_activation(worker, settings):
                should_run = True
                worker.note_viewer_activity()
            if worker.mode.startswith("event_") and event_auto and self._event_activation(worker, settings):
                should_run = True; worker.note_viewer_activity()
            if should_run and not worker.thread_active():
                worker.start()

    def activate_channel(self, key: str) -> ChannelWorker | None:
        """Mark viewer activity and ensure an enabled channel is encoding."""
        self._reconcile()
        with self._lock:
            worker = self._workers.get(key)
            if worker is None:
                return None
            worker.note_viewer_activity()
            if not worker.thread_active():
                worker.start()
        self._wake.set()
        return worker

    def note_channel_activity(self, key: str) -> bool:
        with self._lock:
            worker = self._workers.get(key)
            if not worker:
                return False
            worker.note_viewer_activity()
            return True

    def channel_status(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            worker = self._workers.get(key)
        return worker.status() if worker else None

    def startup_timeout(self) -> int:
        settings = self.config_store.get()
        return max(5, min(60, int((settings.get("channels") or {}).get("startup_timeout_seconds", 12))))

    def _tropical_activation(self, worker: ChannelWorker, settings: dict[str, Any]) -> bool:
        if not self.tropical_manager:
            return False
        location=next((loc for loc in settings.get("locations",[]) if loc.get("id")==worker.location_id),None)
        try: alerts=(self.renderer.weather_manager.snapshot_for(worker.location_id).get("alerts") or [])
        except Exception: alerts=[]
        return bool(self.tropical_manager.activation_status(location,alerts).get("active"))

    def _event_activation(self, worker: ChannelWorker, settings: dict[str, Any]) -> bool:
        if not self.event_manager or not worker.mode.startswith("event_"): return False
        from app.network import region_for_location
        region=region_for_location(settings,worker.location_id)
        return bool(region and self.event_manager.evaluate(str(region.get("id")),worker.mode.removeprefix("event_")).get("should_run"))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._reconcile()
                self.last_error = None
                settings = self.config_store.get()
                perf = settings.get("performance") or {}
                channels = settings.get("channels") or {}
                idle_timeout = max(15, int(channels.get("idle_timeout_seconds", 90)))
                severe_auto = bool(channels.get("severe_auto_start", True))
                tropical_auto = bool((settings.get("tropical") or {}).get("auto_start", True))
                event_auto = bool((settings.get("event_channels") or {}).get("auto_start", True))
                now = time.time()
                with self._lock:
                    workers = list(self._workers.values())

                for worker in workers:
                    lifecycle = worker.lifecycle_mode(settings)
                    running = worker.thread_active() or bool(worker._process and worker._process.poll() is None)
                    severe_active = worker.mode == "severe" and severe_auto and self.renderer.takeover_alert_for(worker.location_id) is not None
                    tropical_active = worker.mode == "tropics" and tropical_auto and self._tropical_activation(worker, settings)
                    event_active = worker.mode.startswith("event_") and event_auto and self._event_activation(worker, settings)
                    if severe_active:
                        # Keep the channel alive during the alert and start the normal
                        # idle grace period when the alert finally clears.
                        worker.note_viewer_activity()
                    if tropical_active:
                        worker.note_viewer_activity()
                    if event_active: worker.note_viewer_activity()

                    if lifecycle == "always_on" and not running:
                        worker.start()
                        running = True
                    elif lifecycle == "on_demand" and running and not severe_active and not tropical_active and not event_active:
                        # A manually/scheduled Local on the 8s block is active
                        # programming, even if no player has requested a segment in
                        # the last few seconds. Never idle-stop it mid-narration.
                        local8_active = bool((worker._local8_status() or {}).get("active"))
                        if not local8_active:
                            last_activity = worker.last_viewer_activity or worker.started_at or now
                            if now - last_activity >= idle_timeout:
                                worker.stop(idle=True)
                                continue

                    if not running:
                        continue

                    if perf.get("stall_recovery_enabled", True):
                        stall_seconds = max(8, int(perf.get("stall_seconds", 15)))
                        st = worker.status()
                        age = st.get("playlist_age_seconds")
                        if age is not None and age > stall_seconds:
                            last = worker._last_recovery_at or 0
                            if now - last > max(30, stall_seconds * 2):
                                worker.request_restart(reason=f"self-heal: playlist age {age:.1f}s", recovery=True)
            except Exception as exc:
                self.last_error = str(exc)
            self._wake.wait(timeout=2.0)
            self._wake.clear()

    def diagnostics_text(self) -> str:
        with self._lock:
            workers = list(self._workers.values())
        chunks = [worker.diagnostics_text() for worker in workers]
        if self.last_error:
            chunks.append(f"[supervisor]\n{self.last_error}")
        return "\n\n".join(chunks)

    def status(self) -> dict[str, Any]:
        self._reconcile()
        with self._lock:
            statuses = {key: worker.status() for key, worker in self._workers.items()}
        running = sum(1 for x in statuses.values() if x.get("running"))
        ready = sum(1 for x in statuses.values() if x.get("playlist_ready"))
        total = len(statuses)
        idle = sum(1 for x in statuses.values() if x.get("lifecycle_state") == "IDLE")
        realtime = sum(1 for x in statuses.values() if x.get("realtime_state") == "REALTIME" and x.get("running"))
        degraded = sum(1 for x in statuses.values() if x.get("running") and x.get("realtime_state") in {"DEGRADED", "FALLING BEHIND", "STALLED"})
        errors = [f"{k}: {v.get('last_error')}" for k, v in statuses.items() if v.get("last_error") and v.get("running")]
        # Idle on-demand channels are healthy by design. Only active/always-on
        # channels need a live FFmpeg process.
        unhealthy = degraded > 0 or bool(errors)
        settings = self.config_store.get()
        channels_cfg = settings.get("channels") or {}
        return {
            "running": not unhealthy,
            "running_channels": running,
            "active_encoders": running,
            "idle_channels": idle,
            "channel_count": total,
            "ready_channels": ready,
            "realtime_channels": realtime,
            "degraded_channels": degraded,
            "playlist_ready": running == 0 or ready == running,
            "last_error": self.last_error or (errors[0] if errors else None),
            "alert_audio": "enabled" if settings.get("alerts", {}).get("chime_enabled", True) else "disabled",
            "tts": self.tts_manager.status(settings),
            "performance_mode": (settings.get("performance") or {}).get("mode", "adaptive"),
            "default_streaming_mode": channels_cfg.get("streaming_mode", "on_demand"),
            "idle_timeout_seconds": channels_cfg.get("idle_timeout_seconds", 90),
            "startup_timeout_seconds": channels_cfg.get("startup_timeout_seconds", 12),
            "severe_auto_start": channels_cfg.get("severe_auto_start", True),
            "tropics_auto_start": (settings.get("tropical") or {}).get("auto_start", True),
            "encoder_capabilities": encoder_capabilities(),
            "channels": statuses,
        }
