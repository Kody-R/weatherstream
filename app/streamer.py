from __future__ import annotations

import math
import os
import random
import struct
import subprocess
import threading
import time
from pathlib import Path

LIVE_DIR = Path(os.environ.get("WEATHERSTREAM_LIVE", "/tmp/weatherstream/live"))
MUSIC_DIR = Path(os.environ.get("WEATHERSTREAM_MUSIC", "/music"))
PREVIEW_PATH = Path(os.environ.get("WEATHERSTREAM_PREVIEW", "/tmp/weatherstream/preview.jpg"))

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus"}


class Streamer:
    def __init__(self, config_store, renderer) -> None:
        self.config_store = config_store
        self.renderer = renderer
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self.last_error: str | None = None
        self.started_at: float | None = None
        self.last_chime_alert_id: str | None = None

    def start(self) -> None:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="streamer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate_process()

    def request_restart(self) -> None:
        self._restart.set()
        self._terminate_process()

    def status(self):
        proc = self._process
        return {
            "running": bool(proc and proc.poll() is None),
            "pid": proc.pid if proc and proc.poll() is None else None,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "playlist_ready": (LIVE_DIR / "weather.m3u8").exists(),
            "alert_audio": "enabled" if self.config_store.get().get("alerts", {}).get("chime_enabled", True) else "disabled",
            "last_chime_alert_id": self.last_chime_alert_id,
        }

    def _terminate_process(self):
        proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _music_playlist(self, settings):
        music = settings.get("music", {})
        if not music.get("enabled", True) or not MUSIC_DIR.exists():
            return None
        files = [p for p in MUSIC_DIR.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
        if not files:
            return None
        if music.get("shuffle", True):
            random.shuffle(files)
        playlist = LIVE_DIR.parent / "music.ffconcat"
        with playlist.open("w", encoding="utf-8") as fh:
            fh.write("ffconcat version 1.0\n")
            for path in files:
                escaped = str(path).replace("'", "'\\''")
                fh.write(f"file '{escaped}'\n")
        return playlist

    def _ffmpeg_command(self, settings, alert_audio_fd: int):
        video = settings["video"]
        w, h = int(video["width"]), int(video["height"])
        render_fps = int(video.get("render_fps", 10))
        output_fps = int(video.get("output_fps", 30))
        seg = int(video.get("hls_segment_seconds", 2))
        list_size = int(video.get("hls_list_size", 6))
        bitrate = str(video.get("bitrate", "2500k"))
        music_playlist = self._music_playlist(settings)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(render_fps), "-i", "pipe:0",
        ]
        if music_playlist:
            cmd += ["-stream_loop", "-1", "-f", "concat", "-safe", "0", "-i", str(music_playlist)]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]

        # A dedicated PCM pipe lets WeatherStream inject an alert chime without
        # restarting FFmpeg or interrupting HLS playback.
        cmd += [
            "-thread_queue_size", "256", "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", f"pipe:{alert_audio_fd}"
        ]

        gop = output_fps * seg
        music_volume = float(settings.get("music", {}).get("volume", 0.30)) if music_playlist else 0.0
        chime_volume = float(settings.get("alerts", {}).get("chime_volume", 0.65))
        filter_complex = (
            f"[1:a]volume={music_volume:.3f}[bg];"
            f"[2:a]volume={chime_volume:.3f}[ch];"
            "[bg][ch]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
        )
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "0:v:0", "-map", "[aout]",
            "-vf", f"fps={output_fps},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage",
            "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", "5000k",
            "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-f", "hls", "-hls_time", str(seg), "-hls_list_size", str(list_size),
            "-hls_flags", "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_filename", str(LIVE_DIR / "segment_%06d.ts"),
            str(LIVE_DIR / "weather.m3u8"),
        ]
        return cmd

    def _make_chime(self, sample_rate: int = 44100) -> bytes:
        # Broadcast-style two-tone attention chime generated locally; no bundled audio asset required.
        sequence = [(880.0, 0.28), (0.0, 0.08), (660.0, 0.34), (0.0, 0.10), (880.0, 0.24)]
        out = bytearray()
        amplitude = 10500
        phase = 0.0
        for freq, duration in sequence:
            frames = int(sample_rate * duration)
            for i in range(frames):
                if freq <= 0:
                    sample = 0
                else:
                    # Gentle fade prevents clicks between tones.
                    edge = max(1, int(sample_rate * 0.015))
                    envelope = min(1.0, i / edge, (frames - i - 1) / edge)
                    sample = int(amplitude * max(0.0, envelope) * math.sin(phase))
                    phase += 2.0 * math.pi * freq / sample_rate
                packed = struct.pack("<h", sample)
                out.extend(packed)
                out.extend(packed)
        return bytes(out)

    def _alert_audio_loop(self, write_fd: int, proc: subprocess.Popen):
        sample_rate = 44100
        chunk_frames = 2205  # 50 ms stereo PCM chunks
        silence = b"\x00\x00\x00\x00" * chunk_frames
        chime = self._make_chime(sample_rate)
        pending = b""
        last_signature: str | None = None
        try:
            with os.fdopen(write_fd, "wb", buffering=0) as pipe:
                while not self._stop.is_set() and not self._restart.is_set() and proc.poll() is None:
                    settings = self.config_store.get()
                    snapshot = self.renderer.weather_manager.snapshot()
                    active = self.renderer._takeover_alert(settings, snapshot)
                    signature = str(active.get("id") or active.get("headline") or active.get("event")) if active else None
                    if not active:
                        last_signature = None
                    elif settings.get("alerts", {}).get("chime_enabled", True) and signature != last_signature:
                        pending += chime
                        last_signature = signature
                        self.last_chime_alert_id = signature

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

    def _clean_live(self):
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        for p in LIVE_DIR.glob("segment_*.ts"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            (LIVE_DIR / "weather.m3u8").unlink()
        except FileNotFoundError:
            pass

    def _run(self):
        while not self._stop.is_set():
            settings = self.config_store.get()
            self._clean_live()
            read_fd, write_fd = os.pipe()
            cmd = self._ffmpeg_command(settings, read_fd)
            self._restart.clear()
            audio_thread = None
            try:
                self._process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, pass_fds=(read_fd,))
                os.close(read_fd)
                read_fd = -1
                self.started_at = time.time()
                self.last_error = None
                audio_thread = threading.Thread(
                    target=self._alert_audio_loop, args=(write_fd, self._process), name="alert-audio", daemon=True
                )
                audio_thread.start()
                write_fd = -1  # owned by the audio thread now

                render_fps = max(1, int(settings["video"].get("render_fps", 10)))
                frame_interval = 1.0 / render_fps
                next_frame = time.perf_counter()
                preview_counter = 0

                while not self._stop.is_set() and not self._restart.is_set() and self._process.poll() is None:
                    now = time.time()
                    image = self.renderer.render(now)
                    self._process.stdin.write(image.tobytes())
                    preview_counter += 1
                    if preview_counter >= render_fps:
                        preview_counter = 0
                        tmp = PREVIEW_PATH.with_suffix(".tmp.jpg")
                        image.save(tmp, format="JPEG", quality=82)
                        tmp.replace(PREVIEW_PATH)
                    next_frame += frame_interval
                    delay = next_frame - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
                    else:
                        next_frame = time.perf_counter()

                if self._process.poll() is not None and not self._stop.is_set() and not self._restart.is_set():
                    stderr = self._process.stderr.read().decode("utf-8", errors="replace")[-3000:]
                    self.last_error = stderr.strip() or f"FFmpeg exited with code {self._process.returncode}"
            except BrokenPipeError:
                if self._process:
                    try:
                        stderr = self._process.stderr.read().decode("utf-8", errors="replace")[-3000:]
                        self.last_error = stderr.strip() or "FFmpeg pipe closed."
                    except Exception:
                        self.last_error = "FFmpeg pipe closed."
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                if read_fd >= 0:
                    try: os.close(read_fd)
                    except OSError: pass
                if write_fd >= 0:
                    try: os.close(write_fd)
                    except OSError: pass
                self._terminate_process()
                self._process = None
            if not self._stop.is_set():
                time.sleep(2)
