from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

from app.config import CONFIG_DIR, SETTINGS_PATH
from app.history import DB_PATH
from app.streamer import LIVE_DIR, encoder_capabilities

MAX_BACKUP_MEMBERS = 8
MAX_BACKUP_SETTINGS_BYTES = 2 * 1024 * 1024
MAX_BACKUP_DATABASE_BYTES = 128 * 1024 * 1024
MAX_BACKUP_BRANDING_BYTES = 8 * 1024 * 1024
MAX_BACKUP_TOTAL_BYTES = 140 * 1024 * 1024
MAX_BACKUP_COMPRESSION_RATIO = 200
ALLOWED_BACKUP_NAMES = {"manifest.json", "settings.json", "weatherstream.db", "branding/logo.png"}


def _validate_backup_archive(zf: zipfile.ZipFile) -> None:
    members = zf.infolist()
    if len(members) > MAX_BACKUP_MEMBERS:
        raise ValueError(f"Backup contains too many files ({len(members)}; maximum {MAX_BACKUP_MEMBERS})")
    total = 0
    for member in members:
        name = member.filename.replace("\\", "/").strip("/")
        if member.is_dir():
            continue
        if name not in ALLOWED_BACKUP_NAMES:
            raise ValueError(f"Backup contains unsupported file: {name}")
        limit = MAX_BACKUP_SETTINGS_BYTES if name in {"manifest.json", "settings.json"} else MAX_BACKUP_DATABASE_BYTES if name == "weatherstream.db" else MAX_BACKUP_BRANDING_BYTES
        if member.file_size < 0 or member.file_size > limit:
            raise ValueError(f"Backup file {name} is larger than the allowed limit")
        if member.compress_size == 0 and member.file_size > 0:
            raise ValueError(f"Backup file {name} has an invalid compressed size")
        if member.compress_size and member.file_size / member.compress_size > MAX_BACKUP_COMPRESSION_RATIO:
            raise ValueError(f"Backup file {name} has an unsafe compression ratio")
        total += member.file_size
    if total > MAX_BACKUP_TOTAL_BYTES:
        raise ValueError("Backup expands beyond the allowed total size")

BRANDING_DIR = CONFIG_DIR / "branding"

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "balanced": {
        "label": "Balanced",
        "description": "Recommended 24/7 profile: smooth 720p output with adaptive protection.",
        "settings": {
            "performance": {"mode": "adaptive"},
            "video": {"render_fps": 5, "content_fps": 3, "output_fps": 15, "encoder_preset": "superfast", "bitrate": "2000k", "hls_segment_seconds": 3, "hls_list_size": 10},
            "presentation": {"transition": "crossfade", "retro_effects": {"enabled": False}},
        },
    },
    "low_cpu": {
        "label": "Low CPU",
        "description": "Prioritizes realtime playback for larger multi-channel lineups.",
        "settings": {
            "performance": {"mode": "low_cpu"},
            "video": {"render_fps": 4, "content_fps": 2, "output_fps": 12, "encoder_preset": "ultrafast", "bitrate": "1700k", "hls_segment_seconds": 4, "hls_list_size": 10},
            "presentation": {"transition": "cut", "background_motion": False, "retro_effects": {"enabled": False}},
        },
    },
    "maximum_quality": {
        "label": "Maximum Quality",
        "description": "Higher motion quality for small lineups with ample CPU/GPU headroom.",
        "settings": {
            "performance": {"mode": "maximum_quality"},
            "video": {"render_fps": 6, "content_fps": 4, "output_fps": 20, "encoder_preset": "superfast", "bitrate": "2800k", "hls_segment_seconds": 3, "hls_list_size": 10},
            "presentation": {"transition": "crossfade"},
        },
    },
    "full_retro": {
        "label": "Full Retro",
        "description": "Weather Terminal styling with CRT effects. Best for one or two channels or hardware encoding.",
        "settings": {
            "performance": {"mode": "adaptive"},
            "theme": "terminal-80s",
            "presentation": {"transition": "crt_fade", "retro_effects": {"enabled": True, "scanlines": 0.12, "noise": 0.012, "bloom": 0.08, "soft_edges": 0.08, "color_bleed_px": 1, "horizontal_jitter_px": 0}},
        },
    },
}


def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(base))
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def system_status() -> dict[str, Any]:
    cpu_count = os.cpu_count() or 1
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1 = load5 = load15 = 0.0
    mem_total = mem_available = None
    try:
        vals = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                vals[k] = int(v.strip().split()[0]) * 1024
        mem_total = vals.get("MemTotal"); mem_available = vals.get("MemAvailable")
    except Exception:
        pass
    cgroup_limit = None
    for p in (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            raw = p.read_text().strip()
            if raw and raw != "max": cgroup_limit = int(raw); break
        except Exception: pass
    live_size = 0
    if LIVE_DIR.exists():
        for f in LIVE_DIR.rglob("*"):
            try:
                if f.is_file(): live_size += f.stat().st_size
            except Exception: pass
    config_size = 0
    if CONFIG_DIR.exists():
        for f in CONFIG_DIR.rglob("*"):
            try:
                if f.is_file(): config_size += f.stat().st_size
            except Exception: pass
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": cpu_count,
        "load": {"1m": round(load1,2), "5m": round(load5,2), "15m": round(load15,2), "load1_percent_of_cpu": round(load1/cpu_count*100,1)},
        "memory": {"total_bytes": mem_total, "available_bytes": mem_available, "used_percent": round((1-(mem_available/mem_total))*100,1) if mem_total and mem_available else None, "cgroup_limit_bytes": cgroup_limit},
        "storage": {"config_bytes": config_size, "live_hls_bytes": live_size},
        "encoders": encoder_capabilities(),
    }


def create_backup_bytes(settings: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"product":"WeatherStream","version":"0.3.0","created_at":time.time()}, indent=2))
        zf.writestr("settings.json", json.dumps(settings, indent=2))
        if DB_PATH.exists(): zf.write(DB_PATH, "weatherstream.db")
        if BRANDING_DIR.exists():
            for f in BRANDING_DIR.rglob("*"):
                if f.is_file(): zf.write(f, f"branding/{f.relative_to(BRANDING_DIR)}")
    return buf.getvalue()


def inspect_backup(data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        _validate_backup_archive(zf)
        names = set(zf.namelist())
        if "settings.json" not in names: raise ValueError("Backup is missing settings.json")
        settings = json.loads(zf.read("settings.json"))
        if not isinstance(settings, dict): raise ValueError("Backup settings are invalid")
        return {"settings": settings, "database": "weatherstream.db" in names, "branding": [x for x in names if x.startswith("branding/") and not x.endswith("/")]}


def restore_backup_bytes(data: bytes, config_store, history_store) -> dict[str, Any]:
    info = inspect_backup(data)
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        _validate_backup_archive(zf)
        settings = info["settings"]
        # Write through ConfigStore so schema validation/migration rules are applied.
        config_store.replace(settings)
        if "weatherstream.db" in zf.namelist():
            history_store.replace_database(zf.read("weatherstream.db"))
        branding_files = [x for x in zf.namelist() if x.startswith("branding/") and not x.endswith("/")]
        if branding_files:
            BRANDING_DIR.mkdir(parents=True, exist_ok=True)
            for old in BRANDING_DIR.iterdir():
                if old.is_file(): old.unlink(missing_ok=True)
            for name in branding_files:
                rel = Path(name).relative_to("branding")
                if len(rel.parts) != 1: continue
                (BRANDING_DIR / rel.name).write_bytes(zf.read(name))
    return {"ok": True, "database_restored": info["database"], "branding_files": len(info["branding"])}


def create_diagnostics_bytes(settings: dict[str, Any], app_status: dict[str, Any], channels: dict[str, Any], streamer) -> bytes:
    sanitized = json.loads(json.dumps(settings))
    # Avoid exporting internal or credential-bearing URLs by default.
    if sanitized.get("public_base_url"):
        sanitized["public_base_url"] = "<configured>"
    if (sanitized.get("notifications") or {}).get("webhook_url"):
        sanitized["notifications"]["webhook_url"] = "<redacted>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", "WeatherStream v0.3.0 diagnostic bundle. No music files are included.\n")
        zf.writestr("status.json", json.dumps(app_status, indent=2, default=str))
        zf.writestr("channels.json", json.dumps(channels, indent=2, default=str))
        zf.writestr("settings-sanitized.json", json.dumps(sanitized, indent=2))
        zf.writestr("system.json", json.dumps(system_status(), indent=2))
        zf.writestr("ffmpeg-errors.txt", streamer.diagnostics_text())
    return buf.getvalue()
