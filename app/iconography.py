from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

ICON_ROOT = Path(__file__).resolve().parent / "static" / "icons" / "rwn"

WMO_MAP: dict[int, str] = {
    0: "clear_day", 1: "mostly_clear_day", 2: "partly_cloudy_day", 3: "cloudy",
    45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle",
    56: "freezing_rain", 57: "freezing_rain", 61: "light_rain", 63: "rain", 65: "heavy_rain",
    66: "freezing_rain", 67: "freezing_rain", 71: "snow", 73: "snow", 75: "heavy_snow",
    77: "snow", 80: "showers", 81: "showers", 82: "heavy_rain", 85: "snow", 86: "heavy_snow",
    95: "thunderstorm", 96: "severe_thunderstorm", 99: "severe_thunderstorm",
}
NIGHT_REMAP = {
    "clear_day": "clear_night",
    "mostly_clear_day": "mostly_clear_night",
    "partly_cloudy_day": "partly_cloudy_night",
}
ANIMATED = {
    "partly_cloudy_day", "partly_cloudy_night", "light_rain", "rain", "heavy_rain", "showers",
    "thunderstorm", "severe_thunderstorm", "snow", "heavy_snow", "windy", "tropical", "lightning",
}


def condition_name(code: Any, *, is_night: bool = False) -> str:
    try:
        name = WMO_MAP.get(int(code), "cloudy")
    except Exception:
        name = "cloudy"
    if is_night:
        return NIGHT_REMAP.get(name, name)
    return name


def variant_for_pixels(px: int) -> str:
    if px >= 150:
        return "hero"
    if px >= 92:
        return "standard"
    return "compact"


@lru_cache(maxsize=512)
def _load(path: str) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None




@lru_cache(maxsize=768)
def _resized(path: str, pixels: int) -> Image.Image | None:
    source = _load(path)
    if source is None:
        return None
    if source.width == pixels and source.height == pixels:
        return source
    return source.resize((pixels, pixels), Image.Resampling.LANCZOS)

def _frame(name: str, variant: str, *, animate: bool, speed: float, now: float | None) -> Path:
    if animate and variant in {"hero", "standard"} and name in ANIMATED:
        stamp = time.time() if now is None else float(now)
        idx = int(stamp * max(0.15, float(speed))) % 4
        p = ICON_ROOT / "animated" / variant / f"{name}-{idx}.png"
        if p.exists():
            return p
    return ICON_ROOT / "conditions" / variant / f"{name}.png"


def condition_icon(
    code: Any,
    *,
    pixels: int,
    is_night: bool = False,
    animate: bool = True,
    speed: float = 1.7,
    now: float | None = None,
) -> Image.Image | None:
    name = condition_name(code, is_night=is_night)
    variant = variant_for_pixels(pixels)
    source = _resized(str(_frame(name, variant, animate=animate, speed=speed, now=now)), pixels)
    return source.copy() if source is not None else None


def metric_icon(name: str, pixels: int = 34) -> Image.Image | None:
    source = _resized(str(ICON_ROOT / "metrics" / f"{name}.png"), pixels)
    return source.copy() if source is not None else None


def alert_kind(event: str) -> str:
    e = (event or "").lower()
    if "tornado" in e: return "tornado"
    if "flash flood" in e or "flood" in e: return "flash_flood"
    if "winter" in e or "snow" in e or "ice" in e or "blizzard" in e: return "winter_storm"
    if "heat" in e: return "extreme_heat"
    if "fire" in e or "red flag" in e or "wildfire" in e: return "wildfire"
    if "hurricane" in e or "tropical" in e or "storm surge" in e: return "tropical"
    return "severe_thunderstorm"


def alert_icon(event: str, pixels: int = 112) -> Image.Image | None:
    source = _resized(str(ICON_ROOT / "alerts" / f"{alert_kind(event)}.png"), pixels)
    return source.copy() if source is not None else None


def paste(target: Image.Image | None, icon: Image.Image | None, x: int, y: int) -> bool:
    if target is None or icon is None:
        return False
    target.paste(icon, (int(x), int(y)), icon)
    return True
