from __future__ import annotations

from typing import Any

# v0.3.10 transition identities.  These names intentionally describe broadcast
# language rather than implementation details so the renderer can evolve later.
DESK_TRANSITIONS = {
    "severe": "angular_wipe",
    "flood": "waterline_wipe",
    "winter": "ice_shards",
    "heat": "heatwave",
    "wildfire": "smoke_dissolve",
    "tropical": "radar_sweep",
}

NETWORK_TRANSITIONS = {
    "clean": "rwn_wipe",
    "soft": "crossfade",
    "dynamic": "panel_push",
}

VALID_MOTION_TRANSITIONS = {
    "cut", "crossfade", "wipe", "wipe_vertical", "slide_left", "slide_up",
    "venetian", "dissolve", "pixel_dissolve", "crt_fade",
    "rwn_wipe", "panel_push", "angular_wipe", "waterline_wipe",
    "ice_shards", "heatwave", "smoke_dissolve", "radar_sweep",
}


def motion_config(settings: dict[str, Any]) -> dict[str, Any]:
    return ((settings.get("presentation") or {}).get("broadcast_motion") or {})


def resolved_transition(settings: dict[str, Any], event_key: str | None = None) -> str:
    """Resolve the transition for this channel/desk.

    ``presentation.transition`` remains the compatibility/fixed-mode control.
    When Broadcast Motion is enabled with ``auto_transitions``, event desks get
    their own transition and normal RWN uses the selected network style.
    """
    pres = settings.get("presentation") or {}
    cfg = motion_config(settings)
    fallback = str(pres.get("transition") or "crossfade")
    if not cfg.get("enabled", True) or not cfg.get("auto_transitions", True):
        return fallback if fallback in VALID_MOTION_TRANSITIONS else "crossfade"
    desk = str(event_key or "").lower()
    if desk and cfg.get("desk_transitions", True):
        return DESK_TRANSITIONS.get(desk, fallback)
    network_style = str(cfg.get("network_style") or "clean")
    return NETWORK_TRANSITIONS.get(network_style, "rwn_wipe")


def transition_seconds(settings: dict[str, Any], *, urgent: bool = False) -> float:
    pres = settings.get("presentation") or {}
    cfg = motion_config(settings)
    if urgent and cfg.get("emergency_hard_cut", True):
        return 0.0
    try:
        value = float(cfg.get("transition_seconds", pres.get("transition_seconds", 0.65)))
    except Exception:
        value = 0.65
    return max(0.0, min(1.5, value))


def entry_style(settings: dict[str, Any], event_key: str | None = None) -> str:
    cfg = motion_config(settings)
    if not cfg.get("enabled", True) or not cfg.get("entry_animation", True):
        return "none"
    if event_key in {"severe", "heat"}:
        return "snap"
    if event_key in {"flood", "tropical"}:
        return "glide"
    if event_key in {"winter", "wildfire"}:
        return "soft"
    return str(cfg.get("entry_style") or "broadcast")


def entry_seconds(settings: dict[str, Any]) -> float:
    cfg = motion_config(settings)
    try:
        return max(0.0, min(1.25, float(cfg.get("entry_seconds", 0.45))))
    except Exception:
        return 0.45
