from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

_CAPS_LOCK = threading.Lock()
_CAPS_CACHE: dict[str, Any] | None = None
_RENDER_NODE_RE = re.compile(r"^/dev/dri/renderD\d+$")


def _clean_error(text: str, limit: int = 700) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return " | ".join(lines[-6:])[-limit:]


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip()
        return value or None
    except Exception:
        return None


def _render_node_metadata(path: Path) -> dict[str, Any]:
    name = path.name
    sys_device = Path("/sys/class/drm") / name / "device"
    vendor = _read_text(sys_device / "vendor")
    device_id = _read_text(sys_device / "device")
    uevent: dict[str, str] = {}
    raw_uevent = _read_text(sys_device / "uevent") or ""
    for line in raw_uevent.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            uevent[key] = value
    driver = uevent.get("DRIVER")
    if not driver:
        try:
            driver = (sys_device / "driver").resolve().name
        except Exception:
            driver = None
    pci_slot = uevent.get("PCI_SLOT_NAME")
    vendor_label = "Intel" if str(vendor or "").lower() == "0x8086" else "GPU"
    bits = [vendor_label]
    if device_id:
        bits.append(f"device {device_id}")
    if driver:
        bits.append(driver)
    if pci_slot:
        bits.append(pci_slot)
    label = " • ".join(bits) + f" • {path}"
    return {
        "path": str(path),
        "label": label,
        "vendor_id": vendor,
        "device_id": device_id,
        "driver": driver,
        "pci_slot": pci_slot,
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
    }


def enumerate_render_nodes() -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for path in sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").exists() else []:
        if _RENDER_NODE_RE.fullmatch(str(path)):
            nodes.append(_render_node_metadata(path))
    return nodes


def _ffmpeg_encoder_text() -> str:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return (result.stdout or "") + "\n" + (result.stderr or "")
    except Exception:
        return ""


def _run_probe(command: list[str], timeout: int = 12) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        detail = _clean_error((result.stderr or "") + "\n" + (result.stdout or ""))
        if result.returncode == 0:
            return True, detail
        return False, detail or f"FFmpeg probe exited with code {result.returncode}."
    except subprocess.TimeoutExpired:
        return False, f"Hardware probe timed out after {timeout} seconds."
    except Exception as exc:
        return False, str(exc)[:700]


def _vainfo_probe(device: str) -> tuple[bool, str]:
    exe = shutil.which("vainfo")
    if not exe:
        return False, "vainfo is not installed in the container."
    try:
        result = subprocess.run(
            [exe, "--display", "drm", "--device", device],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        detail = _clean_error((result.stdout or "") + "\n" + (result.stderr or ""), limit=900)
        return result.returncode == 0, detail
    except subprocess.TimeoutExpired:
        return False, "vainfo timed out."
    except Exception as exc:
        return False, str(exc)[:700]


def _vaapi_encode_probe(device: str) -> tuple[bool, str]:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-init_hw_device", f"vaapi=hw:{device}",
        "-filter_hw_device", "hw",
        "-f", "lavfi", "-i", "color=c=black:s=128x72:r=1",
        "-frames:v", "1",
        "-vf", "format=nv12,hwupload",
        "-c:v", "h264_vaapi",
        "-f", "null", "-",
    ]
    return _run_probe(command)


def _qsv_encode_probe(device: str) -> tuple[bool, str]:
    # On Linux FFmpeg expects the DRM render node as the QSV child_device.
    # Explicit child_device_type=vaapi avoids relying on platform defaults.
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-init_hw_device", f"qsv=hw,child_device={device},child_device_type=vaapi",
        "-filter_hw_device", "hw",
        "-f", "lavfi", "-i", "color=c=black:s=128x72:r=1",
        "-frames:v", "1",
        "-vf", "format=nv12,hwupload=extra_hw_frames=64",
        "-c:v", "h264_qsv",
        "-f", "null", "-",
    ]
    return _run_probe(command)


def _nvenc_encode_probe() -> tuple[bool, str]:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=128x72:r=1",
        "-frames:v", "1", "-pix_fmt", "yuv420p",
        "-c:v", "h264_nvenc", "-f", "null", "-",
    ]
    return _run_probe(command)


def encoder_capabilities(force: bool = False) -> dict[str, Any]:
    """Return encoder capabilities proven by a real one-frame encode.

    Merely seeing h264_qsv/h264_vaapi in `ffmpeg -encoders` is not enough: the
    userspace driver, container device mapping, permissions, and selected render
    node must all work. Each /dev/dri/renderD* node is therefore tested.
    """
    global _CAPS_CACHE
    with _CAPS_LOCK:
        if _CAPS_CACHE is not None and not force:
            return _clone_caps(_CAPS_CACHE)

        text = _ffmpeg_encoder_text()
        qsv_compiled = "h264_qsv" in text
        vaapi_compiled = "h264_vaapi" in text
        nvenc_compiled = "h264_nvenc" in text
        nodes = enumerate_render_nodes()
        qsv_devices: list[dict[str, Any]] = []
        vaapi_devices: list[dict[str, Any]] = []

        # Probe nodes in parallel so one misbehaving driver does not make the
        # Dashboard wait for every QSV/VAAPI/vainfo timeout serially.
        workers = max(1, min(8, len(nodes) * 3))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gpu-probe") as pool:
            vainfo_jobs = {node["path"]: pool.submit(_vainfo_probe, node["path"]) for node in nodes}
            qsv_jobs = {node["path"]: pool.submit(_qsv_encode_probe, node["path"]) for node in nodes} if qsv_compiled else {}
            vaapi_jobs = {node["path"]: pool.submit(_vaapi_encode_probe, node["path"]) for node in nodes} if vaapi_compiled else {}
            for node in nodes:
                device = node["path"]
                vainfo_ok, vainfo_detail = vainfo_jobs[device].result()
                qsv_ok, qsv_error = (qsv_jobs[device].result() if qsv_compiled else (False, "FFmpeg was not built with h264_qsv."))
                vaapi_ok, vaapi_error = (vaapi_jobs[device].result() if vaapi_compiled else (False, "FFmpeg was not built with h264_vaapi."))
                qsv_devices.append({
                    **node,
                    "available": qsv_ok,
                    "status": "READY" if qsv_ok else ("DRIVER_ERROR" if qsv_compiled else "NOT_COMPILED"),
                    "error": "" if qsv_ok else qsv_error,
                    "vainfo_ok": vainfo_ok,
                    "vainfo": vainfo_detail,
                })
                vaapi_devices.append({
                    **node,
                    "available": vaapi_ok,
                    "status": "READY" if vaapi_ok else ("DRIVER_ERROR" if vaapi_compiled else "NOT_COMPILED"),
                    "error": "" if vaapi_ok else vaapi_error,
                    "vainfo_ok": vainfo_ok,
                    "vainfo": vainfo_detail,
                })

        nvenc_present = Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists()
        nvenc_ok = False
        nvenc_error = "NVIDIA device is not mapped into the container."
        if nvenc_compiled and nvenc_present:
            nvenc_ok, nvenc_error = _nvenc_encode_probe()
        elif not nvenc_compiled:
            nvenc_error = "FFmpeg was not built with h264_nvenc."

        qsv_ready = [row for row in qsv_devices if row.get("available")]
        vaapi_ready = [row for row in vaapi_devices if row.get("available")]
        _CAPS_CACHE = {
            "software": {"available": True, "compiled": True, "encoder": "libx264", "status": "READY", "devices": []},
            "qsv": {
                "available": bool(qsv_ready), "compiled": qsv_compiled, "encoder": "h264_qsv",
                "status": "NOT_COMPILED" if not qsv_compiled else ("READY" if qsv_ready else ("NO_DEVICE" if not nodes else "DRIVER_ERROR")),
                "default_device": qsv_ready[0]["path"] if qsv_ready else None,
                "devices": qsv_devices,
            },
            "vaapi": {
                "available": bool(vaapi_ready), "compiled": vaapi_compiled, "encoder": "h264_vaapi",
                "status": "NOT_COMPILED" if not vaapi_compiled else ("READY" if vaapi_ready else ("NO_DEVICE" if not nodes else "DRIVER_ERROR")),
                "default_device": vaapi_ready[0]["path"] if vaapi_ready else None,
                "devices": vaapi_devices,
            },
            "nvenc": {
                "available": nvenc_ok, "compiled": nvenc_compiled, "encoder": "h264_nvenc",
                "status": "READY" if nvenc_ok else ("NO_DEVICE" if not nvenc_present else "DRIVER_ERROR"),
                "default_device": "/dev/nvidia0" if nvenc_present else None,
                "devices": [], "error": "" if nvenc_ok else nvenc_error,
            },
        }
        return _clone_caps(_CAPS_CACHE)


def _clone_caps(value: dict[str, Any]) -> dict[str, Any]:
    # Avoid importing copy in the hot path just for a shallow, known structure.
    result: dict[str, Any] = {}
    for key, item in value.items():
        row = dict(item)
        row["devices"] = [dict(device) for device in item.get("devices", [])]
        result[key] = row
    return result


def normalize_device_setting(value: Any) -> str:
    text = str(value or "auto").strip()
    if text == "auto":
        return "auto"
    if _RENDER_NODE_RE.fullmatch(text):
        return text
    return "auto"


def choose_encoder(
    requested: str,
    requested_device: str = "auto",
    failed_signature: tuple[str, str] | None = None,
) -> tuple[str, str | None, str | None]:
    """Return (encoder_name, render_node, fallback_reason)."""
    requested = requested if requested in {"auto", "software", "nvenc", "qsv", "vaapi"} else "software"
    requested_device = normalize_device_setting(requested_device)
    signature = (requested, requested_device)
    if failed_signature == signature:
        return "software", None, "previous hardware initialization failed for this encoder/device selection"
    if requested == "software":
        return "software", None, None

    caps = encoder_capabilities()

    def ready_device(kind: str) -> str | None:
        rows = (caps.get(kind) or {}).get("devices") or []
        if requested_device != "auto":
            for row in rows:
                if row.get("path") == requested_device and row.get("available"):
                    return requested_device
            return None
        for row in rows:
            if row.get("available"):
                return str(row.get("path"))
        return None

    if requested == "auto":
        for kind in ("qsv", "vaapi"):
            device = ready_device(kind)
            if device:
                return kind, device, None
        if requested_device == "auto" and (caps.get("nvenc") or {}).get("available"):
            return "nvenc", None, None
        return "software", None, "no probed hardware encoder is ready"

    if requested in {"qsv", "vaapi"}:
        device = ready_device(requested)
        if device:
            return requested, device, None
        return "software", None, f"{requested.upper()} is not ready on the selected render node"

    if requested == "nvenc" and (caps.get("nvenc") or {}).get("available"):
        return "nvenc", None, None
    return "software", None, "NVENC is not ready"


def device_info(encoder: str, device: str | None) -> dict[str, Any] | None:
    if not device or encoder not in {"qsv", "vaapi"}:
        return None
    for row in (encoder_capabilities().get(encoder) or {}).get("devices") or []:
        if row.get("path") == device:
            return {k: row.get(k) for k in ("path", "label", "vendor_id", "device_id", "driver", "pci_slot", "available", "status")}
    return {"path": device, "label": device}
