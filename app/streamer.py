from __future__ import annotations

import math
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


def location_channel_key(location: dict[str, Any]) -> str:
    postal = "".join(ch for ch in str(location.get("postal_code") or "local") if ch.isdigit())[:5]
    return f"zip-{postal or str(location.get('id') or 'local')[:10]}"


class ChannelWorker:
    """One H.264/AAC HLS encoder for a logical RWN channel."""

    def __init__(self, config_store, renderer, key: str, location_id: str, mode: str, primary_preview: bool = False) -> None:
        self.config_store = config_store
        self.renderer = renderer
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

    @property
    def spec(self) -> tuple[str, str, str]:
        return (self.key, self.location_id, self.mode)

    def start(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.preview_path.parent.mkdir(parents=True, exist_ok=True)
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=f"stream-{self.key}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._terminate_process()

    def request_restart(self) -> None:
        self._restart.set()
        self._terminate_process()

    def status(self) -> dict[str, Any]:
        proc = self._process
        self._update_hls_metrics(force=False)
        now = time.time()
        playlist_age = (now - self._playlist_mtime) if self._playlist_mtime else None
        settings = self.config_store.get(); video = settings.get("video") or {}
        seg = max(1, int(video.get("hls_segment_seconds", 3)))
        ratio = self._realtime_ratio
        if playlist_age is not None and playlist_age > seg * 4:
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
            "running": bool(proc and proc.poll() is None),
            "pid": proc.pid if proc and proc.poll() is None else None,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "playlist_ready": (self.output_dir / "index.m3u8").exists(),
            "path": f"/live/{self.key}/index.m3u8",
            "last_chime_alert_id": self.last_chime_alert_id,
            "media_sequence": self._media_sequence,
            "latest_segment": self._latest_segment,
            "playlist_age_seconds": round(playlist_age, 2) if playlist_age is not None else None,
            "realtime_ratio": round(ratio, 3) if ratio is not None else None,
            "realtime_state": realtime_state,
            "producer": {
                "render_fps": int(video.get("render_fps", 5)),
                "content_fps": int(video.get("content_fps", 3)),
                "output_fps": int(video.get("output_fps", 15)),
                "frames_sent": self._frames_sent,
                "frames_rendered": self._frames_rendered,
                "late_frames": self._late_frames,
                "average_render_ms": round(avg_render_ms, 1) if avg_render_ms is not None else None,
            },
        }

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
        cmd = ["ffmpeg","-hide_banner","-loglevel","warning","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{w}x{h}","-r",str(render_fps),"-i","pipe:0"]
        if music_playlist:
            cmd += ["-stream_loop","-1","-f","concat","-safe","0","-i",str(music_playlist)]
        else:
            cmd += ["-f","lavfi","-i","anullsrc=r=44100:cl=stereo"]
        cmd += ["-thread_queue_size","256","-f","s16le","-ar","44100","-ac","2","-i",f"pipe:{alert_audio_fd}"]
        gop = output_fps * seg
        music_volume = float(settings.get("music",{}).get("volume",0.30)) if music_playlist else 0.0
        chime_volume = float(settings.get("alerts",{}).get("chime_volume",0.65))
        filt = f"[1:a]volume={music_volume:.3f}[bg];[2:a]volume={chime_volume:.3f}[ch];[bg][ch]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
        cmd += [
            "-filter_complex",filt,"-map","0:v:0","-map","[aout]",
            "-vf",f"fps={output_fps},format=yuv420p","-c:v","libx264","-preset",preset,"-tune","stillimage",
            "-b:v",bitrate,"-maxrate",bitrate,"-bufsize","4000k","-g",str(gop),"-keyint_min",str(gop),"-sc_threshold","0",
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

    def _alert_audio_loop(self, write_fd: int, proc: subprocess.Popen) -> None:
        # Keep the PCM pipe continuously fed, but do not rebuild configuration and
        # weather snapshots 20 times per second. Alert state only needs sub-second/
        # one-second responsiveness for a TV chime.
        chunk_frames=2205; silence=b"\x00\x00\x00\x00"*chunk_frames; chime=self._make_chime(); pending=b""; last_signature=None
        settings=self.config_store.get(); chime_enabled=bool((settings.get("alerts") or {}).get("chime_enabled",True))
        active=None; signature=None; next_alert_check=0.0
        try:
            with os.fdopen(write_fd,"wb",buffering=0) as pipe:
                while not self._stop.is_set() and not self._restart.is_set() and proc.poll() is None:
                    mono=time.monotonic()
                    if mono >= next_alert_check:
                        active=self.renderer.takeover_alert_for(self.location_id)
                        signature=str(active.get("id") or active.get("headline") or active.get("event")) if active else None
                        if not active:
                            last_signature=None
                        elif chime_enabled and signature != last_signature:
                            pending += chime; last_signature=signature; self.last_chime_alert_id=signature
                        next_alert_check=mono+1.0
                    need=len(silence)
                    if pending:
                        block=pending[:need]; pending=pending[need:]
                        if len(block)<need: block += silence[:need-len(block)]
                    else: block=silence
                    try: pipe.write(block)
                    except (BrokenPipeError,OSError): break
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
            settings=self.config_store.get(); self._clean_live(); read_fd,write_fd=os.pipe(); cmd=self._ffmpeg_command(settings,read_fd); self._restart.clear(); audio_thread=None
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
                    if frame_bytes is None or tick >= next_content:
                        started=time.perf_counter(); now=time.time(); image=self.renderer.render_channel(now, self.location_id, self.mode); frame_bytes=image.tobytes()
                        self._frames_rendered += 1; self._render_seconds_total += time.perf_counter()-started
                        next_content = tick + content_interval
                    if self._process.stdin is None:
                        break
                    self._process.stdin.write(frame_bytes); self._frames_sent += 1
                    if image is not None and tick >= next_preview:
                        self._write_preview(image); next_preview=tick+preview_interval
                    self._update_hls_metrics(force=False)
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
    """v0.1.8.1 multi-channel supervisor.

    A worker is created automatically for every configured ZIP, plus RWN Radar
    and RWN Severe channels centered on the selected primary ZIP.
    """

    def __init__(self, config_store, renderer) -> None:
        self.config_store=config_store; self.renderer=renderer
        self._stop=threading.Event(); self._wake=threading.Event(); self._lock=threading.RLock(); self._thread:threading.Thread|None=None
        self._workers:dict[str,ChannelWorker]={}; self.last_error:str|None=None

    def start(self) -> None:
        LIVE_DIR.mkdir(parents=True,exist_ok=True); PREVIEW_PATH.parent.mkdir(parents=True,exist_ok=True)
        if self._thread and self._thread.is_alive(): return
        self._thread=threading.Thread(target=self._run,name="multi-channel-supervisor",daemon=True); self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()
        with self._lock:
            workers=list(self._workers.values())
        for worker in workers: worker.stop()

    def request_restart(self) -> None:
        with self._lock:
            for worker in self._workers.values(): worker.request_restart()
        self._wake.set()

    def request_reconfigure(self) -> None:
        self._wake.set()

    def _expected(self) -> dict[str, tuple[str,str,bool]]:
        settings=self.config_store.get(); cfg=settings.get("channels") or {}; locations=list(settings.get("locations") or [])
        max_zip=max(1,min(24,int(cfg.get("max_zip_channels",12)))); primary_id=settings.get("primary_location_id")
        expected:dict[str,tuple[str,str,bool]]={}
        if cfg.get("per_zip_enabled",True):
            for loc in locations[:max_zip]:
                lid=loc.get("id")
                if lid: expected[location_channel_key(loc)]=(lid,"local",lid==primary_id)
        if primary_id and cfg.get("radar_enabled",True): expected["radar"]=(primary_id,"radar",False)
        if primary_id and cfg.get("severe_enabled",True): expected["severe"]=(primary_id,"severe",False)
        return expected

    def _reconcile(self) -> None:
        expected=self._expected()
        with self._lock:
            for key in list(self._workers):
                worker=self._workers[key]; spec=expected.get(key)
                if spec is None or worker.location_id != spec[0] or worker.mode != spec[1] or worker.primary_preview != spec[2]:
                    worker.stop(); del self._workers[key]
            for key,(lid,mode,primary_preview) in expected.items():
                if key not in self._workers:
                    worker=ChannelWorker(self.config_store,self.renderer,key,lid,mode,primary_preview); self._workers[key]=worker; worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try: self._reconcile(); self.last_error=None
            except Exception as exc: self.last_error=str(exc)
            self._wake.wait(timeout=3.0); self._wake.clear()

    def status(self) -> dict[str,Any]:
        with self._lock: statuses={key:w.status() for key,w in self._workers.items()}
        running=sum(1 for x in statuses.values() if x.get("running")); ready=sum(1 for x in statuses.values() if x.get("playlist_ready")); total=len(statuses)
        realtime=sum(1 for x in statuses.values() if x.get("realtime_state") == "REALTIME")
        degraded=sum(1 for x in statuses.values() if x.get("realtime_state") in {"DEGRADED", "FALLING BEHIND", "STALLED"})
        errors=[f"{k}: {v.get('last_error')}" for k,v in statuses.items() if v.get("last_error")]
        return {
            "running": total>0 and running==total,
            "running_channels": running,
            "channel_count": total,
            "ready_channels": ready,
            "realtime_channels": realtime,
            "degraded_channels": degraded,
            "playlist_ready": total>0 and ready==total,
            "last_error": self.last_error or (errors[0] if errors else None),
            "alert_audio": "enabled" if self.config_store.get().get("alerts",{}).get("chime_enabled",True) else "disabled",
            "channels": statuses,
        }
