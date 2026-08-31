from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.config import CONFIG_DIR

TTS_ROOT = Path(os.environ.get("WEATHERSTREAM_TTS", str(CONFIG_DIR / "tts")))
VOICE_DIR = TTS_ROOT / "voices"
CACHE_DIR = TTS_ROOT / "cache"


def _clean_text(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].strip()


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_int(value: Any) -> int | None:
    number = _num(value)
    return int(round(number)) if number is not None else None


class TTSManager:
    """Small, optional Piper TTS service for broadcast announcements.

    Synthesis is intentionally asynchronous for live channels. The audio loop keeps
    feeding silence while a new sentence is generated, then inserts the cached PCM
    when it becomes available. Forecast/alert text hashes are cached on disk so a
    repeated announcement does not need to be synthesized again.
    """

    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self._lock = threading.RLock()
        self._voice_lock = threading.Lock()
        workers = max(1, min(2, int(os.environ.get("WEATHERSTREAM_TTS_WORKERS", "1"))))
        queue_size = max(workers, min(64, int(os.environ.get("WEATHERSTREAM_TTS_QUEUE_SIZE", "24"))))
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="weatherstream-tts")
        self._queue_slots = threading.BoundedSemaphore(queue_size)
        self._tts_workers = workers
        self._queue_size = queue_size
        self._dropped_requests = 0
        self._inflight: set[str] = set()
        self._memory_pcm: dict[str, bytes] = {}
        self._last_error: str | None = None
        self._last_success: float | None = None
        self._last_text_kind: str | None = None
        self._synth_count = 0
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _cfg(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        source = settings if settings is not None else self.config_store.get()
        return dict(source.get("tts") or {})

    def _voice_name(self, settings: dict[str, Any] | None = None) -> str:
        value = _clean_text(self._cfg(settings).get("voice") or "en_US-lessac-medium", 96)
        return value if re.fullmatch(r"[A-Za-z0-9_.-]+", value) else "en_US-lessac-medium"

    def _voice_model_path(self, settings: dict[str, Any] | None = None) -> Path:
        return VOICE_DIR / f"{self._voice_name(settings)}.onnx"

    def package_available(self) -> bool:
        return importlib.util.find_spec("piper") is not None

    def voice_installed(self, settings: dict[str, Any] | None = None) -> bool:
        model = self._voice_model_path(settings)
        return model.exists() and Path(f"{model}.json").exists()

    def ensure_voice(self, settings: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
        with self._voice_lock:
            cfg = self._cfg(settings)
            voice = self._voice_name(settings)
            if not self.package_available():
                raise RuntimeError("Piper is not installed in this WeatherStream image.")
            if self.voice_installed(settings) and not force:
                return self.status(settings)
            if not bool(cfg.get("auto_download_voice", True)) and not force:
                raise RuntimeError(f"Piper voice {voice} is not installed and automatic download is disabled.")
            VOICE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "piper.download_voices", "--data-dir", str(VOICE_DIR), voice],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
            except Exception as exc:
                with self._lock:
                    self._last_error = f"Voice download failed: {exc}"
                raise RuntimeError(self._last_error) from exc
            if proc.returncode != 0 or not self.voice_installed(settings):
                detail = (proc.stderr or proc.stdout or "voice files were not created").strip()[-1200:]
                with self._lock:
                    self._last_error = f"Voice download failed: {detail}"
                raise RuntimeError(self._last_error)
            with self._lock:
                self._last_error = None
            return self.status(settings)

    def _cache_key(self, text: str, settings: dict[str, Any], kind: str) -> str:
        cfg = self._cfg(settings)
        payload = "|".join([
            self._voice_name(settings),
            f"{float(cfg.get('speed', 1.0)):.3f}",
            f"{float(cfg.get('volume', 0.92)):.3f}",
            kind,
            text,
        ])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def _pcm_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.s16le"

    def _wav_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.wav"

    def _run_piper(self, text: str, settings: dict[str, Any], wav_path: Path) -> None:
        self.ensure_voice(settings)
        cfg = self._cfg(settings)
        voice = self._voice_name(settings)
        speed = max(0.70, min(1.35, float(cfg.get("speed", 1.0))))
        # Piper's length-scale is inverse-ish from what users expect: below 1.0 is
        # faster, above 1.0 is slower. Expose "speed" as the intuitive multiplier.
        length_scale = 1.0 / speed
        proc = subprocess.run(
            [
                sys.executable, "-m", "piper", "-m", voice,
                "--data-dir", str(VOICE_DIR), "-f", str(wav_path),
                "--length-scale", f"{length_scale:.4f}",
            ],
            input=text + "\n",
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0 or not wav_path.exists():
            detail = (proc.stderr or proc.stdout or "Piper did not create a WAV file").strip()[-1600:]
            raise RuntimeError(f"Piper synthesis failed: {detail}")

    def _wav_to_pcm(self, wav_path: Path, pcm_path: Path, gain: float = 1.0) -> None:
        tmp = pcm_path.with_suffix(".tmp")
        gain = max(0.0, min(1.5, float(gain)))
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(wav_path), "-filter:a", f"volume={gain:.3f}",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", "44100", "-ac", "2", str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0 or not tmp.exists():
            detail = (proc.stderr or "FFmpeg failed to convert Piper audio").strip()[-1200:]
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise RuntimeError(detail)
        tmp.replace(pcm_path)

    def _build_cache(self, key: str, text: str, settings: dict[str, Any], kind: str) -> None:
        wav_path = self._wav_path(key)
        pcm_path = self._pcm_path(key)
        try:
            self._run_piper(text, settings, wav_path)
            self._wav_to_pcm(wav_path, pcm_path, float(self._cfg(settings).get("volume", 0.92)))
            try:
                wav_path.unlink()
            except OSError:
                pass
            pcm = pcm_path.read_bytes()
            with self._lock:
                self._memory_pcm[key] = pcm
                self._last_error = None
                self._last_success = time.time()
                self._last_text_kind = kind
                self._synth_count += 1
            self._prune_cache(settings)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            with self._lock:
                self._inflight.discard(key)
            self._queue_slots.release()

    def request_pcm(self, text: str, settings: dict[str, Any], kind: str) -> bytes | None:
        cfg = self._cfg(settings)
        if not bool(cfg.get("enabled", False)):
            return None
        text = _clean_text(text)
        if not text:
            return None
        key = self._cache_key(text, settings, kind)
        with self._lock:
            cached = self._memory_pcm.get(key)
        if cached:
            return cached
        pcm_path = self._pcm_path(key)
        if pcm_path.exists():
            try:
                pcm = pcm_path.read_bytes()
                with self._lock:
                    self._memory_pcm[key] = pcm
                return pcm
            except OSError:
                pass
        with self._lock:
            if key in self._inflight:
                return None
            if not self._queue_slots.acquire(blocking=False):
                self._dropped_requests += 1
                self._last_error = "TTS queue is full; a stale narration request was dropped."
                return None
            self._inflight.add(key)
        try:
            self._executor.submit(self._build_cache, key, text, settings, kind)
        except Exception:
            with self._lock:
                self._inflight.discard(key)
            self._queue_slots.release()
            raise
        return None

    def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def synthesize_wav_bytes(self, text: str, settings: dict[str, Any] | None = None) -> bytes:
        settings = settings or self.config_store.get()
        text = _clean_text(text, 700)
        if not text:
            raise ValueError("TTS test text is empty.")
        TTS_ROOT.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="weatherstream-tts-", suffix=".wav", dir=str(TTS_ROOT))
        os.close(fd)
        wav_path = Path(temp_name)
        try:
            self._run_piper(text, settings, wav_path)
            data = wav_path.read_bytes()
            with self._lock:
                self._last_error = None
                self._last_success = time.time()
                self._last_text_kind = "test"
                self._synth_count += 1
            return data
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            raise
        finally:
            try:
                wav_path.unlink()
            except FileNotFoundError:
                pass

    def _prune_cache(self, settings: dict[str, Any] | None = None) -> None:
        cfg = self._cfg(settings)
        keep = max(8, min(256, int(cfg.get("cache_items", 64))))
        files = sorted(CACHE_DIR.glob("*.s16le"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[keep:]:
            key = path.stem
            try:
                path.unlink()
            except OSError:
                pass
            try:
                self._wav_path(key).unlink()
            except OSError:
                pass
            with self._lock:
                self._memory_pcm.pop(key, None)

    def status(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = self._cfg(settings)
        voice = self._voice_name(settings)
        with self._lock:
            inflight = len(self._inflight)
            last_error = self._last_error
            last_success = self._last_success
            last_kind = self._last_text_kind
            synth_count = self._synth_count
            dropped_requests = self._dropped_requests
        cache_files = list(CACHE_DIR.glob("*.s16le")) if CACHE_DIR.exists() else []
        cache_bytes = 0
        for path in cache_files:
            try:
                cache_bytes += path.stat().st_size
            except OSError:
                pass
        return {
            "enabled": bool(cfg.get("enabled", False)),
            "provider": "piper",
            "package_available": self.package_available(),
            "voice": voice,
            "voice_installed": self.voice_installed(settings),
            "auto_download_voice": bool(cfg.get("auto_download_voice", True)),
            "local_on_8s": bool(cfg.get("local_on_8s", True)),
            "severe_alerts": bool(cfg.get("severe_alerts", True)),
            "duck_music": bool(cfg.get("duck_music", True)),
            "inflight": inflight,
            "workers": self._tts_workers,
            "queue_size": self._queue_size,
            "dropped_requests": dropped_requests,
            "cache_items": len(cache_files),
            "cache_bytes": cache_bytes,
            "synth_count": synth_count,
            "last_success": last_success,
            "last_kind": last_kind,
            "last_error": last_error,
        }

    def _local8_location_label(self, primary: dict[str, Any]) -> str:
        location = primary.get("location") or {}
        name = _clean_text(location.get("name") or location.get("postal_code") or "your area", 80)
        admin1 = _clean_text(location.get("admin1") or "", 60)
        return f"{name}, {admin1}" if admin1 else name

    def _local8_hour_indices(self, primary: dict[str, Any]) -> list[int]:
        hourly = primary.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return []
        now_local = (primary.get("current") or {}).get("time")
        start = 0
        if now_local in times:
            start = times.index(now_local)
        else:
            try:
                import datetime as _dt
                target = _dt.datetime.fromisoformat(str(now_local))
                for i, raw in enumerate(times):
                    if _dt.datetime.fromisoformat(str(raw)) >= target:
                        start = i
                        break
            except Exception:
                start = 0
        return list(range(start, min(start + 6, len(times))))

    def local_on_8s_phase_text(self, phase: str, primary: dict[str, Any], settings: dict[str, Any]) -> str:
        """Build narration from only the values rendered by one Local on the 8s phase.

        This function intentionally mirrors the corresponding renderer cards. It
        does not add forecast discussion, hidden weather values, ticker text, map
        interpretation, or any other commentary that is not visible in that phase.
        """
        import datetime as _dt
        phase = str(phase or "").strip().lower()
        location = self._local8_location_label(primary)
        station = _clean_text(settings.get("station_name") or "Roller Weather Network", 80)
        current = primary.get("current") or {}
        daily = primary.get("daily") or {}

        if phase == "intro":
            return _clean_text(f"Local on the 8s. {location}. {station}. Your local forecast starts now.", 260)

        if phase == "current":
            parts = [f"Current conditions for {location}."]
            temp = _round_int(current.get("temperature_2m"))
            desc = _clean_text(current.get("description") or "Weather unavailable", 100)
            if temp is not None:
                parts.append(f"{temp} degrees. {desc}.")
            else:
                parts.append(f"{desc}.")
            feels = _round_int(current.get("apparent_temperature"))
            humidity = _round_int(current.get("relative_humidity_2m"))
            wind = _round_int(current.get("wind_speed_10m"))
            gusts = _round_int(current.get("wind_gusts_10m"))
            direction = _clean_text(current.get("wind_cardinal") or "", 12)
            pressure = _num(current.get("surface_pressure"))
            if feels is not None: parts.append(f"Feels like {feels} degrees.")
            if humidity is not None: parts.append(f"Humidity {humidity} percent.")
            if wind is not None: parts.append(f"Wind {direction + ' ' if direction else ''}{wind} miles per hour.")
            if gusts is not None: parts.append(f"Gusts {gusts} miles per hour.")
            if pressure is not None: parts.append(f"Pressure {pressure / 33.8638866667:.2f} inches of mercury.")
            return _clean_text(" ".join(parts), 700)

        if phase == "today":
            try:
                from app.weather import describe_weather
            except Exception:
                describe_weather = lambda code: "Weather unavailable"
            codes = daily.get("weather_code") or []
            highs = daily.get("temperature_2m_max") or []
            lows = daily.get("temperature_2m_min") or []
            pops = daily.get("precipitation_probability_max") or []
            code = codes[0] if codes else None
            parts = [f"Your forecast for {location}. Today. {describe_weather(code)}."]
            high = _round_int(highs[0]) if highs else None
            low = _round_int(lows[0]) if lows else None
            pop = _round_int(pops[0]) if pops else None
            if high is not None: parts.append(f"High {high} degrees.")
            if low is not None: parts.append(f"Low {low} degrees.")
            if pop is not None: parts.append(f"Chance of precipitation {pop} percent.")
            return _clean_text(" ".join(parts), 520)

        if phase == "hourly":
            hourly = primary.get("hourly") or {}
            indices = self._local8_hour_indices(primary)
            if not indices:
                return f"Hour by hour for {location}. Hourly forecast unavailable."
            parts = [f"Hour by hour for {location}."]
            times = hourly.get("time") or []
            temps = hourly.get("temperature_2m") or []
            pops = hourly.get("precipitation_probability") or []
            winds = hourly.get("wind_speed_10m") or []
            for i in indices:
                try:
                    label = _dt.datetime.fromisoformat(str(times[i])).strftime("%I %p").lstrip("0")
                except Exception:
                    label = _clean_text(times[i] if i < len(times) else "", 20)
                bits = [label]
                temp = _round_int(temps[i]) if i < len(temps) else None
                pop = _round_int(pops[i]) if i < len(pops) else None
                wind = _round_int(winds[i]) if i < len(winds) else None
                if temp is not None: bits.append(f"{temp} degrees")
                if pop is not None: bits.append(f"rain {pop} percent")
                if wind is not None: bits.append(f"wind {wind}")
                parts.append(", ".join(bits) + ".")
            return _clean_text(" ".join(parts), 900)

        if phase == "radar_local":
            return _clean_text(f"Local radar. {location}.", 180)

        if phase == "seven_day":
            dates = daily.get("time") or []
            highs = daily.get("temperature_2m_max") or []
            lows = daily.get("temperature_2m_min") or []
            pops = daily.get("precipitation_probability_max") or []
            count = min(7, len(dates))
            if not count:
                return f"Seven day forecast for {location}. Forecast unavailable."
            parts = [f"Seven day forecast for {location}."]
            for i in range(count):
                try:
                    day = _dt.date.fromisoformat(str(dates[i])).strftime("%A")
                except Exception:
                    day = "Day"
                high = _round_int(highs[i]) if i < len(highs) else None
                low = _round_int(lows[i]) if i < len(lows) else None
                pop = _round_int(pops[i]) if i < len(pops) else None
                bits = [day]
                if high is not None: bits.append(f"high {high}")
                if low is not None: bits.append(f"low {low}")
                if pop is not None: bits.append(f"rain {pop} percent")
                parts.append(", ".join(bits) + ".")
            return _clean_text(" ".join(parts), 1000)

        # Unsupported/custom phases are intentionally silent rather than allowing
        # narration to describe weather that the viewer cannot see.
        return ""

    def local_on_8s_text(self, primary: dict[str, Any], settings: dict[str, Any]) -> str:
        """Compatibility helper retained for API/tests from v0.2.2."""
        return self.local_on_8s_phase_text("current", primary, settings)

    def severe_alert_text(self, alert: dict[str, Any], settings: dict[str, Any]) -> str:
        station = _clean_text(settings.get("station_name") or "Roller Weather Network", 80)
        event = _clean_text(alert.get("event") or "Severe Weather Alert", 120)
        area = _clean_text(alert.get("areaDesc") or "the warned area", 220)
        headline = _clean_text(alert.get("headline") or "", 260)
        instruction = _clean_text(alert.get("instruction") or "", 300)
        parts = [f"{station} severe weather alert. {event} for {area}."]
        if headline and event.lower() not in headline.lower():
            parts.append(headline + ("" if headline.endswith(".") else "."))
        if instruction:
            first_sentence = re.split(r"(?<=[.!?])\s+", instruction, maxsplit=1)[0]
            parts.append(first_sentence)
        return _clean_text(" ".join(parts), 780)
