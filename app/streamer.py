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

LIVE_DIR = Path(os.environ.get("WEATHERSTREAM_LIVE", "/tmp/weatherstream/live"))
MUSIC_DIR = Path(os.environ.get("WEATHERSTREAM_MUSIC", "/music"))
PREVIEW_PATH = Path(os.environ.get("WEATHERSTREAM_PREVIEW", "/tmp/weatherstream/preview.jpg"))
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"}

_ENCODER_CAPS: dict[str, Any] | None = None

def encoder_capabilities(force: bool = False) -> dict[str, Any]:
    global _ENCODER_CAPS
    if _ENCODER_CAPS is not None and not force:
        return dict(_ENCODER_CAPS)
    text = ""
    try:
        text = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=8).stdout
    except Exception:
        pass
    dri = Path("/dev/dri/renderD128").exists()
    nvdev = Path("/dev/nvidia0").exists()
    _ENCODER_CAPS = {
        "software": {"available": True, "encoder": "libx264"},
        "nvenc": {"available": "h264_nvenc" in text and nvdev, "compiled": "h264_nvenc" in text, "device": nvdev, "encoder": "h264_nvenc"},
        "qsv": {"available": "h264_qsv" in text and dri, "compiled": "h264_qsv" in text, "device": dri, "encoder": "h264_qsv"},
        "vaapi": {"available": "h264_vaapi" in text and dri, "compiled": "h264_vaapi" in text, "device": dri, "encoder": "h264_vaapi"},
    }
    return dict(_ENCODER_CAPS)

def _choose_encoder(requested: str, hardware_failed: bool = False) -> str:
    if hardware_failed:
        return "software"
    requested = requested if requested in {"auto", "software", "nvenc", "qsv", "vaapi"} else "software"
    caps = encoder_capabilities()
    if requested == "auto":
        for name in ("qsv", "vaapi", "nvenc"):
            if (caps.get(name) or {}).get("available"):
                return name
        return "software"
    if requested != "software" and not (caps.get(requested) or {}).get("available"):
        return "software"
    return requested


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
        self._hardware_failed = False
        self._active_encoder = "software"
        self.last_viewer_activity: float | None = None
        self.last_idle_at: float | None = None
        self.activation_count = 0

    @property
    def spec(self) -> tuple[str, str, str]:
        return (self.key, self.location_id, self.mode)

    def start(self) -> None:
        """Start this channel encoder if it is not already active.

        v0.2.2 workers are reusable: an on-demand worker can stop after its idle
        timeout and later be started again by a new HLS playlist request.
        """
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

    def stop(self, idle: bool = False) -> None:
        self._stop.set()
        if idle:
            self.last_idle_at = time.time()
        self._terminate_process()

    def note_viewer_activity(self) -> None:
        self.last_viewer_activity = time.time()

    def thread_active(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def request_restart(self, reason: str = "manual", recovery: bool = False) -> None:
        self._last_restart_reason = reason
        self._restart_count += 1
        if recovery:
            self._recovery_count += 1
            self._last_recovery_at = time.time()
        self._restart.set()
        self._terminate_process()

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
        for key in ("encoder", "output_fps", "content_fps", "bitrate"):
            if key in override:
                video[key] = override[key]
        return settings

    def _runtime_render_overrides(self, settings: dict[str, Any]) -> dict[str, Any]:
        override = self._channel_override(settings)
        runtime = {k: override[k] for k in ("theme", "retro_enabled", "transition") if k in override}
        if self._adaptive_degraded:
            runtime["performance_degraded"] = True
        return runtime

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
            "encoder": self._active_encoder,
            "requested_encoder": str(video.get("encoder", "software")),
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
        lines = [f"[{self.key}] encoder={self._active_encoder} state={'DEGRADED' if self._adaptive_degraded else 'NORMAL'}"]
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
        files = [p for p in MUSIC_DIR.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
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
        requested_encoder = str(video.get("encoder", "software")); self._active_encoder = _choose_encoder(requested_encoder, self._hardware_failed)
        cmd = ["ffmpeg","-hide_banner","-loglevel","warning","-y"]
        if self._active_encoder == "vaapi": cmd += ["-vaapi_device", "/dev/dri/renderD128"]
        elif self._active_encoder == "qsv": cmd += ["-init_hw_device", "qsv=hw:/dev/dri/renderD128", "-filter_hw_device", "hw"]
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
        """Feed the auxiliary PCM bus with chimes and tightly-scoped narration.

        v0.2.2 TTS is intentionally restricted to two situations:
        1) one concise forecast narration per Local on the 8s scheduled block, and
        2) one narration per new qualifying severe-weather takeover alert.
        All synthesis happens on background threads inside TTSManager; this loop
        never stops feeding PCM while Piper is working.
        """
        chunk_frames = 2205
        silence = b"\x00\x00\x00\x00" * chunk_frames
        gap = b"\x00\x00\x00\x00" * int(44100 * 0.30)
        raw_chime = self._make_chime()
        pending = b""
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

                        if active and bool(alerts_cfg.get("chime_enabled", True)) and signature != last_chime_signature:
                            chime = self._scale_pcm(raw_chime, float(alerts_cfg.get("chime_volume", 0.65)))
                            pending += chime
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
                                self.last_tts_alert_id = signature

                        # Local narration only belongs to the scheduled Local on the
                        # 8s block. A severe takeover always wins and suppresses it.
                        if (
                            not active
                            and self.mode == "local"
                            and bool(tts_cfg.get("enabled", False))
                            and bool(tts_cfg.get("local_on_8s", True))
                        ):
                            context = self.renderer.narration_context_for(self.location_id, "local", time.time())
                            block_id = context.get("scheduled_block_id")
                            if context.get("scheduled_update_active") and block_id and block_id != self.last_tts_local_block_id:
                                text = self.tts_manager.local_on_8s_text(context.get("primary") or {}, settings)
                                speech = self.tts_manager.request_pcm(text, settings, "local_on_8s")
                                if speech:
                                    pending += speech
                                    self.last_tts_local_block_id = str(block_id)

                        next_check = mono + 1.0

                    need = len(silence)
                    if pending:
                        block = pending[:need]
                        pending = pending[need:]
                        if len(block) < need:
                            block += silence[:need-len(block)]
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

    def _run(self) -> None:
        while not self._stop.is_set():
            settings=self._effective_settings(self.config_store.get()); self._clean_live(); read_fd,write_fd=os.pipe(); cmd=self._ffmpeg_command(settings,read_fd); self._restart.clear(); audio_thread=None
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
                        started=time.perf_counter(); now=time.time(); image=self.renderer.render_channel(now, self.location_id, self.mode, runtime_overrides=self._runtime_render_overrides(settings)); frame_bytes=image.tobytes()
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
                    if self._active_encoder != "software" and self.started_at and time.time() - self.started_at < 20:
                        self._hardware_failed = True
                        self.last_error = f"{self.last_error} | hardware encoder failed; falling back to libx264"
            except BrokenPipeError:
                tail="\n".join(self._stderr_lines)[-3000:]
                self.last_error=tail.strip() or "FFmpeg pipe closed."
            except Exception as exc:
                self.last_error=str(exc)
            finally:
                if read_fd>=0:
                    try: os.close(read_fd)
                    except OSError: pass
                if write_fd>=0:
                    try: os.close(write_fd)
                    except OSError: pass
                self._terminate_process(); self._process=None
            if not self._stop.is_set(): time.sleep(2)


class Streamer:
    """v0.2.2 multi-channel supervisor with optional on-demand encoders.

    Data services remain continuously available. Channel workers are kept in a
    catalog, but an on-demand worker only starts its Pillow/FFmpeg pipeline when
    the HLS playlist is requested. It shuts down after the configured idle
    timeout once viewer requests stop.
    """

    def __init__(self, config_store, renderer, tts_manager) -> None:
        self.config_store = config_store
        self.renderer = renderer
        self.tts_manager = tts_manager
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
                now = time.time()
                with self._lock:
                    workers = list(self._workers.values())

                for worker in workers:
                    lifecycle = worker.lifecycle_mode(settings)
                    running = worker.thread_active() or bool(worker._process and worker._process.poll() is None)
                    severe_active = worker.mode == "severe" and severe_auto and self.renderer.takeover_alert_for(worker.location_id) is not None
                    if severe_active:
                        # Keep the channel alive during the alert and start the normal
                        # idle grace period when the alert finally clears.
                        worker.note_viewer_activity()

                    if lifecycle == "always_on" and not running:
                        worker.start()
                        running = True
                    elif lifecycle == "on_demand" and running and not severe_active:
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
            "encoder_capabilities": encoder_capabilities(),
            "channels": statuses,
        }
