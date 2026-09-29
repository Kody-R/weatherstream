from __future__ import annotations

import datetime as dt
import copy
import math
import threading
from functools import lru_cache
import textwrap
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageEnhance, ImageFilter

from app.config import CONFIG_DIR
from app.events import EVENT_TYPES
from app.network import apply_region_identity, region_for_location
from app.radar import latlon_to_world
from app.studio import active_sequence, bumper
from app.story import classify_story, compose_story_sequence, STORY_PRIORITY
from app.iconography import condition_icon as rwn_condition_icon, metric_icon as rwn_metric_icon, alert_icon as rwn_alert_icon, paste as paste_rwn_icon
from app.event_identity import apply_identity_colors, identity as event_identity
from app.broadcast_motion import resolved_transition, transition_seconds as motion_transition_seconds, entry_style as motion_entry_style, entry_seconds as motion_entry_seconds

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
BRANDING_LOGO = CONFIG_DIR / "branding" / "logo.png"
BUILTIN_RWN_LOGO = Path(__file__).resolve().parent / "static" / "rwn-logo.png"

THEMES = {
    "classic-blue": {
        "style": "classic", "bg": "#082a60", "panel": "#174b8f", "panel2": "#0c376f", "title": "#f6f7fb",
        "text": "#ffffff", "accent": "#ffd447", "muted": "#b7d4ef", "ticker": "#061b3b", "alert": "#a41f25",
    },
    "local-90s": {
        "style": "local90", "bg": "#0b2f63", "panel": "#154d8e", "panel2": "#103b73", "title": "#ffd94a",
        "text": "#ffffff", "accent": "#ffd94a", "muted": "#c4ddf5", "ticker": "#071e42", "alert": "#9f2028",
    },
    "retro-2000": {
        "style": "retro00", "bg": "#102b45", "panel": "#244b68", "panel2": "#183b57", "title": "#ffffff",
        "text": "#f7fbff", "accent": "#7bd6ff", "muted": "#c7d8e7", "ticker": "#0b2237", "alert": "#a6262e",
    },
    "terminal-80s": {
        "style": "terminal80", "bg": "#061f45", "panel": "#0d3d72", "panel2": "#03152f", "title": "#55e7ff",
        "text": "#f4fbff", "accent": "#ffe35a", "muted": "#8ed7e8", "ticker": "#020f24", "alert": "#a51f2a",
    },
    "cable-gold": {
        "style": "cablegold", "bg": "#12304b", "panel": "#1d5570", "panel2": "#0a263c", "title": "#fff6d3",
        "text": "#ffffff", "accent": "#f4c84b", "muted": "#b9d6dc", "ticker": "#061b2c", "alert": "#a3252b",
    },
}


@lru_cache(maxsize=96)
def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO_BOLD if mono and bold else FONT_MONO if mono else FONT_BOLD if bold else FONT_REG
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


@lru_cache(maxsize=192)
def _cached_pattern_mask(width: int, height: int, alpha_bucket: int, block: int) -> Image.Image:
    sw, sh = max(1, (width + block - 1) // block), max(1, (height + block - 1) // block)
    mask = Image.new("L", (sw, sh), 0)
    pix = mask.load()
    threshold = max(0, min(64, alpha_bucket)) * 10000 // 64
    for y in range(sh):
        for x in range(sw):
            value = ((x * 92821) ^ (y * 68917) ^ ((x + y) * 31337)) % 10000
            pix[x, y] = 255 if value < threshold else 0
    return mask.resize((width, height), Image.Resampling.NEAREST)


def safe(value: Any, default: str = "--") -> str:
    return default if value is None else str(value)


def n(value: Any, digits: int = 0, suffix: str = "") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except Exception:
        return "--"


def pressure_inhg(value: Any) -> str:
    try:
        return f"{float(value) / 33.8638866667:.2f} inHg"
    except Exception:
        return "--"

def dew_point_f(temp_f: Any, humidity: Any) -> float | None:
    try:
        t_c = (float(temp_f) - 32.0) * 5.0 / 9.0; rh = max(1.0, min(100.0, float(humidity)))
        a, b = 17.625, 243.04
        gamma = math.log(rh / 100.0) + (a * t_c) / (b + t_c)
        d_c = (b * gamma) / (a - gamma)
        return d_c * 9.0 / 5.0 + 32.0
    except Exception:
        return None

def heat_index_f(temp_f: Any, humidity: Any) -> float | None:
    try:
        t = float(temp_f); r = float(humidity)
        if t < 80 or r < 35: return t
        return (-42.379 + 2.04901523*t + 10.14333127*r - 0.22475541*t*r - 0.00683783*t*t - 0.05481717*r*r + 0.00122874*t*t*r + 0.00085282*t*r*r - 0.00000199*t*t*r*r)
    except Exception:
        return None

def wind_chill_f(temp_f: Any, wind_mph: Any) -> float | None:
    try:
        t = float(temp_f); v = float(wind_mph)
        if t > 50 or v <= 3: return t
        return 35.74 + 0.6215*t - 35.75*(v**0.16) + 0.4275*t*(v**0.16)
    except Exception:
        return None


def location_label(location: dict[str, Any]) -> str:
    if location.get("admin1"):
        return f"{location.get('name', '')}, {location.get('admin1', '')}"
    return location.get("name", location.get("postal_code", "Local Weather"))


def round_rect(draw: ImageDraw.ImageDraw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def draw_weather_icon(draw: ImageDraw.ImageDraw, code: int | None, x: int, y: int, scale: float, c: dict[str, str], *, is_night: bool = False, icon_cfg: dict[str, Any] | None = None, now: float | None = None) -> None:
    icon_cfg = icon_cfg or {}
    if icon_cfg.get("enabled", True):
        px = max(48, int(round(180 * scale)))
        icon = rwn_condition_icon(
            code, pixels=px, is_night=is_night,
            animate=bool(icon_cfg.get("animation_enabled", True)),
            speed=float(icon_cfg.get("animation_speed", 1.7) or 1.7), now=now,
        )
        target = getattr(draw, "_image", None)
        if target is not None and paste_rwn_icon(target, icon, x, y):
            return
    code = int(code) if code is not None else 0
    sun = code in {0, 1}
    cloud = code in {1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99}
    rain = code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
    snow = code in {71, 73, 75, 77, 85, 86}
    storm = code in {95, 96, 99}

    # Clear/mainly-clear/partly-cloudy conditions receive a celestial marker.
    # Hourly cards pass is_night=True using that forecast date's sunrise/sunset,
    # so future overnight hours never inherit the renderer's current clock state.
    if sun or code in {1, 2}:
        r = int(45 * scale)
        cx, cy = x + int(55 * scale), y + int(48 * scale)
        if is_night:
            moon_fill = "#f3f6d0"
            # A crescent built from two circles keeps the icon readable at IPTV scale.
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=moon_fill)
            cut = max(5, int(18 * scale))
            draw.ellipse((cx-r+cut, cy-r-int(5*scale), cx+r+cut, cy+r-int(5*scale)), fill=c["panel"])
            # Tiny stars help make the day/night state obvious without clutter.
            star_fill = "#d7e8ff"
            for sx, sy in ((cx+int(62*scale), cy-int(32*scale)), (cx+int(76*scale), cy+int(10*scale))):
                rr = max(1, int(3*scale))
                draw.ellipse((sx-rr, sy-rr, sx+rr, sy+rr), fill=star_fill)
        else:
            for a in range(0, 360, 45):
                ra = math.radians(a)
                x1 = cx + math.cos(ra) * r * 1.25
                y1 = cy + math.sin(ra) * r * 1.25
                x2 = cx + math.cos(ra) * r * 1.65
                y2 = cy + math.sin(ra) * r * 1.65
                draw.line((x1, y1, x2, y2), fill=c["accent"], width=max(2, int(5 * scale)))
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=c["accent"])

    if cloud:
        ox, oy = x + int(38 * scale), y + int(60 * scale)
        fill = "#d7e4ef"
        draw.ellipse((ox, oy, ox+int(72*scale), oy+int(58*scale)), fill=fill)
        draw.ellipse((ox+int(34*scale), oy-int(26*scale), ox+int(108*scale), oy+int(58*scale)), fill=fill)
        draw.ellipse((ox+int(78*scale), oy-int(6*scale), ox+int(142*scale), oy+int(58*scale)), fill=fill)
        draw.rectangle((ox+int(22*scale), oy+int(24*scale), ox+int(125*scale), oy+int(58*scale)), fill=fill)

    if rain:
        for i in range(4):
            rx = x + int((56 + i*27) * scale)
            ry = y + int(126 * scale)
            draw.line((rx, ry, rx-int(9*scale), ry+int(23*scale)), fill="#76c9ff", width=max(2, int(5*scale)))
    if snow:
        for i in range(4):
            sx = x + int((54 + i*28) * scale)
            sy = y + int(135 * scale)
            draw.text((sx, sy), "*", font=font(max(14, int(28*scale)), bold=True), fill="#ffffff", anchor="mm")
    if storm:
        pts = [(x+int(100*scale), y+int(112*scale)), (x+int(78*scale), y+int(158*scale)), (x+int(104*scale), y+int(151*scale)), (x+int(86*scale), y+int(190*scale)), (x+int(130*scale), y+int(137*scale)), (x+int(105*scale), y+int(142*scale))]
        draw.polygon(pts, fill=c["accent"])


class WeatherRenderer:
    def __init__(self, config_store, weather_manager, radar_manager=None, place_manager=None, history_store=None, spc_manager=None, tropical_manager=None, imagery_manager=None, event_manager=None) -> None:
        self.config_store = config_store
        self.weather_manager = weather_manager
        self.radar_manager = radar_manager
        self.place_manager = place_manager
        self.history_store = history_store
        self.spc_manager = spc_manager
        self.tropical_manager = tropical_manager
        self.imagery_manager = imagery_manager
        self.event_manager = event_manager
        self.cycle_started = dt.datetime.now().timestamp()
        self._logo_cache = None
        self._logo_mtime = None
        self._logo_scaled_cache: dict[tuple[Any, int, int], Image.Image] = {}
        self._context_lock = threading.RLock()
        self._config_revision = -1
        self._weather_revision = -1
        self._base_settings: dict[str, Any] | None = None
        self._base_weather: dict[str, Any] | None = None
        self._context_cache: dict[tuple[str | None, str, int, int, int, int], tuple[dict[str, Any], dict[str, Any]]] = {}
        self._context_hits = 0
        self._context_misses = 0
        self.audio_durations = {}
        self._story_state: dict[str, dict[str, Any]] = {}

    def _theme(self, settings: dict[str, Any]) -> dict[str, str]:
        colors = dict(THEMES.get(settings.get("theme", "local-90s"), THEMES["local-90s"]))
        accent = (settings.get("_branding_profile") or {}).get("accent_color")
        if accent: colors["accent"] = accent; colors["title"] = accent
        # Dedicated v0.3.10 event desks deliberately override the ordinary station
        # theme only on specialty/event channels. The everyday local service keeps
        # the user's selected RWN theme even when the Story Engine spots a hazard.
        return apply_identity_colors(colors, settings)

    def _visual(self, settings: dict[str, Any]) -> dict[str, Any]:
        return ((settings.get("presentation") or {}).get("visual_system") or {})

    def _design_dimensions(self, settings: dict[str, Any]) -> tuple[int, int]:
        # Visual System 2.0 uses a stable broadcast design canvas. The completed
        # frame is scaled once to the configured encoder resolution, preserving
        # layout proportions at 720p, 1080p, 1440p, and 4K.
        if self._visual(settings).get("enabled", True):
            return (1280, 720)
        return (int(settings["video"].get("width", 1280)), int(settings["video"].get("height", 720)))

    def _scale_frame(self, img: Image.Image, settings: dict[str, Any]) -> Image.Image:
        target = (int(settings["video"].get("width", 1280)), int(settings["video"].get("height", 720)))
        if img.size == target:
            return img
        return img.resize(target, Image.Resampling.LANCZOS)

    def _freshness_label(self, stamp: Any, now: float | None = None) -> str:
        if not stamp:
            return "UPDATE TIME UNKNOWN"
        try:
            when = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            current = dt.datetime.fromtimestamp(now, dt.timezone.utc) if now else dt.datetime.now(dt.timezone.utc)
            age = max(0, int((current - when.astimezone(dt.timezone.utc)).total_seconds()))
            if age < 60:
                return "UPDATED JUST NOW"
            if age < 3600:
                return f"UPDATED {age // 60} MIN AGO"
            return f"UPDATED {age // 3600} HR AGO"
        except Exception:
            return "UPDATED"

    def _source_badge(self, draw: ImageDraw.ImageDraw, p: dict[str, Any], c: dict[str, str], settings: dict[str, Any], source: str = "OPEN-METEO", *, right: int = 1226, y: int = 98) -> None:
        visual = self._visual(settings)
        if not visual.get("show_source_badges", True):
            return
        freshness = self._freshness_label(p.get("fetched_at")) if visual.get("show_freshness", True) else ""
        label = source if not freshness else f"{source}  •  {freshness}"
        f = font(12, bold=True, mono=True)
        box = draw.textbbox((0, 0), label, font=f)
        bw = min(420, max(180, box[2]-box[0] + 28))
        x1 = right - bw
        round_rect(draw, (x1, y, right, y+28), 7, c["panel2"], outline=c["muted"], width=1)
        draw.text((right-12, y+14), label, font=f, fill=c["muted"], anchor="rm")

    def _metric_tile(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str, value: str, c: dict[str, str], detail: str = "", *, icon_name: str | None = None, icon_enabled: bool = True) -> None:
        x1, y1, x2, y2 = box
        round_rect(draw, box, 13, c["panel"], outline=c["panel2"], width=2)
        label_x = x1 + 18
        if icon_name and icon_enabled:
            target = getattr(draw, "_image", None)
            if target is not None and paste_rwn_icon(target, rwn_metric_icon(icon_name, 30), x1+15, y1+10):
                label_x = x1 + 52
        draw.text((label_x, y1+14), label.upper(), font=font(15, bold=True, mono=True), fill=c["muted"])
        size = 30 if len(value) < 15 else 23
        draw.text((x1+18, y1+43), value, font=font(size, bold=True), fill=c["text"])
        if detail:
            draw.text((x1+18, y2-24), detail[:34], font=font(13, mono=True), fill=c["accent"])

    def _condition_story(self, cur: dict[str, Any]) -> tuple[str, str]:
        t = cur.get("temperature_2m")
        rh = cur.get("relative_humidity_2m")
        gust = cur.get("wind_gusts_10m")
        dew = dew_point_f(t, rh)
        hi = heat_index_f(t, rh)
        wc = wind_chill_f(t, cur.get("wind_speed_10m"))
        try:
            if hi is not None and float(hi) >= 100:
                return "HEAT STRESS", f"Heat index near {hi:.0f}°"
            if wc is not None and float(wc) <= 25:
                return "BITTER COLD", f"Wind chill near {wc:.0f}°"
            if gust is not None and float(gust) >= 30:
                return "GUSTY", f"Gusts up to {float(gust):.0f} mph"
            if dew is not None and dew >= 70:
                return "VERY HUMID", f"Dew point {dew:.0f}°"
            if dew is not None and dew >= 65:
                return "HUMID", f"Dew point {dew:.0f}°"
        except Exception:
            pass
        desc = str(cur.get("description") or "Current weather")
        return "RIGHT NOW", desc

    def _paint_condition_overlay(self, img: Image.Image, p: dict[str, Any], settings: dict[str, Any]) -> None:
        # Dedicated event desks own their background language. Do not mix the
        # everyday condition overlay into warning/hydrology/heat desk motifs.
        if event_identity(settings):
            return
        if not self._visual(settings).get("condition_backgrounds", True):
            return
        cur = p.get("current") or {}
        try: code = int(cur.get("weather_code"))
        except Exception: code = -1
        is_day = bool(cur.get("is_day", 1))
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        w, h = img.size
        if not is_day:
            od.rectangle((0, 88, w, h-86), fill=(0, 4, 20, 38))
        if code in {95, 96, 99}:
            od.rectangle((0, 88, w, h-86), fill=(20, 12, 35, 48))
        elif code in {51,53,55,56,57,61,63,65,66,67,80,81,82}:
            od.rectangle((0, 88, w, h-86), fill=(8, 24, 42, 30))
        elif code in {71,73,75,77,85,86}:
            od.rectangle((0, 88, w, h-86), fill=(70, 100, 120, 22))
        elif code in {0,1} and is_day:
            # soft warm glow, kept deliberately subtle so theme identity remains intact
            for r, a in ((260, 12), (190, 16), (125, 20)):
                od.ellipse((w-r-40, 100-r//3, w+40, 100+r*2), fill=(255, 211, 71, a))
        img.paste(overlay, (0, 0), overlay)

    def _primary(self, settings: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
        pid = settings.get("primary_location_id")
        return snapshot.get("locations", {}).get(pid)

    def _severity_rank(self, severity: str | None) -> int:
        return {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3, "Unknown": 4}.get(severity or "Unknown", 4)

    def _takeover_alert(self, settings: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
        alerts = snapshot.get("alerts") or []
        cfg = settings.get("alerts", {})
        if not alerts or not cfg.get("takeover_enabled", True):
            return None
        threshold = self._severity_rank(cfg.get("takeover_min_severity", "Severe"))
        for alert in alerts:
            if self._severity_rank(alert.get("severity")) <= threshold:
                return alert
        return None

    def _local_datetime(self, snapshot: dict[str, Any], settings: dict[str, Any], now: float) -> dt.datetime:
        primary = self._primary(settings, snapshot)
        tz_name = ((primary or {}).get("location") or {}).get("timezone")
        try:
            tz = ZoneInfo(tz_name) if tz_name and tz_name != "auto" else dt.datetime.now().astimezone().tzinfo
        except Exception:
            tz = dt.datetime.now().astimezone().tzinfo
        return dt.datetime.fromtimestamp(now, tz=tz)

    def _scheduled_update_elapsed(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float) -> float | None:
        # Severe-weather takeover always outranks scheduled presentation blocks.
        if self._takeover_alert(settings, snapshot):
            return None
        cfg = (settings.get("presentation", {}) or {}).get("scheduled_updates", {}) or {}
        if not cfg.get("enabled", False):
            return None
        marks = sorted({int(x) for x in (cfg.get("minute_marks") or []) if 0 <= int(x) <= 59})
        if not marks:
            return None
        local_now = self._local_datetime(snapshot, settings, now)
        candidates = []
        for hour_delta in (0, -1):
            base = local_now.replace(second=0, microsecond=0) + dt.timedelta(hours=hour_delta)
            for mark in marks:
                candidate = base.replace(minute=mark)
                if candidate <= local_now:
                    candidates.append(candidate)
        if not candidates:
            return None
        started = max(candidates)
        elapsed = (local_now - started).total_seconds()
        window = max(30, int(cfg.get("window_seconds", 120)))
        return elapsed if 0 <= elapsed < window else None

    def scheduled_update_active(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float | None = None) -> bool:
        return self._scheduled_update_elapsed(settings, snapshot, now or dt.datetime.now().timestamp()) is not None

    def _daypart(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float) -> str:
        local = self._local_datetime(snapshot, settings, now)
        cfg = settings.get("dayparts") or {}
        h = local.hour
        morning = int(cfg.get("morning_start", 5)); daytime = int(cfg.get("daytime_start", 10)); evening = int(cfg.get("evening_start", 17)); overnight = int(cfg.get("overnight_start", 22))
        if morning <= h < daytime: return "morning"
        if daytime <= h < evening: return "daytime"
        if evening <= h < overnight: return "evening"
        return "overnight"

    def _max_next(self, hourly: dict[str, Any], key: str, count: int = 12) -> float:
        vals = hourly.get(key) or []; times = hourly.get("time") or []; start = 0
        if times:
            now = dt.datetime.now()
            for i, value in enumerate(times):
                try:
                    stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    if stamp.tzinfo is not None: stamp = stamp.astimezone().replace(tzinfo=None)
                    if stamp >= now.replace(minute=0, second=0, microsecond=0): start = i; break
                except Exception: continue
        nums = []
        for v in vals[start:start+count]:
            try: nums.append(float(v))
            except Exception: pass
        return max(nums) if nums else 0.0

    def _sum_next(self, hourly: dict[str, Any], key: str, count: int = 24) -> float:
        vals = hourly.get(key) or []; times = hourly.get("time") or []; start = 0
        if times:
            local_now = dt.datetime.now()
            for i, value in enumerate(times):
                try:
                    stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    if stamp.tzinfo is not None: stamp = stamp.astimezone().replace(tzinfo=None)
                    if stamp >= local_now.replace(minute=0, second=0, microsecond=0): start = i; break
                except Exception: continue
        total = 0.0
        for value in vals[start:start+count]:
            try: total += max(0.0, float(value))
            except Exception: pass
        return total

    def _dewpoint_range_next(self, hourly: dict[str, Any], count: int = 12) -> tuple[float | None, float | None]:
        temps=hourly.get("temperature_2m") or []; rhs=hourly.get("relative_humidity_2m") or []; times=hourly.get("time") or []
        start=0
        if times:
            now=dt.datetime.now()
            for i,value in enumerate(times):
                try:
                    stamp=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
                    if stamp.tzinfo is not None: stamp=stamp.astimezone().replace(tzinfo=None)
                    if stamp >= now.replace(minute=0,second=0,microsecond=0): start=i; break
                except Exception: continue
        vals=[]
        for i in range(start,min(len(times) if times else max(len(temps),len(rhs)),start+count)):
            try:
                dp=dew_point_f(temps[i],rhs[i])
                if dp is not None: vals.append(float(dp))
            except Exception: pass
        return (min(vals),max(vals)) if vals else (None,None)

    def _weather_story(self, settings: dict[str, Any], snapshot: dict[str, Any], primary: dict[str, Any] | None, now: float, *, stable: bool = True) -> dict[str, Any]:
        story = classify_story(settings, snapshot, primary)
        cfg = settings.get("story_engine") or {}
        if not stable or not cfg.get("enabled", True):
            return story
        key = f"{settings.get('_channel_mode','local')}:{settings.get('_render_location_id') or settings.get('primary_location_id') or 'default'}"
        hold_seconds = max(0, int(cfg.get("hold_minutes", 8))) * 60
        with self._context_lock:
            previous = self._story_state.get(key)
            if previous:
                old = previous.get("story") or {}
                age = max(0.0, now - float(previous.get("changed_at") or 0))
                old_priority = int(STORY_PRIORITY.get(str(old.get("id") or "quiet"), 0))
                new_priority = int(STORY_PRIORITY.get(str(story.get("id") or "quiet"), 0))
                # Hold only ordinary forecast stories. Official/higher-impact stories
                # must be able to clear immediately when their triggering data clears.
                if age < hold_seconds and old_priority < 70 and new_priority <= old_priority and old.get("id") != story.get("id"):
                    held = copy.deepcopy(old)
                    held["held"] = True
                    held["held_seconds_remaining"] = int(hold_seconds - age)
                    return held
                if old.get("id") == story.get("id"):
                    previous["story"] = copy.deepcopy(story)
                    return story
            self._story_state[key] = {"story": copy.deepcopy(story), "changed_at": now}
        return story

    def story_status(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = now or dt.datetime.now().timestamp()
        primary = self._primary(settings, snapshot) or {}
        story = self._weather_story(settings, snapshot, primary, now)
        cfg = settings.get("story_engine") or {}
        sequence, sections = compose_story_sequence(
            story, station_id=(settings.get("presentation") or {}).get("show_station_id", True),
            story_brief=cfg.get("show_story_brief", True), include_context=cfg.get("include_context", True),
            max_slides=int(cfg.get("max_slides", 13)),
        )
        channel_mode=str(settings.get("_channel_mode") or "local")
        region_id=str((settings.get("_region") or {}).get("id") or "default")
        studio_override=active_sequence(settings, region_id, channel_mode, self._local_datetime(snapshot, settings, now))
        takeover=bool(self._takeover_alert(settings,snapshot))
        directing=bool(cfg.get("enabled",True) and (settings.get("smart_programming") or {}).get("enabled",True) and channel_mode=="local" and not studio_override and not takeover)
        override="severe_takeover" if takeover else "studio" if studio_override else "specialty_channel" if channel_mode!="local" else None
        return {**story, "sequence": sequence, "sections": sections, "enabled": bool(cfg.get("enabled", True)), "directing": directing, "override": override}

    def _smart_wanted(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float) -> list[str]:
        pres = settings.get("presentation") or {}; smart = settings.get("smart_programming") or {}; dayparts = settings.get("dayparts") or {}
        if dayparts.get("enabled", True):
            part = self._daypart(settings, snapshot, now)
            wanted = list(((dayparts.get("sequences") or {}).get(part)) or (pres.get("sequence") or []))
        else:
            wanted = list(pres.get("sequence") or [])
        pool = set(pres.get("sequence") or wanted)
        primary = self._primary(settings, snapshot) or {}; hourly = primary.get("hourly") or {}
        story_cfg = settings.get("story_engine") or {}
        story_directed = bool(smart.get("enabled", True) and story_cfg.get("enabled", True) and settings.get("_channel_mode", "local") == "local")
        if story_directed:
            story = self._weather_story(settings, snapshot, primary, now)
            composed, _ = compose_story_sequence(
                story, station_id=pres.get("show_station_id", True), story_brief=story_cfg.get("show_story_brief", True),
                include_context=story_cfg.get("include_context", True), max_slides=int(story_cfg.get("max_slides", 13)),
            )
            wanted = [x for x in composed if x in pool or x in {"station_id", "story_brief"}]

        graphics=settings.get("forecast_graphics") or {}
        if not graphics.get("enabled", True):
            wanted=[x for x in wanted if x not in {"day_ahead","humidity_outlook","wind_outlook","rain_accumulation"}]
        else:
            if not graphics.get("wind_outlook_enabled",True): wanted=[x for x in wanted if x != "wind_outlook"]
            if not graphics.get("humidity_outlook_enabled",True): wanted=[x for x in wanted if x != "humidity_outlook"]
            if not graphics.get("rain_accumulation_enabled",True): wanted=[x for x in wanted if x != "rain_accumulation"]
        if not smart.get("enabled", True): return wanted

        rain = self._max_next(hourly, "precipitation_probability", 12)
        rain_total = self._sum_next(hourly, "precipitation", 24)
        gust = self._max_next(hourly, "wind_gusts_10m", 12)
        low_dew, high_dew = self._dewpoint_range_next(hourly, 12)
        storm_hourly = ((snapshot.get("storm_guidance") or {}).get("hourly") or {})
        storm = self._max_next(storm_hourly, "thunderstorm_probability", 12)
        spc = settings.get("_spc_outlook") or {}
        rank = int(((spc.get("day1") or {}).get("rank")) or 0)
        min_risk = {"TSTM":1,"MRGL":2,"SLGT":3,"ENH":4,"MDT":5,"HIGH":6}.get((settings.get("spc") or {}).get("minimum_smart_risk", "MRGL"), 2)
        rain_threshold=int(smart.get("rain_threshold",20)); storm_threshold=int(smart.get("storm_threshold",15)); wind_threshold=int(smart.get("wind_threshold",25))
        humid_threshold=float(smart.get("humid_dewpoint_threshold",65)); dry_threshold=float(smart.get("dry_dewpoint_threshold",35))

        if rain < rain_threshold:
            wanted = [x for x in wanted if x != "precipitation"]
        if rain_total < 0.05:
            wanted = [x for x in wanted if x != "rain_accumulation"]
        if gust < wind_threshold:
            wanted = [x for x in wanted if x != "wind_outlook"]
        humidity_notable = (high_dew is not None and high_dew >= humid_threshold) or (low_dew is not None and low_dew <= dry_threshold)
        if not humidity_notable:
            wanted = [x for x in wanted if x != "humidity_outlook"]
        if storm < storm_threshold:
            wanted = [x for x in wanted if x != "storm_outlook"]
        if rank < min_risk:
            wanted = [x for x in wanted if x != "spc_outlook"]
        elif "spc_outlook" not in wanted:
            idx = wanted.index("radar_local") if "radar_local" in wanted else min(5, len(wanted)); wanted.insert(idx, "spc_outlook")
        if not (settings.get("history") or {}).get("enabled", True):
            wanted = [x for x in wanted if x not in {"weather_history","today_so_far","past_24_hours"}]

        # Forecast Graphics 2.0 turns the rundown into a simple weather story. We do
        # not delete user-selected graphics here; notable products are moved near the
        # top so the channel explains the important signal before the long-range recap.
        if smart.get("smart_story_ordering", True) and not story_directed:
            def promote(item: str, anchor: str) -> None:
                nonlocal wanted
                if item not in wanted: return
                wanted.remove(item)
                if anchor in wanted: wanted.insert(wanted.index(anchor)+1,item)
                else: wanted.insert(min(2,len(wanted)),item)
            if rank >= min_risk or storm >= storm_threshold:
                promote("spc_outlook","current")
                promote("storm_outlook","spc_outlook" if "spc_outlook" in wanted else "current")
                if gust >= wind_threshold: promote("wind_outlook","storm_outlook" if "storm_outlook" in wanted else "current")
                if rain >= rain_threshold: promote("precipitation","storm_outlook" if "storm_outlook" in wanted else "current")
                if rain_total >= 0.05: promote("rain_accumulation","precipitation" if "precipitation" in wanted else "current")
            elif rain >= rain_threshold:
                promote("precipitation","hourly" if "hourly" in wanted else "current")
                if rain_total >= 0.05: promote("rain_accumulation","precipitation")
            if gust >= wind_threshold and "wind_outlook" in wanted:
                promote("wind_outlook","hourly" if "hourly" in wanted else "current")
            if humidity_notable and "humidity_outlook" in wanted:
                promote("humidity_outlook","hourly" if "hourly" in wanted else "current")
        return wanted

    def programming_status(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = now or dt.datetime.now().timestamp()
        return {"daypart": self._daypart(settings, snapshot, now), "smart_enabled": bool((settings.get("smart_programming") or {}).get("enabled", True)), "story": self.story_status(settings, snapshot, now), "sequence": [x for x,_ in self._sequence(settings, snapshot, now)]}

    def playout_status(self, location_id: str | None = None, channel_mode: str = "local", now: float | None = None) -> dict[str, Any]:
        """Return the renderer's actual timeline position for Studio Control Room."""
        now = float(now or dt.datetime.now().timestamp())
        settings, snapshot = self._channel_context(location_id, channel_mode)
        primary = self._primary(settings, snapshot)
        if not primary:
            return {"current_slide": "setup", "next_slide": None, "progress": 0.0, "elapsed_seconds": 0.0, "remaining_seconds": None, "duration_seconds": None, "sequence": [], "story": self.story_status(settings, snapshot, now), "severe_takeover": False}
        seq, idx, name, progress, elapsed, duration = self._timeline(settings, snapshot, now)
        next_name = seq[(idx + 1) % len(seq)][0] if seq else None
        prev_name = seq[(idx - 1) % len(seq)][0] if seq else None
        return {
            "current_slide": name, "next_slide": next_name, "previous_slide": prev_name,
            "index": idx, "progress": round(float(progress), 4),
            "elapsed_seconds": round(float(elapsed), 2), "remaining_seconds": round(max(0.0, float(duration) - float(elapsed)), 2),
            "duration_seconds": int(duration),
            "sequence": [{"slide": slide, "duration_seconds": int(seconds)} for slide, seconds in seq],
            "story": self.story_status(settings, snapshot, now),
            "severe_takeover": bool(self._takeover_alert(settings, snapshot)),
            "daypart": self._daypart(settings, snapshot, now),
        }

    def timeline_status(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        """Expose the renderer timeline for Studio Control Room 2.0.

        This uses the same cycle origin and sequence selection as render_channel(),
        so LIVE/NEXT labels match what the renderer is actually producing.
        """
        now = now or dt.datetime.now().timestamp()
        seq, idx, name, progress, elapsed, duration = self._timeline(settings, snapshot, now)
        next_name, next_duration = seq[(idx + 1) % len(seq)]
        return {
            "current": name, "next": next_name, "index": idx, "count": len(seq),
            "elapsed_seconds": round(elapsed, 2), "duration_seconds": duration,
            "remaining_seconds": round(max(0.0, duration - elapsed), 2),
            "progress": round(progress, 4), "next_duration_seconds": next_duration,
            "sequence": [{"slide": slide, "duration_seconds": seconds} for slide, seconds in seq],
        }

    def _sequence(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float | None = None):
        durations = settings.get("slides", {})
        pres = settings.get("presentation", {})
        radar_cfg = settings.get("radar", {})
        radar_views = radar_cfg.get("views", {})
        radar_name_to_view = {
            "radar": "local", "radar_local": "local", "alert_radar": "local",
            "radar_regional": "regional", "radar_wide": "wide",
        }

        takeover = self._takeover_alert(settings, snapshot)
        if takeover:
            wanted = (settings.get("alerts", {}) or {}).get("takeover_sequence") or [
                "alert", "alert_radar", "current", "nws_forecast", "alert_radar"
            ]
        else:
            # v0.2.2.1 Local on the 8s is driven by ChannelWorker as a dedicated
            # programming block. The normal renderer timeline no longer swaps its
            # sequence merely because the clock is inside the scheduled trigger
            # window. This lets the block finish naturally even after that window.
            region_id = str((settings.get("_region") or {}).get("id") or "default")
            channel_mode = str(settings.get("_channel_mode") or "local")
            wanted = self._smart_wanted(settings, snapshot, now if now is not None else dt.datetime.now().timestamp())
            studio_sequence = active_sequence(settings, region_id, channel_mode, self._local_datetime(snapshot, settings, now or dt.datetime.now().timestamp()))
            if studio_sequence:
                wanted = studio_sequence

        tropical = settings.get("_tropical") or {}
        if settings.get("_channel_mode") == "local" and tropical.get("segment_active") and "tropical_update" not in wanted:
            insert_at = wanted.index("current") + 1 if "current" in wanted else min(1, len(wanted))
            wanted.insert(insert_at, "tropical_update")

        seq = []
        for name in wanted:
            if str(name).startswith("bumper:"):
                item = bumper(settings, str(name).split(":", 1)[1]) or {}
                seq.append((str(name), max(3, min(30, int(item.get("duration", 6)))))); continue
            if name == "station_id" and not pres.get("show_station_id", True):
                continue
            if name == "story_brief":
                story_cfg=settings.get("story_engine") or {}
                if not story_cfg.get("enabled",True) or not story_cfg.get("show_story_brief",True):
                    continue
            if name in radar_name_to_view:
                if not radar_cfg.get("enabled", True):
                    continue
                view = radar_name_to_view[name]
                if not (radar_views.get(view) or {}).get("enabled", True):
                    continue
            if name == "regional_map" and not (settings.get("maps") or {}).get("regional_map_enabled", True):
                continue
            if name == "storm_outlook" and not (settings.get("storm_guidance") or {}).get("enabled", True):
                continue
            if name == "spc_outlook" and not (settings.get("spc") or {}).get("enabled", True):
                continue
            if name == "weather_history" and not (settings.get("history") or {}).get("enabled", True):
                continue
            local_data=settings.get("local_data") or {}
            if name in {"today_so_far","past_24_hours"} and not (settings.get("history") or {}).get("enabled",True): continue
            if name == "air_quality" and not (local_data.get("air_quality") or {}).get("enabled",True): continue
            if name == "local_rivers" and not (local_data.get("rivers") or {}).get("enabled",True): continue
            if name == "climate_context" and not (local_data.get("climate") or {}).get("enabled",True): continue
            fg = settings.get("forecast_graphics") or {}
            if name in {"day_ahead", "humidity_outlook", "wind_outlook", "rain_accumulation"} and not fg.get("enabled", True):
                continue
            if name == "humidity_outlook" and not fg.get("humidity_outlook_enabled", True):
                continue
            if name == "wind_outlook" and not fg.get("wind_outlook_enabled", True):
                continue
            if name == "rain_accumulation" and not fg.get("rain_accumulation_enabled", True):
                continue
            engine_cfg=((settings.get("maps") or {}).get("engine3") or (settings.get("maps") or {}).get("engine2") or {})
            engine_layers=(engine_cfg.get("layers") or {})
            if name in {"map_engine","spc_map","spc_hazards","surface_map","qpf_map","hazard_map"} and not engine_cfg.get("enabled", True): continue
            if name == "map_satellite" and not engine_layers.get("satellite", True): continue
            if name == "map_lightning" and not engine_layers.get("lightning", True): continue
            if name == "spc_map" and not engine_layers.get("spc_outlook", True): continue
            if name == "spc_hazards" and not engine_layers.get("spc_probabilities", True): continue
            if name == "surface_map" and not engine_layers.get("surface_chart", True): continue
            if name == "qpf_map" and not engine_layers.get("qpf", True): continue
            if name == "hazard_map" and not engine_layers.get("alerts", True): continue
            if name.startswith("tropical_") and not (settings.get("tropical") or {}).get("enabled", True):
                continue
            fallback = durations.get("radar", 16) if name.startswith("radar") or name == "alert_radar" else 10
            seq.append((name, max(3, int(durations.get(name, fallback)))))

        if snapshot.get("alerts") and not takeover and not any(name == "alert" for name, _ in seq):
            seq.insert(1 if seq else 0, ("alert", max(3, int(durations.get("alert", 14)))))
        extras = self.audio_durations.get((settings.get("primary_location_id"), settings.get("_channel_mode", "local")), {})
        if (settings.get("audio") or {}).get("enabled") and not takeover:
            extension = float((settings.get("audio") or {}).get("max_extension", 3))
            seq = [(name, max(seconds, min(seconds + extension, extras.get(name, seconds)))) for name, seconds in seq]
        return seq or [("current", 12)]

    def _timeline(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float):
        seq = self._sequence(settings, snapshot, now)
        total = sum(d for _, d in seq)
        offset = (now - self.cycle_started) % max(1, total)
        cursor = 0.0
        for idx, (name, duration) in enumerate(seq):
            if offset < cursor + duration:
                elapsed = offset - cursor
                return seq, idx, name, elapsed / duration, elapsed, duration
            cursor += duration
        return seq, 0, seq[0][0], 0.0, 0.0, seq[0][1]

    def _paint_event_background(self, img: Image.Image, c: dict[str, str], settings: dict[str, Any], now: float) -> None:
        """Paint the approved v0.3.10 desk motif without replacing map/data content.

        The patterns are intentionally restrained: they establish channel identity
        at a glance while preserving high-contrast broadcast readability.
        """
        draw = ImageDraw.Draw(img)
        w, h = img.size
        key = str(c.get("event_key") or "")
        # dark-to-light vertical base using the identity palette
        def rgb(hexv: str) -> tuple[int, int, int]:
            v=hexv.lstrip("#"); return tuple(int(v[i:i+2],16) for i in (0,2,4))
        a=rgb(str(c.get("bg") or "#061523")); b=rgb(str(c.get("panel2") or "#0b2f63"))
        for y in range(h):
            t=y/max(1,h-1); mix=min(0.72,0.12+t*0.48)
            col=tuple(int(a[i]*(1-mix)+b[i]*mix) for i in range(3))
            draw.line((0,y,w,y),fill=col)
        if not (settings.get("event_identity") or {}).get("background_motifs", True):
            return
        phase=int(now*8)%120 if (settings.get("presentation") or {}).get("background_motion",True) else 0
        if key == "severe":
            for x in range(-240+phase,w+240,150):
                draw.polygon([(x,88),(x+55,88),(x-125,h-86),(x-180,h-86)], fill="#260d14")
            for y in range(118,h-86,58): draw.line((0,y,w,y),fill="#3d111a",width=1)
            draw.rectangle((0,88,14,h-86),fill=c["accent"])
        elif key == "flood":
            for y in range(135,h-86,52):
                pts=[]
                for x in range(-20,w+20,20):
                    yy=y+int(math.sin((x+phase)/85)*8); pts.append((x,yy))
                draw.line(pts,fill="#0f5b72",width=2)
            for x in range(0,w,96): draw.line((x,105,x,h-86),fill="#0a3b4c",width=1)
        elif key == "winter":
            for x in range(-160+phase,w+160,110): draw.line((x,88,x-210,h-86),fill="#214d83",width=2)
            for x,y in ((150,150),(330,260),(590,145),(815,330),(1035,180),(1170,440),(430,470)):
                r=4; draw.ellipse((x-r,y-r,x+r,y+r),fill="#bcecff")
        elif key == "heat":
            # broadcast heat shimmer: broad bands and a restrained sun halo
            for y in range(135,h-86,58):
                pts=[]
                for x in range(-20,w+20,24): pts.append((x,y+int(math.sin((x+phase)/70)*6)))
                draw.line(pts,fill="#7d2810",width=2)
            for r,alpha in ((260,1),(185,1),(120,1)):
                draw.ellipse((w-r-15,70-r//4,w+35,70+r*2),outline="#a43a12",width=3)
        elif key == "wildfire":
            for band in range(5):
                y=145+band*95
                pts=[]
                for x in range(-50,w+50,35): pts.append((x,y+int(math.sin((x+phase+band*35)/100)*18)))
                draw.line(pts,fill="#6d421b",width=9)
            draw.rectangle((0,88,w,96),fill=c["accent"])
        elif key == "tropical":
            cx,cy=w-175,245
            for r in (85,145,215,290): draw.arc((cx-r,cy-r,cx+r,cy+r),15,315,fill="#12649b",width=2)
            for y in range(120,h-86,70): draw.line((0,y,w,y),fill="#0b416e",width=1)
            for x in range(0,w,100): draw.line((x,88,x,h-86),fill="#09385f",width=1)

    def _paint_background(self, img: Image.Image, c: dict[str, str], settings: dict[str, Any], now: float) -> None:
        draw = ImageDraw.Draw(img)
        w, h = img.size
        if c.get("event_key"):
            self._paint_event_background(img,c,settings,now)
            return
        theme = settings.get("theme", "local-90s")
        if theme == "classic-blue":
            for y in range(h):
                t = y / max(1, h-1)
                col = (5 + int(6*t), 31 + int(30*t), 78 + int(45*t))
                draw.line((0, y, w, y), fill=col)
            for x in range(-h, w, 90):
                draw.line((x, 0, x+h, h), fill=(17, 67, 127), width=2)
        elif theme == "retro-2000":
            for y in range(h):
                t = y / max(1, h-1)
                col = (9 + int(13*t), 30 + int(24*t), 49 + int(36*t))
                draw.line((0, y, w, y), fill=col)
            phase = int(now * 14) % 120 if settings.get("presentation", {}).get("background_motion", True) else 0
            for x in range(-120 + phase, w+120, 120):
                draw.line((x, 88, x-230, h-86), fill=(28, 73, 103), width=3)
        elif theme == "terminal-80s":
            draw.rectangle((0, 0, w, h), fill=(4, 27, 59))
            phase = int(now * 8) % 32 if settings.get("presentation", {}).get("background_motion", True) else 0
            for y in range(100 + phase, h-86, 32):
                draw.line((0, y, w, y), fill=(8, 55, 91), width=1)
            for x in range(0, w, 80):
                draw.line((x, 88, x, h-86), fill=(5, 43, 79), width=1)
            draw.rectangle((0, 88, 12, h-86), fill=c["accent"])
        elif theme == "cable-gold":
            for y in range(h):
                t = y / max(1, h-1)
                col = (10 + int(12*t), 36 + int(38*t), 57 + int(45*t))
                draw.line((0, y, w, y), fill=col)
            phase = int(now * 10) % 160 if settings.get("presentation", {}).get("background_motion", True) else 0
            for x in range(-200 + phase, w+200, 160):
                draw.polygon([(x, 88), (x+55, 88), (x-170, h-86), (x-225, h-86)], fill=(18, 67, 86))
            draw.line((0, 102, w, 102), fill=(244, 200, 75), width=2)
        else:
            for y in range(h):
                t = y / max(1, h-1)
                col = (7 + int(7*t), 36 + int(34*t), 86 + int(42*t))
                draw.line((0, y, w, y), fill=col)
            for y in range(105, h-86, 34):
                draw.line((0, y, w, y), fill=(15, 61, 112), width=1)

    def _render_slide(self, name: str, settings: dict[str, Any], snapshot: dict[str, Any], primary: dict[str, Any], now: float, progress: float) -> Image.Image:
        # All broadcast graphics are authored on one logical canvas and scaled once
        # at the end. This removes the old fixed-720p limitation without forcing every
        # individual drawing primitive to understand encoder resolution.
        w, h = self._design_dimensions(settings)
        c = self._theme(settings)
        img = Image.new("RGB", (w, h), c["bg"])
        self._paint_background(img, c, settings, now)
        self._paint_condition_overlay(img, primary, settings)
        draw = ImageDraw.Draw(img)
        if name == "local8_intro": self._draw_local8_intro(draw, w, h, settings, primary, c)
        elif name == "station_id": self._draw_station_id(draw, w, h, settings, primary, c, now)
        elif name == "current": self._draw_current(draw, w, h, settings, primary, c)
        elif name == "story_brief": self._draw_story_brief(draw, w, h, settings, snapshot, primary, c, now)
        elif name == "today": self._draw_today(draw, w, h, settings, primary, c)
        elif name == "nws_forecast": self._draw_nws_forecast(draw, w, h, settings, primary, c)
        elif name == "day_ahead": self._draw_day_ahead(draw, w, h, settings, primary, c)
        elif name == "temperature_trend": self._draw_temperature_trend(draw, w, h, settings, primary, c)
        elif name == "hourly": self._draw_hourly(draw, w, h, settings, primary, c)
        elif name == "humidity_outlook": self._draw_humidity_outlook(draw, w, h, settings, primary, c)
        elif name == "wind_outlook": self._draw_wind_outlook(draw, w, h, settings, primary, c)
        elif name == "precipitation": self._draw_precipitation(draw, w, h, settings, primary, c)
        elif name == "rain_accumulation": self._draw_rain_accumulation(draw, w, h, settings, primary, c)
        elif name == "storm_outlook": self._draw_storm_outlook(draw, w, h, settings, snapshot, primary, c)
        elif name == "spc_outlook": self._draw_spc_outlook(draw, w, h, settings, primary, c)
        elif name == "tropical_update": self._draw_tropical_update(draw, w, h, settings, primary, c)
        elif name == "tropical_systems": self._draw_tropical_systems(draw, w, h, settings, primary, c)
        elif name == "tropical_track": self._draw_tropical_track(draw, w, h, settings, primary, c, now)
        elif name == "tropical_local": self._draw_tropical_local(draw, w, h, settings, primary, c)
        elif name == "event_summary": self._draw_event_summary(draw, w, h, settings, snapshot, primary, c)
        elif name == "map_engine": self._draw_map_engine(img, draw, w, h, settings, snapshot, primary, c, now, progress)
        elif name == "spc_map": self._draw_official_map_product(img, draw, w, h, settings, primary, c, "spc_day1", "SPC DAY 1 OUTLOOK", "GEOGRAPHIC SEVERE WEATHER RISK")
        elif name == "spc_hazards": self._draw_spc_hazard_probabilities(img, draw, w, h, settings, primary, c)
        elif name == "surface_map": self._draw_official_map_product(img, draw, w, h, settings, primary, c, "wpc_surface", "DAY 1 WEATHER MAP", "FRONTS • HIGHS/LOWS • SIGNIFICANT WEATHER")
        elif name == "qpf_map": self._draw_official_map_product(img, draw, w, h, settings, primary, c, "wpc_qpf_day1", "24-HOUR PRECIPITATION FORECAST", "WPC QUANTITATIVE PRECIPITATION FORECAST")
        elif name == "hazard_map": self._draw_hazard_map(img, draw, w, h, settings, snapshot, primary, c, now, progress)
        elif name in {"map_satellite", "map_lightning"}: self._draw_goes_product(img, ImageDraw.Draw(img), w, h, settings, c, "satellite" if name == "map_satellite" else "lightning")
        elif name.startswith("bumper:"): self._draw_studio_bumper(draw, w, h, settings, name.split(":", 1)[1], c)
        elif name == "condition_focus": self._draw_condition_focus(draw, w, h, settings, primary, c)
        elif name == "today_so_far": self._draw_today_so_far(draw, w, h, settings, primary, c)
        elif name == "past_24_hours": self._draw_past_24_hours(draw, w, h, settings, primary, c)
        elif name == "air_quality": self._draw_air_quality(draw, w, h, settings, primary, c)
        elif name == "local_rivers": self._draw_local_rivers(draw, w, h, settings, primary, c)
        elif name == "climate_context": self._draw_climate_context(draw, w, h, settings, primary, c)
        elif name == "weather_history": self._draw_weather_history(draw, w, h, settings, primary, c)
        elif name == "seven_day": self._draw_seven_day(draw, w, h, settings, primary, c)
        elif name in {"radar", "radar_local", "radar_regional", "radar_wide", "alert_radar"}:
            view = {"radar": "local", "radar_local": "local", "radar_regional": "regional", "radar_wide": "wide", "alert_radar": "local"}[name]
            self._draw_radar(img, draw, w, h, settings, primary, c, now, progress, view=view, snapshot=snapshot, alert_mode=(name == "alert_radar"))
        elif name == "regional_map": self._draw_regional_map(img, ImageDraw.Draw(img), w, h, settings, snapshot, primary, c)
        elif name == "regional": self._draw_regional(draw, w, h, settings, snapshot, primary, c)
        elif name == "almanac": self._draw_almanac(draw, w, h, settings, primary, c)
        elif name == "alert": self._draw_alert(draw, w, h, settings, snapshot, primary, c)
        self._draw_branding_logo(img, settings, name)
        self._draw_footer(ImageDraw.Draw(img), w, h, settings, snapshot, c, now)
        return self._scale_frame(img, settings)

    def _channel_context(self, location_id: str | None = None, channel_mode: str = "local") -> tuple[dict[str, Any], dict[str, Any]]:
        spc_revision = self.spc_manager.revision() if self.spc_manager and hasattr(self.spc_manager, "revision") else 0
        tropical_revision = self.tropical_manager.revision() if self.tropical_manager and hasattr(self.tropical_manager, "revision") else 0
        with self._context_lock:
            config_revision, changed_settings = self.config_store.snapshot_if_changed(self._config_revision)
            weather_revision, changed_weather = self.weather_manager.snapshot_if_changed(self._weather_revision)
            if changed_settings is not None:
                self._base_settings = changed_settings
                self._config_revision = config_revision
                self._context_cache.clear()
            if changed_weather is not None:
                self._base_weather = changed_weather
                self._weather_revision = weather_revision
                self._context_cache.clear()
            key = (location_id, channel_mode, config_revision, weather_revision, spc_revision, tropical_revision)
            cached = self._context_cache.get(key)
            if cached is not None:
                self._context_hits += 1
                return cached
            self._context_misses += 1
            settings = copy.deepcopy(self._base_settings or {})
            base_snapshot = self._base_weather or {}
            snapshot = dict(base_snapshot)
        if location_id:
            settings["primary_location_id"] = location_id
            settings["_render_location_id"] = location_id
            snapshot["alerts"] = (base_snapshot.get("alerts_by_location") or {}).get(location_id) or []
            snapshot["storm_guidance"] = (base_snapshot.get("storm_guidance_by_location") or {}).get(location_id) or {}
        else:
            settings["_render_location_id"] = settings.get("primary_location_id")

        region = region_for_location(settings, settings.get("_render_location_id"))
        apply_region_identity(settings, region)
        if region and channel_mode != "local":
            settings["primary_location_id"] = region.get("primary_location_id") or settings.get("primary_location_id")

        channels = settings.get("channels") or {}
        if channel_mode == "local":
            # Per-ZIP local channels intentionally leave shared radar/map products to
            # the dedicated RWN Radar channel. Forecast/history/SPC data are ZIP-local.
            zip_seq = list(channels.get("zip_sequence") or [])
            if zip_seq:
                settings.setdefault("presentation", {})["sequence"] = zip_seq
            allowed = set(zip_seq) if zip_seq else None
            if allowed and isinstance((settings.get("dayparts") or {}).get("sequences"), dict):
                for part, seq in settings["dayparts"]["sequences"].items():
                    filtered = [x for x in seq if x in allowed]
                    settings["dayparts"]["sequences"][part] = filtered or zip_seq
            loc = next((x for x in settings.get("locations", []) if x.get("id") == settings.get("primary_location_id")), None)
            if loc:
                settings["station_slogan"] = f"LOCAL WEATHER • {loc.get('postal_code','')} • RADAR ON RWN RADAR"
        elif channel_mode == "radar":
            settings.setdefault("presentation", {}).setdefault("scheduled_updates", {})["enabled"] = False
            settings.setdefault("dayparts", {})["enabled"] = False
            settings.setdefault("smart_programming", {})["enabled"] = False
            settings["presentation"]["sequence"] = list(channels.get("radar_sequence") or ["station_id","radar_local","radar_regional","regional_map","radar_wide"])
            settings["station_slogan"] = "RWN RADAR • LOCAL • REGIONAL • WIDE AREA"
        elif channel_mode == "severe":
            settings.setdefault("presentation", {}).setdefault("scheduled_updates", {})["enabled"] = False
            settings.setdefault("dayparts", {})["enabled"] = False
            settings.setdefault("smart_programming", {})["enabled"] = False
            settings["presentation"]["sequence"] = list(channels.get("severe_idle_sequence") or ["station_id","current","spc_outlook","storm_outlook","radar_local","regional_map","radar_regional"])
            settings["station_slogan"] = "RWN SEVERE WEATHER CENTER • ALERTS • RADAR"
        elif channel_mode == "tropics":
            settings.setdefault("presentation", {}).setdefault("scheduled_updates", {})["enabled"] = False
            settings.setdefault("dayparts", {})["enabled"] = False
            settings.setdefault("smart_programming", {})["enabled"] = False
            settings["presentation"]["sequence"] = list(channels.get("tropics_sequence") or ["tropical_update","tropical_systems","tropical_track","tropical_local","radar_wide"])
            settings["station_slogan"] = "RWN TROPICS WATCH • OFFICIAL NHC DATA • GULF FOCUS"
        elif channel_mode.startswith("event_"):
            event_type=channel_mode.removeprefix("event_"); event_cfg=settings.get("event_channels") or {}
            settings.setdefault("presentation", {}).setdefault("scheduled_updates", {})["enabled"] = False
            settings.setdefault("dayparts", {})["enabled"] = False; settings.setdefault("smart_programming", {})["enabled"] = False
            settings["presentation"]["sequence"] = list((event_cfg.get("sequences") or {}).get(event_type) or ["event_summary","alert","current","nws_forecast","radar_regional"])
            settings["station_slogan"] = f"AUTOMATIC {(EVENT_TYPES.get(event_type) or {}).get('name','WEATHER EVENT').upper()} COVERAGE • OFFICIAL ALERTS"
            if self.event_manager and region:
                event_status=self.event_manager.evaluate(str(region.get("id")),event_type); settings["_event"] = event_status
                if event_status.get("alerts"): snapshot["alerts"] = event_status["alerts"]
        settings["_channel_mode"] = channel_mode
        if self.spc_manager:
            settings["_spc_outlook"] = (self.spc_manager.snapshot(settings.get("_render_location_id") or settings.get("primary_location_id")).get("outlook") or {})
        if self.tropical_manager:
            loc = next((x for x in settings.get("locations", []) if x.get("id") == settings.get("_render_location_id")), None)
            settings["_tropical"] = self.tropical_manager.status(loc, snapshot.get("alerts") or [])
        context = (settings, snapshot)
        with self._context_lock:
            self._context_cache[key] = context
            if len(self._context_cache) > 96:
                self._context_cache.pop(next(iter(self._context_cache)))
        return context

    def context_cache_status(self) -> dict[str, int]:
        with self._context_lock:
            return {"entries": len(self._context_cache), "hits": self._context_hits, "misses": self._context_misses, "config_revision": self._config_revision, "weather_revision": self._weather_revision}

    def takeover_alert_for(self, location_id: str | None = None) -> dict[str, Any] | None:
        settings, snapshot = self._channel_context(location_id, "local")
        return self._takeover_alert(settings, snapshot)

    def narration_context_for(self, location_id: str | None = None, channel_mode: str = "local", now: float | None = None) -> dict[str, Any]:
        """Return lightweight state used by the optional broadcast narrator.

        Narration is deliberately constrained to Local on the 8s blocks and
        qualifying severe alerts. Radar/normal rotation screens never trigger it.
        """
        now = now or dt.datetime.now().timestamp()
        settings, snapshot = self._channel_context(location_id, channel_mode)
        primary = self._primary(settings, snapshot) or {}
        scheduled_elapsed = self._scheduled_update_elapsed(settings, snapshot, now) if channel_mode == "local" else None
        local_dt = self._local_datetime(snapshot, settings, now)
        block_id = None
        if scheduled_elapsed is not None:
            started = local_dt - dt.timedelta(seconds=scheduled_elapsed)
            block_id = started.strftime("%Y%m%d-%H%M")
        return {
            "settings": settings,
            "snapshot": snapshot,
            "primary": primary,
            "scheduled_update_active": scheduled_elapsed is not None,
            "scheduled_block_id": block_id,
            "takeover_alert": self._takeover_alert(settings, snapshot),
        }

    def render_channel(self, now: float | None = None, location_id: str | None = None, channel_mode: str = "local", runtime_overrides: dict[str, Any] | None = None) -> Image.Image:
        now = now or dt.datetime.now().timestamp()
        base_settings, snapshot = self._channel_context(location_id, channel_mode)
        overrides = runtime_overrides or {}
        settings = base_settings
        if overrides:
            settings = dict(base_settings)
            presentation = dict(base_settings.get("presentation") or {})
            presentation["retro_effects"] = dict(presentation.get("retro_effects") or {})
            settings["presentation"] = presentation
        if overrides.get("theme"):
            settings["theme"] = overrides["theme"]
        if overrides.get("branding_profile"):
            apply_region_identity(settings, settings.get("_region"), str(overrides["branding_profile"]))
        if "transition" in overrides:
            settings.setdefault("presentation", {})["transition"] = overrides["transition"]
        if "retro_enabled" in overrides:
            settings.setdefault("presentation", {}).setdefault("retro_effects", {})["enabled"] = bool(overrides["retro_enabled"])
        if overrides.get("performance_degraded"):
            perf = settings.get("performance") or {}
            if perf.get("adaptive_disable_retro", True):
                settings.setdefault("presentation", {}).setdefault("retro_effects", {})["enabled"] = False
            settings.setdefault("presentation", {})["transition"] = perf.get("adaptive_transition", "cut")
        if overrides.get("local8_active"):
            settings["_local8_active"] = True
            settings["_local8_phase"] = str(overrides.get("local8_phase") or "")
        w = int(settings["video"].get("width", 1280)); h = int(settings["video"].get("height", 720))
        c = self._theme(settings)
        primary = self._primary(settings, snapshot)
        if not primary:
            dw, dh = self._design_dimensions(settings)
            out = Image.new("RGB", (dw, dh), c["bg"]); self._paint_background(out, c, settings, now)
            draw = ImageDraw.Draw(out); self._draw_setup(draw, dw, dh, settings, c); self._draw_footer(draw, dw, dh, settings, snapshot, c, now)
            return self._apply_retro_effects(self._scale_frame(out, settings), settings, now)

        forced_name = str(overrides.get("force_slide") or "").strip()
        if forced_name:
            # Dedicated programming blocks supply their exact phase. Deliberately
            # bypass the normal timeline/transitions so audio and picture cannot
            # drift onto different phases while Piper is speaking.
            progress = max(0.0, min(1.0, float(overrides.get("force_progress", 0.0))))
            return self._apply_retro_effects(self._render_slide(forced_name, settings, snapshot, primary, now, progress), settings, now)

        seq, idx, name, progress, elapsed, duration = self._timeline(settings, snapshot, now)
        current = self._render_slide(name, settings, snapshot, primary, now, progress)
        pres = settings.get("presentation", {})
        desk = event_identity(settings) or {}
        urgent = bool(self._takeover_alert(settings, snapshot))
        kind = resolved_transition(settings, str(desk.get("key") or "") or None)
        transition = min(motion_transition_seconds(settings, urgent=urgent), duration / 3)
        # A short entry settle gives static forecast boards some motion even after
        # the transition has completed. Emergency takeovers deliberately skip it.
        entry = 0.0 if urgent else min(motion_entry_seconds(settings), duration / 4)
        if entry > 0 and elapsed < entry:
            current = self._apply_entry_motion(current, max(0.0, min(1.0, elapsed / entry)), motion_entry_style(settings, str(desk.get("key") or "") or None), settings)
        out = current
        if kind != "cut" and transition > 0:
            if elapsed < transition:
                prev_name, _ = seq[(idx-1) % len(seq)]
                prev = self._render_slide(prev_name, settings, snapshot, primary, now, 1.0)
                alpha = max(0.0, min(1.0, elapsed / transition))
                out = self._transition(prev, current, alpha, kind, settings=settings)
            elif duration - elapsed < transition:
                next_name, _ = seq[(idx+1) % len(seq)]
                nxt = self._render_slide(next_name, settings, snapshot, primary, now, 0.0)
                alpha = max(0.0, min(1.0, (transition - (duration-elapsed)) / transition))
                out = self._transition(current, nxt, alpha, kind, settings=settings)
        return self._apply_retro_effects(out, settings, now)

    def render(self, now: float | None = None) -> Image.Image:
        return self.render_channel(now=now, location_id=None, channel_mode="local")

    def render_preview(self, slide_name: str, test_alert: bool = False, location_id: str | None = None, channel_mode: str = "local") -> Image.Image:
        settings, snapshot = self._channel_context(location_id, channel_mode); primary = self._primary(settings, snapshot)
        w = int(settings["video"].get("width", 1280)); h = int(settings["video"].get("height", 720)); c = self._theme(settings)
        if not primary:
            dw, dh = self._design_dimensions(settings)
            out = Image.new("RGB", (dw,dh), c["bg"]); self._paint_background(out,c,settings,dt.datetime.now().timestamp()); d=ImageDraw.Draw(out); self._draw_setup(d,dw,dh,settings,c); self._draw_footer(d,dw,dh,settings,snapshot,c,dt.datetime.now().timestamp()); return self._scale_frame(out, settings)
        valid = {"station_id","current","story_brief","condition_focus","today","nws_forecast","day_ahead","temperature_trend","hourly","humidity_outlook","wind_outlook","precipitation","rain_accumulation","storm_outlook","spc_outlook","tropical_update","tropical_systems","tropical_track","tropical_local","radar_local","radar_regional","radar_wide","seven_day","regional_map","regional","today_so_far","past_24_hours","air_quality","local_rivers","climate_context","weather_history","almanac","alert","alert_radar","event_summary","map_engine","map_satellite","map_lightning","spc_map","spc_hazards","surface_map","qpf_map","hazard_map"}
        if slide_name not in valid and not slide_name.startswith("bumper:"): slide_name = "current"
        snap = snapshot
        if test_alert:
            snap = dict(snapshot); snap["alerts"] = list(snapshot.get("alerts") or [])
            loc = primary.get("location") or {}; lat=float(loc.get("latitude",0)); lon=float(loc.get("longitude",0)); d=0.35
            snap["alerts"] = [{"id":"weatherstream-test","event":"TEST Tornado Warning","headline":"TEST MODE — NOT A REAL WEATHER ALERT","severity":"Severe","urgency":"Immediate","certainty":"Observed","areaDesc":"TEST MODE — Roller Weather Network","description":"This synthetic warning is only for testing WeatherStream graphics and alert presentation.","instruction":"No action is required. This is not a real weather warning.","expires":(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=30)).isoformat(),"geometry":{"type":"Polygon","coordinates":[[[lon-d,lat-d],[lon+d,lat-d],[lon+d,lat+d],[lon-d,lat+d],[lon-d,lat-d]]]}}]
            if slide_name not in {"alert","alert_radar"}: slide_name="alert"
        out=self._render_slide(slide_name,settings,snap,primary,dt.datetime.now().timestamp(),0.35)
        if test_alert:
            d=ImageDraw.Draw(out); sx=w/1280.0; sy=h/720.0; y1=int(88*sy); y2=int(126*sy)
            d.rectangle((0,y1,w,y2),fill="#f0c400"); d.text((w//2,int(107*sy)),"TEST MODE • NOT A REAL WEATHER ALERT",font=font(max(10,int(19*min(sx,sy))),bold=True,mono=True),fill="#111111",anchor="mm")
        return out

    def _pattern_mask(self, size: tuple[int, int], alpha: float, block: int) -> Image.Image:
        w, h = size
        bucket = int(round(max(0.0, min(1.0, alpha)) * 64))
        return _cached_pattern_mask(w, h, bucket, max(1, block))

    def _apply_entry_motion(self, frame: Image.Image, alpha: float, style: str, settings: dict[str, Any]) -> Image.Image:
        """Apply a short whole-frame settle after a transition.

        This intentionally remains subtle; weather data must stay readable and the
        animation cannot change the meaning or timing of a forecast graphic.
        """
        alpha=max(0.0,min(1.0,alpha))
        if style in {"none","cut"} or alpha >= 0.999:
            return frame
        w,h=frame.size
        bg=(0,0,0)
        if style == "snap":
            # Small scale settle for high-impact desks.
            scale=1.035-0.035*alpha
            nw,nh=max(w,int(w*scale)),max(h,int(h*scale))
            z=frame.resize((nw,nh),Image.Resampling.BICUBIC)
            left=(nw-w)//2; top=(nh-h)//2
            out=z.crop((left,top,left+w,top+h))
            return ImageEnhance.Brightness(out).enhance(0.88+0.12*alpha)
        if style == "glide":
            dx=int((1-alpha)*34)
            out=Image.new("RGB",(w,h),bg); out.paste(frame,(dx,0))
            return Image.blend(Image.new("RGB",(w,h),bg),out,0.76+0.24*alpha)
        if style == "soft":
            blur=max(0.0,(1-alpha)*2.5)
            out=frame.filter(ImageFilter.GaussianBlur(radius=blur)) if blur else frame
            return ImageEnhance.Brightness(out).enhance(0.82+0.18*alpha)
        # Default RWN broadcast settle: 18px vertical lift + quick fade.
        dy=int((1-alpha)*18); out=Image.new("RGB",(w,h),bg); out.paste(frame,(0,dy))
        return Image.blend(Image.new("RGB",(w,h),bg),out,0.82+0.18*alpha)

    def _transition(self, a: Image.Image, b: Image.Image, alpha: float, kind: str, settings: dict[str, Any] | None = None) -> Image.Image:
        alpha = max(0.0, min(1.0, alpha))
        w, h = a.size
        settings = settings or {}
        if kind == "rwn_wipe":
            # Network signature: clean two-edge wipe plus the RWN bug riding the
            # transition band. It is intentionally brief and never used for an
            # emergency takeover.
            out = a.copy(); edge = int(w * alpha)
            if edge > 0: out.paste(b.crop((0, 0, edge, h)), (0, 0))
            if 0 < edge < w:
                d = ImageDraw.Draw(out, "RGBA")
                d.rectangle((max(0, edge-18), 0, min(w, edge+8), h), fill=(12,72,150,150))
                d.rectangle((max(0, edge-5), 0, min(w, edge+5), h), fill=(230,248,255,235))
                try:
                    logo=self._load_logo(settings)
                    if logo is not None:
                        lw=132; lh=max(1,int(logo.height*(lw/logo.width)))
                        bug=logo.resize((lw,lh),Image.Resampling.LANCZOS)
                        bx=max(-lw//2,min(w-lw//2,edge-lw//2)); by=max(12,(h-lh)//2)
                        out.paste(bug,(bx,by),bug)
                except Exception:
                    pass
            return out
        if kind == "panel_push":
            edge = int(w * alpha); out = Image.new("RGB", (w, h), "black")
            if edge < w: out.paste(a.crop((edge,0,w,h)), (0,0))
            if edge > 0: out.paste(b.crop((0,0,edge,h)), (w-edge,0))
            d=ImageDraw.Draw(out,"RGBA")
            x=max(0,min(w-1,w-edge)); d.rectangle((max(0,x-10),0,min(w,x+10),h),fill=(255,255,255,36))
            return out
        if kind == "angular_wipe":
            # Severe: fast diagonal warning wedge.
            edge=int((w+220)*alpha)-220
            mask=Image.new("L",(w,h),0); md=ImageDraw.Draw(mask)
            md.polygon([(0,0),(edge,0),(edge-180,h),(0,h)],fill=255)
            out=Image.composite(b,a,mask); d=ImageDraw.Draw(out,"RGBA")
            if -180 < edge < w+180:
                d.polygon([(edge-16,0),(edge+10,0),(edge-170,h),(edge-196,h)],fill=(255,49,61,210))
                d.polygon([(edge+10,0),(edge+20,0),(edge-160,h),(edge-170,h)],fill=(255,157,31,210))
            return out
        if kind == "waterline_wipe":
            # Flood: horizontal waterline with a moving sinusoidal edge. The mask
            # is a polygon rather than a per-pixel loop so it remains cheap at 1080p.
            mask=Image.new("L",(w,h),0); md=ImageDraw.Draw(mask); base=int(w*alpha)
            pts=[(max(0,min(w,base+int(20*math.sin(y/34.0+alpha*8.0)))),y) for y in range(0,h+8,8)]
            poly=[(0,0),*pts,(0,h)]
            md.polygon(poly,fill=255)
            out=Image.composite(b,a,mask); d=ImageDraw.Draw(out,"RGBA")
            if len(pts)>1: d.line(pts,fill=(34,200,239,220),width=5)
            return out
        if kind == "ice_shards":
            # Winter: staggered crystalline diagonal panes.
            out=a.copy(); shards=9; sw=max(1,w//shards)
            for i in range(shards):
                local=max(0.0,min(1.0,alpha*1.55-i*0.055)); reveal=int(h*local)
                if reveal<=0: continue
                x0=i*sw; x1=w if i==shards-1 else min(w,x0+sw+3)
                out.paste(b.crop((x0,0,x1,reveal)),(x0,0))
                if reveal<h: ImageDraw.Draw(out,"RGBA").line((x0,reveal,x1,reveal),fill=(201,242,255,150),width=3)
            return out
        if kind == "heatwave":
            # Heat: crossfade plus restrained horizontal shimmer; no psychedelic distortion.
            base=Image.blend(a,b,alpha); amp=int(9*math.sin(math.pi*alpha))
            if amp<=0: return base
            out=Image.new("RGB",(w,h))
            band=10
            for y in range(0,h,band):
                y1=min(h,y+band); dx=int(math.sin(y/25.0+alpha*9.0)*amp)
                row=base.crop((0,y,w,y1)); shifted=Image.new("RGB",(w,y1-y),(42,13,7)); shifted.paste(row,(dx,0)); out.paste(shifted,(0,y))
            return out
        if kind == "smoke_dissolve":
            # Wildfire: soft deterministic dissolve using blurred noise.
            bucket=max(0,min(64,int(round(alpha*64))))
            mask=_cached_pattern_mask(w,h,bucket,28).filter(ImageFilter.GaussianBlur(radius=12))
            return Image.composite(b,a,mask)
        if kind == "radar_sweep":
            # Tropical: radial sweep rotating clockwise around the screen center.
            mask=Image.new("L",(w,h),0); md=ImageDraw.Draw(mask); cx,cy=w//2,h//2; r=int(math.hypot(w,h))
            start=-90; end=start+alpha*360
            md.pieslice((cx-r,cy-r,cx+r,cy+r),start=start,end=end,fill=255)
            out=Image.composite(b,a,mask)
            if 0.01 < alpha < .99:
                ang=math.radians(end); ex=cx+int(math.cos(ang)*r); ey=cy+int(math.sin(ang)*r)
                ImageDraw.Draw(out,"RGBA").line((cx,cy,ex,ey),fill=(33,199,255,190),width=5)
            return out
        if kind == "wipe":
            out = a.copy(); edge = int(w * alpha)
            if edge > 0: out.paste(b.crop((0, 0, edge, h)), (0, 0))
            d = ImageDraw.Draw(out)
            if 0 < edge < w: d.rectangle((max(0, edge-4), 0, min(w, edge+4), h), fill="#d9f3ff")
            return out
        if kind == "wipe_vertical":
            out = a.copy(); edge = int(h * alpha)
            if edge > 0: out.paste(b.crop((0, 0, w, edge)), (0, 0))
            d = ImageDraw.Draw(out)
            if 0 < edge < h: d.rectangle((0, max(0, edge-4), w, min(h, edge+4)), fill="#d9f3ff")
            return out
        if kind == "slide_left":
            edge = int(w * alpha)
            out = Image.new("RGB", (w, h), "black")
            if edge < w: out.paste(a.crop((edge, 0, w, h)), (0, 0))
            if edge > 0: out.paste(b.crop((0, 0, edge, h)), (w-edge, 0))
            return out
        if kind == "slide_up":
            edge = int(h * alpha)
            out = Image.new("RGB", (w, h), "black")
            if edge < h: out.paste(a.crop((0, edge, w, h)), (0, 0))
            if edge > 0: out.paste(b.crop((0, 0, w, edge)), (0, h-edge))
            return out
        if kind == "venetian":
            out = a.copy()
            bands = 12
            band_h = max(1, h // bands)
            reveal = int(w * alpha)
            for band in range(bands):
                y0 = band * band_h
                y1 = h if band == bands-1 else min(h, y0 + band_h)
                if band % 2 == 0:
                    box = (0, y0, reveal, y1); dest = (0, y0)
                else:
                    box = (w-reveal, y0, w, y1); dest = (w-reveal, y0)
                if reveal > 0:
                    out.paste(b.crop(box), dest)
            return out
        if kind == "dissolve":
            return Image.composite(b, a, self._pattern_mask((w, h), alpha, 4))
        if kind == "pixel_dissolve":
            return Image.composite(b, a, self._pattern_mask((w, h), alpha, 20))
        if kind == "crt_fade":
            out = Image.new("RGB", (w, h), (0, 0, 0))
            if alpha < 0.5:
                q = max(0.015, 1.0 - alpha * 1.97)
                sh = max(2, int(h * q))
                frame = a.resize((w, sh), Image.Resampling.BILINEAR)
                frame = ImageEnhance.Brightness(frame).enhance(max(0.2, q))
            else:
                q = max(0.015, (alpha - 0.5) * 1.97)
                sh = max(2, int(h * q))
                frame = b.resize((w, sh), Image.Resampling.BILINEAR)
                frame = ImageEnhance.Brightness(frame).enhance(max(0.2, q))
            out.paste(frame, (0, (h-sh)//2))
            if sh < 12:
                ImageDraw.Draw(out).line((0, h//2, w, h//2), fill="#e5f7ff", width=2)
            return out
        return Image.blend(a, b, alpha)

    def _apply_retro_effects(self, img: Image.Image, settings: dict[str, Any], now: float) -> Image.Image:
        effects = ((settings.get("presentation", {}) or {}).get("retro_effects") or {})
        if not effects.get("enabled", False):
            return img
        out = img.convert("RGB")
        w, h = out.size

        jitter = max(0, int(effects.get("horizontal_jitter_px", 0)))
        if jitter:
            dx = int(round(math.sin(now * 19.0) * jitter))
            if dx:
                shifted = Image.new("RGB", (w, h), (0, 0, 0))
                if dx > 0: shifted.paste(out.crop((0, 0, w-dx, h)), (dx, 0))
                else: shifted.paste(out.crop((-dx, 0, w, h)), (0, 0))
                out = shifted

        bleed = max(0, int(effects.get("color_bleed_px", 0)))
        if bleed:
            r, g, b = out.split()
            r = ImageChops.offset(r, bleed, 0)
            b = ImageChops.offset(b, -bleed, 0)
            out = Image.merge("RGB", (r, g, b))

        bloom = max(0.0, min(0.5, float(effects.get("bloom", 0.0))))
        if bloom > 0:
            blur = out.filter(ImageFilter.GaussianBlur(radius=2.0))
            screened = ImageChops.screen(out, blur)
            out = Image.blend(out, screened, bloom)

        noise_strength = max(0.0, min(0.25, float(effects.get("noise", 0.0))))
        if noise_strength > 0:
            small = Image.effect_noise((max(1, w//3), max(1, h//3)), 28).resize((w, h), Image.Resampling.NEAREST).convert("RGB")
            out = Image.blend(out, small, noise_strength)

        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        scan = max(0.0, min(0.65, float(effects.get("scanlines", 0.0))))
        if scan > 0:
            alpha_line = int(255 * scan)
            for y in range(1, h, 3):
                od.line((0, y, w, y), fill=(0, 0, 0, alpha_line), width=1)

        soft = max(0.0, min(0.60, float(effects.get("soft_edges", 0.0))))
        if soft > 0:
            steps = 20
            max_edge = int(min(w, h) * 0.08)
            for i in range(steps):
                inset = int(i * max_edge / steps)
                a = int(150 * soft * (1.0 - i/steps) ** 2)
                od.rectangle((inset, inset, w-1-inset, h-1-inset), outline=(0, 0, 0, a), width=max(1, max_edge//steps))
        return Image.alpha_composite(out.convert("RGBA"), overlay).convert("RGB")

    def _header(self, draw, w, title, subtitle, c):
        style = c.get("style", "classic")
        if style == "event":
            # Dedicated desk package: accent cap, compact RWN desk bug, strong title
            # and a secondary accent that differentiates each event identity.
            draw.rectangle((0,0,w,88),fill=c["panel2"])
            draw.rectangle((0,0,w,7),fill=c["accent"])
            draw.rectangle((20,17,116,70),fill=c["accent"])
            draw.text((68,43),"RWN",font=font(23,bold=True),fill="#ffffff",anchor="mm")
            draw.text((134,13),str(c.get("event_desk") or "RWN EVENT DESK"),font=font(12,bold=True,mono=True),fill=c.get("accent2",c["accent"]))
            draw.text((134,31),title.upper(),font=font(35,bold=True),fill=c["title"])
            draw.text((w-28,31),subtitle.upper(),font=font(16,bold=True,mono=True),fill=c["muted"],anchor="ra")
            draw.text((w-28,57),str(c.get("event_slug") or "DEDICATED WEATHER COVERAGE"),font=font(11,bold=True,mono=True),fill=c.get("accent2",c["accent"]),anchor="ra")
            draw.line((0,86,w,86),fill=c.get("accent2",c["accent"]),width=2)
        elif style == "terminal80":
            draw.rectangle((0, 0, w, 88), fill=c["panel2"])
            draw.rectangle((0, 0, 18, 88), fill=c["accent"])
            draw.line((18, 86, w, 86), fill=c["title"], width=2)
            draw.text((38, 17), title.upper(), font=font(37, bold=True, mono=True), fill=c["title"])
            draw.text((w-30, 29), subtitle.upper(), font=font(19, bold=True, mono=True), fill=c["muted"], anchor="ra")
        elif style == "cablegold":
            draw.rectangle((0, 0, w, 88), fill=c["panel2"])
            draw.rectangle((0, 0, w, 7), fill=c["accent"])
            draw.rectangle((25, 20, 43, 69), fill=c["accent"])
            draw.text((62, 17), title.upper(), font=font(39, bold=True), fill=c["title"])
            draw.text((w-34, 28), subtitle, font=font(21, bold=True, mono=True), fill=c["muted"], anchor="ra")
            draw.line((0, 87, w, 87), fill="#ffffff", width=1)
        else:
            draw.rectangle((0, 0, w, 88), fill=c["panel2"])
            title_font = font(39, bold=True, mono=(style == "local90"))
            draw.text((34, 15), title.upper(), font=title_font, fill=c["title"])
            draw.text((w-34, 28), subtitle, font=font(22, bold=True, mono=True), fill=c["muted"], anchor="ra")
            draw.line((0, 86, w, 86), fill=c["accent"], width=2)

    def _draw_local8_intro(self, draw, w, h, settings, p, c):
        """Dedicated Local on the 8s opening card.

        Keep the card intentionally sparse so the phase narration can be derived
        directly from the visible text without inventing off-screen weather copy.
        """
        loc = p.get("location") or {}
        station = settings.get("station_name", "Roller Weather Network")
        place = location_label(loc)
        draw.rectangle((0, 0, w, 16), fill=c["accent"])
        draw.text((w//2, 142), "LOCAL ON THE 8s", font=font(66, bold=True), fill=c["title"], anchor="mm")
        draw.line((250, 198, w-250, 198), fill=c["accent"], width=3)
        draw.text((w//2, 264), place.upper(), font=font(38, bold=True, mono=True), fill=c["accent"], anchor="mm")
        draw.text((w//2, 356), station.upper(), font=font(32, bold=True), fill=c["text"], anchor="mm")
        draw.text((w//2, 430), "YOUR LOCAL FORECAST STARTS NOW", font=font(23, bold=True, mono=True), fill=c["muted"], anchor="mm")

    def _draw_station_id(self, draw, w, h, settings, p, c, now):
        loc = p.get("location", {})
        cur = p.get("current", {})
        station = settings.get("station_name", "Roller Weather Network")
        callsign = (settings.get("station_callsign") or "").strip().upper()
        slogan = (settings.get("station_slogan") or "Local Weather • Radar • Alerts • 24 Hours").strip()
        service = (settings.get("service_area") or location_label(loc)).strip()
        variant = int(now // max(3, int(settings.get("slides", {}).get("station_id", 6)))) % 3
        draw.rectangle((0, 0, w, 16), fill=c["accent"])
        brand_shift = 78 if (settings.get("branding") or {}).get("logo_enabled", False) else 0
        cx = w//2 + brand_shift
        if callsign:
            draw.text((cx, 118), callsign, font=font(36, bold=True, mono=True), fill=c["accent"], anchor="mm")
        draw.text((cx, 175 if callsign else 154), station.upper(), font=font(52 if brand_shift else 56, bold=True), fill=c["title"], anchor="mm")
        strap = ["LOCAL WEATHER • 24 HOURS A DAY", slogan.upper(), f"SERVING {service.upper()}"][variant]
        draw.text((cx, 238 if callsign else 220), strap[:62], font=font(22, bold=True, mono=True), fill=c["accent"], anchor="mm")
        round_rect(draw, (290, 292, w-290, 500), 24, c["panel"], outline=c["muted"], width=2)
        draw_weather_icon(draw, cur.get("weather_code"), 350, 308, 0.85, c, icon_cfg=settings.get("icon_system"))
        draw.text((615, 345), location_label(loc), font=font(31, bold=True), fill=c["text"])
        draw.text((615, 402), f"{n(cur.get('temperature_2m'),0,'°')}  {cur.get('description','')}", font=font(39, bold=True), fill=c["text"])
        date_text = dt.datetime.fromtimestamp(now).strftime("%A %B %d • %I:%M %p").upper().replace(" 0", " ")
        draw.text((w//2, 552), date_text, font=font(22, bold=True, mono=True), fill=c["muted"], anchor="mm")

    def _load_logo(self, settings):
        cfg = settings.get("branding") or {}
        profile_id = str(settings.get("_branding_profile_id") or "default")
        profile_logo = CONFIG_DIR / "branding" / "profiles" / f"{profile_id}.png"
        path = profile_logo if profile_id != "default" and profile_logo.exists() else BRANDING_LOGO if BRANDING_LOGO.exists() else (BUILTIN_RWN_LOGO if cfg.get("use_builtin_logo", True) else BRANDING_LOGO)
        try:
            mtime = path.stat().st_mtime
            cache_key = (str(path), mtime)
            if self._logo_cache is None or self._logo_mtime != cache_key:
                self._logo_cache = Image.open(path).convert("RGBA")
                self._logo_mtime = cache_key
                self._logo_scaled_cache.clear()
            return self._logo_cache
        except Exception:
            self._logo_cache = None; self._logo_mtime = None
            return None

    def _draw_branding_logo(self, img, settings, slide_name):
        cfg = settings.get("branding") or {}
        if not cfg.get("logo_enabled", False):
            return
        position = cfg.get("logo_position", "station_id_only")
        if position == "station_id_only" and slide_name != "station_id":
            return
        logo = self._load_logo(settings)
        if logo is None:
            return
        max_w = max(64, min(320, int(cfg.get("logo_max_width", 170))))
        max_h = 92 if slide_name != "station_id" else 150
        scaled_key = (self._logo_mtime, max_w, max_h)
        scaled = self._logo_scaled_cache.get(scaled_key)
        if scaled is None:
            scale = min(max_w / max(1, logo.width), max_h / max(1, logo.height), 1.0)
            scaled = logo.resize((max(1, int(logo.width*scale)), max(1, int(logo.height*scale))), Image.Resampling.LANCZOS)
            self._logo_scaled_cache[scaled_key] = scaled
        logo = scaled
        if slide_name == "station_id":
            x, y = 48, 96
        elif position == "top_left":
            x, y = 24, 96
        else:
            x, y = img.width - logo.width - 24, 96
        img.paste(logo, (x, y), logo)

    def _hourly_window(self, p, count=12):
        hourly = p.get("hourly", {})
        times = hourly.get("time") or []
        if not times:
            return [], []
        now = dt.datetime.now()
        idx = 0
        for i, raw in enumerate(times):
            try:
                if dt.datetime.fromisoformat(raw) >= now.replace(tzinfo=None):
                    idx = i; break
            except Exception:
                pass
        return list(range(idx, min(len(times), idx+count))), times

    def _draw_temperature_trend(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Temperature Trend", location_label(p["location"]), c)
        hourly = p.get("hourly", {})
        idxs, times = self._hourly_window(p, 12)
        temps = hourly.get("temperature_2m") or []
        feels = hourly.get("apparent_temperature") or []
        values = [float(temps[i]) for i in idxs if i < len(temps) and temps[i] is not None]
        if not values:
            draw.text((w//2, 340), "HOURLY TEMPERATURE DATA UNAVAILABLE", font=font(34, bold=True), fill=c["accent"], anchor="mm")
            return
        x1, y1, x2, y2 = 92, 154, w-72, 535
        round_rect(draw, (x1, y1, x2, y2), 14, c["panel2"], outline=c["muted"], width=2)
        lo = math.floor((min(values)-4)/5)*5; hi = math.ceil((max(values)+4)/5)*5
        if hi <= lo: hi = lo + 10
        for step in range(5):
            yy = y2-42 - step*((y2-y1-82)/4)
            val = lo + step*(hi-lo)/4
            draw.line((x1+72, yy, x2-28, yy), fill=c["panel"], width=1)
            draw.text((x1+54, yy), f"{val:.0f}°", font=font(14, bold=True, mono=True), fill=c["muted"], anchor="rm")
        points=[]; feel_points=[]
        for pos,i in enumerate(idxs):
            if i >= len(temps) or temps[i] is None: continue
            xx = x1+86 + pos*(x2-x1-132)/max(1,len(idxs)-1)
            yy = y2-42 - (float(temps[i])-lo)/(hi-lo)*(y2-y1-82)
            points.append((xx,yy))
            if i < len(feels) and feels[i] is not None:
                fy = y2-42 - (float(feels[i])-lo)/(hi-lo)*(y2-y1-82)
                feel_points.append((xx,fy))
            if pos % 2 == 0:
                try: label=dt.datetime.fromisoformat(times[i]).strftime("%-I %p")
                except Exception: label=times[i][-5:]
                draw.text((xx, y2-24), label, font=font(13, bold=True, mono=True), fill=c["muted"], anchor="mm")
        if len(feel_points)>1: draw.line(feel_points, fill=c["muted"], width=3, joint="curve")
        if len(points)>1: draw.line(points, fill=c["accent"], width=5, joint="curve")
        for xx,yy in points: draw.ellipse((xx-5,yy-5,xx+5,yy+5), fill=c["accent"], outline=c["text"], width=1)
        draw.text((100, 575), "AIR TEMPERATURE", font=font(16, bold=True, mono=True), fill=c["accent"])
        draw.text((330, 575), "FEELS LIKE", font=font(16, bold=True, mono=True), fill=c["muted"])

    def _draw_storm_outlook(self, draw, w, h, settings, snapshot, p, c):
        self._header(draw, w, "Storm Potential", location_label(p["location"]), c)
        guidance = snapshot.get("storm_guidance") or {}
        hourly = guidance.get("hourly") or {}
        times = hourly.get("time") or []
        probs = hourly.get("thunderstorm_probability") or []
        capes = hourly.get("cape") or []
        if not times:
            # Fallback to generic model CAPE already carried with the normal forecast.
            hourly = p.get("hourly", {})
            times = hourly.get("time") or []
            probs = []
            capes = hourly.get("cape") or []
        if not times:
            draw.text((w//2, 330), "MODEL STORM GUIDANCE UNAVAILABLE", font=font(36, bold=True), fill=c["accent"], anchor="mm")
            if guidance.get("error"):
                draw.text((w//2, 390), "WeatherStream will retry automatically.", font=font(20), fill=c["muted"], anchor="mm")
            return
        idxs,_ = self._hourly_window({"hourly":hourly}, 10)
        x1,y1,x2,y2=70,145,w-70,535
        round_rect(draw,(x1,y1,x2,y2),14,c["panel2"],outline=c["muted"],width=2)
        max_cape=max([float(capes[i] or 0) for i in idxs if i < len(capes)] or [1000])
        max_cape=max(1000,max_cape)
        bw=(x2-x1-100)/max(1,len(idxs))
        for pos,i in enumerate(idxs):
            prob=float(probs[i] or 0) if i < len(probs) and probs[i] is not None else 0
            cape=float(capes[i] or 0) if i < len(capes) and capes[i] is not None else 0
            x=x1+65+pos*bw
            bar_h=(prob/100)*(y2-y1-110)
            draw.rectangle((x,y2-55-bar_h,x+bw*0.56,y2-55),fill=c["accent"])
            cape_h=(cape/max_cape)*(y2-y1-110)
            draw.rectangle((x+bw*0.60,y2-55-cape_h,x+bw*0.82,y2-55),fill=c["muted"])
            try: label=dt.datetime.fromisoformat(times[i]).strftime("%-I%p")
            except Exception: label=times[i][-5:]
            draw.text((x+bw*0.4,y2-34),label,font=font(11,bold=True,mono=True),fill=c["text"],anchor="mm")
            if prob>0: draw.text((x+bw*0.28,y2-65-bar_h),f"{prob:.0f}%",font=font(12,bold=True,mono=True),fill=c["text"],anchor="ms")
        peak_prob=max([float(probs[i] or 0) for i in idxs if i < len(probs)] or [0])
        peak_cape=max([float(capes[i] or 0) for i in idxs if i < len(capes)] or [0])
        draw.text((88,565),f"PEAK THUNDERSTORM PROBABILITY  {peak_prob:.0f}%",font=font(18,bold=True,mono=True),fill=c["accent"])
        draw.text((640,565),f"PEAK CAPE  {peak_cape:.0f} J/kg",font=font(18,bold=True,mono=True),fill=c["muted"])
        draw.text((w//2,603),"MODEL GUIDANCE • NOT OBSERVED LIGHTNING • NWS ALERTS REMAIN THE WARNING SOURCE",font=font(13,bold=True,mono=True),fill=c["text"],anchor="mm")

    def _project_to_map(self, lat, lon, center_lat, center_lon, zoom, map_box):
        source_w, source_h = 1180.0, 500.0
        cx, cy = latlon_to_world(float(center_lat), float(center_lon), int(zoom))
        left = cx*256.0-source_w/2; top=cy*256.0-source_h/2
        wx,wy=latlon_to_world(float(lat),float(lon),int(zoom))
        x1,y1,x2,y2=map_box
        return (x1+(wx*256.0-left)*(x2-x1)/source_w, y1+(wy*256.0-top)*(y2-y1)/source_h)

    def _draw_auto_city_labels(self, draw, map_box, p, zoom, settings, c, compact=False, reserved=None):
        if not self.place_manager:
            return
        cfg=settings.get("maps") or {}
        loc=p.get("location") or {}
        try: lat=float(loc["latitude"]); lon=float(loc["longitude"])
        except Exception: return
        cities=self.place_manager.nearby(lat,lon,float(cfg.get("city_radius_miles",180)),int(cfg.get("city_max_labels",10)),int(cfg.get("city_min_population",5000)))
        placed=list(reserved or []); x1,y1,x2,y2=map_box
        configured={str(x.get("name","")).lower() for x in settings.get("locations",[])}
        for city in cities:
            if str(city.get("name","")).lower() in configured: continue
            try: x,y=self._project_to_map(city["latitude"],city["longitude"],lat,lon,zoom,map_box)
            except Exception: continue
            if not (x1+22<x<x2-22 and y1+24<y<y2-24): continue
            size=11 if compact else 14
            label=str(city.get("name") or "").upper()
            f=font(size,bold=True,mono=True)
            box=draw.textbbox((x+7,y-7),label,font=f,stroke_width=2)
            if any(not(box[2]<b[0] or box[0]>b[2] or box[3]<b[1] or box[1]>b[3]) for b in placed): continue
            placed.append(box)
            draw.ellipse((x-3,y-3,x+3,y+3),fill=c["accent"],outline="#081520")
            draw.text((x+7,y-7),label,font=f,fill="#ffffff",stroke_width=2,stroke_fill="#102030")

    def _draw_regional_map(self, img, draw, w, h, settings, snapshot, p, c):
        cfg=settings.get("maps") or {}
        view=cfg.get("regional_map_view","regional")
        self._header(draw,w,"Regional Weather Map",location_label(p["location"]),c)
        map_box=(54,112,w-54,h-108)
        round_rect(draw,(map_box[0]-7,map_box[1]-7,map_box[2]+7,map_box[3]+7),12,c["panel2"],outline=c["muted"],width=2)
        target_w, target_h = map_box[2]-map_box[0], map_box[3]-map_box[1]
        location_id=str((p.get("location") or {}).get("id") or settings.get("_render_location_id") or "")
        base=self.radar_manager.resized_map(view, target_w, target_h, location_id=location_id) if self.radar_manager and hasattr(self.radar_manager, "resized_map") else self.radar_manager.map_snapshot(view, copy_image=False, location_id=location_id) if self.radar_manager else None
        if base is None:
            draw.text((w//2,330),"REGIONAL MAP IS LOADING",font=font(36,bold=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,385),"It will appear after the first map/radar refresh.",font=font(20),fill=c["muted"],anchor="mm")
            return
        if base.size != (target_w, target_h):
            base=base.resize((target_w,target_h),Image.Resampling.LANCZOS)
        img.paste(base,(map_box[0],map_box[1])); draw=ImageDraw.Draw(img)
        zoom=int(((settings.get("radar") or {}).get("views") or {}).get(view,{}).get("zoom",6))
        center=p.get("location") or {}
        reserved=[]
        location_items=list(snapshot.get("locations",{}).values())
        location_items.sort(key=lambda item: 0 if (item.get("location") or {}).get("postal_code")==center.get("postal_code") else 1)
        for item in location_items:
            loc=item.get("location") or {}; cur=item.get("current") or {}
            try: x,y=self._project_to_map(loc["latitude"],loc["longitude"],center["latitude"],center["longitude"],zoom,map_box)
            except Exception: continue
            if not (map_box[0]+30<x<map_box[2]-30 and map_box[1]+30<y<map_box[3]-30): continue
            is_primary=loc.get("postal_code")==center.get("postal_code")
            r=8 if is_primary else 6
            draw.ellipse((x-r,y-r,x+r,y+r),fill="#ffffff" if is_primary else c["accent"],outline="#102030",width=2)
            label=f"{str(loc.get('name','')).upper()}  {n(cur.get('temperature_2m'),0,'°')}"
            lf=font(15 if is_primary else 13,bold=True,mono=True)
            text_box=draw.textbbox((0,0),label,font=lf,stroke_width=3)
            tw=text_box[2]-text_box[0]; th=text_box[3]-text_box[1]
            candidates=[(x+12,y-10),(x+12,y+9),(x-tw-12,y-10),(x-tw-12,y+9)]
            chosen=None
            for tx,ty in candidates:
                box=(tx,ty,tx+tw,ty+th)
                if box[0]<map_box[0]+4 or box[2]>map_box[2]-4 or box[1]<map_box[1]+4 or box[3]>map_box[3]-4:
                    continue
                if any(not(box[2]<b[0] or box[0]>b[2] or box[3]<b[1] or box[1]>b[3]) for b in reserved):
                    continue
                chosen=(tx,ty,box); break
            if chosen is None:
                tx,ty=x+12,y-10; chosen=(tx,ty,(tx,ty,tx+tw,ty+th))
            tx,ty,box=chosen
            reserved.append(box)
            draw.text((tx,ty),label,font=lf,fill="#ffffff",stroke_width=3,stroke_fill="#102030")
        self._draw_auto_city_labels(draw,map_box,p,zoom,settings,c,compact=False,reserved=reserved)
        draw.rectangle((map_box[0]+14,map_box[3]-37,map_box[0]+500,map_box[3]-10),fill=(8,20,34))
        draw.text((map_box[0]+25,map_box[3]-31),"CURRENT TEMPERATURES • AUTOMATIC CITY CONTEXT",font=font(13,bold=True,mono=True),fill="#ffffff")
        draw.text((w-58,h-101),"Map © OpenStreetMap • Boundaries U.S. Census • Cities GeoNames CC BY 4.0",font=font(11,mono=True),fill=c["muted"],anchor="ra")

    def _draw_setup(self, draw, w, h, settings, c):
        self._header(draw, w, settings.get("station_name", "Roller Weather Network"), "SETUP REQUIRED", c)
        round_rect(draw, (160, 175, w-160, 545), 18, c["panel"])
        draw.text((w//2, 260), "ADD A ZIP CODE", font=font(58, bold=True), fill=c["accent"], anchor="mm")
        draw.text((w//2, 348), "Open the WeatherStream admin page", font=font(30), fill=c["text"], anchor="mm")
        draw.text((w//2, 402), "and add at least one U.S. ZIP code.", font=font(30), fill=c["text"], anchor="mm")
        draw.text((w//2, 482), "http://SERVER-IP:8787/admin", font=font(30, bold=True, mono=True), fill=c["muted"], anchor="mm")

    def _draw_story_brief(self, draw, w, h, settings, snapshot, p, c, now):
        loc = p.get("location") or {}
        story = self._weather_story(settings, snapshot, p, now)
        title = str(story.get("title") or "Local Weather Story").upper()
        reason = str(story.get("reason") or "Current local conditions and forecast")
        self._header(draw, w, "The Weather Story", location_label(loc), c)
        # Large identity panel
        round_rect(draw, (72, 142, w-72, 360), 18, c["panel2"], outline=c["accent"], width=3)
        draw.text((104, 182), "RWN WEATHER STORY", font=font(17, bold=True, mono=True), fill=c["muted"])
        size = 49 if len(title) <= 22 else 39
        draw.text((104, 224), title, font=font(size, bold=True), fill=c["accent"])
        wrapped = textwrap.wrap(reason, width=62)[:2]
        for i, line in enumerate(wrapped):
            draw.text((108, 294+i*28), line, font=font(19, bold=(i==0)), fill=c["text"])

        # Signal cards: concise evidence behind the director's selection.
        signals = [str(x) for x in (story.get("signals") or []) if str(x).strip()][:3]
        metrics = story.get("metrics") or {}; sid=str(story.get("id") or "quiet")
        extras=[]
        if sid in {"rain","storms","severe","flood"}:
            extras=[f"Peak rain chance {metrics.get('rain_probability_12h',0):.0f}%",f"Peak gust {metrics.get('peak_gust_mph',0):.0f} mph"]
        elif sid == "heat": extras=[f"Peak heat {metrics.get('peak_heat_f') or 0:.0f}°",f"AQI {metrics.get('aqi') or 0:.0f}"]
        elif sid == "wind": extras=[f"Peak gust {metrics.get('peak_gust_mph',0):.0f} mph",f"Rain chance {metrics.get('rain_probability_12h',0):.0f}%"]
        elif sid == "air_quality": extras=[f"AQI {metrics.get('aqi') or 0:.0f}",f"Peak gust {metrics.get('peak_gust_mph',0):.0f} mph"]
        elif sid in {"cold","winter"}: extras=[f"Low next 24h {metrics.get('low_24h_f') or 0:.0f}°",f"Peak gust {metrics.get('peak_gust_mph',0):.0f} mph"]
        for extra in extras:
            if len(signals)>=3: break
            if extra not in signals: signals.append(extra)
        if not signals:
            signals = [f"Rain next 12h {metrics.get('rain_probability_12h',0):.0f}%",f"Peak gust {metrics.get('peak_gust_mph',0):.0f} mph","No dominant hazard signal"]
        x0, y0, gap = 72, 392, 18
        card_w = (w-144-gap*2)//3
        for i, signal in enumerate(signals[:3]):
            x = x0 + i*(card_w+gap)
            round_rect(draw, (x, y0, x+card_w, 492), 13, c["panel"], outline=c["muted"], width=2)
            draw.text((x+18, y0+20), f"SIGNAL {i+1}", font=font(11, bold=True, mono=True), fill=c["muted"])
            lines = textwrap.wrap(signal, width=28)[:2]
            for j, line in enumerate(lines):
                draw.text((x+18, y0+49+j*24), line, font=font(17, bold=True), fill=c["text"])

        sections = story.get("sections") or {}
        labels=[]
        for key in ("now", "next", "later", "context"):
            slides = sections.get(key) or []
            if slides:
                pretty = str(slides[0]).replace("_", " ").upper()
                labels.append((key.upper(), pretty))
        if labels:
            cell=(w-144)/len(labels)
            for i,(label,value) in enumerate(labels):
                x=72+i*cell
                if i: draw.line((int(x),520,int(x),588),fill=c["panel"],width=2)
                draw.text((int(x)+14,530),label,font=font(12,bold=True,mono=True),fill=c["muted"])
                draw.text((int(x)+14,556),value[:24],font=font(16,bold=True),fill=c["text"])

    def _draw_current(self, draw, w, h, settings, p, c):
        loc = p["location"]
        cur = p.get("current", {})
        daily = p.get("daily", {})
        self._header(draw, w, "Current Conditions", location_label(loc), c)
        obs = p.get("observation") or {}
        badge_p = dict(p)
        obs_cfg = ((settings.get("local_data") or {}).get("observations") or {})
        use_observed = obs_cfg.get("enabled", True) and obs_cfg.get("use_for_current", True) and bool(cur.get("station_id"))
        if use_observed and obs.get("timestamp"):
            badge_p["fetched_at"] = obs.get("timestamp")
        source = f"NWS OBSERVED • {obs.get('station_id')}" if use_observed and obs.get("station_id") else "OPEN-METEO MODEL"
        self._source_badge(draw, badge_p, c, settings, source=source)

        # Hero condition panel.
        round_rect(draw, (54, 132, 706, 486), 20, c["panel2"], outline=c["muted"], width=2)
        draw_weather_icon(draw, cur.get("weather_code"), 78, 180, 1.10, c, is_night=not bool(cur.get("is_day", 1)), icon_cfg=settings.get("icon_system"))
        draw.text((315, 155), n(cur.get("temperature_2m"), 0, "°"), font=font(108, bold=True), fill=c["text"])
        draw.text((322, 286), str(cur.get("description") or "Weather Unavailable"), font=font(34, bold=True), fill=c["accent"])
        draw.text((322, 338), f"Feels like {n(cur.get('apparent_temperature'), 0, '°')}", font=font(25, bold=True), fill=c["muted"])
        story, story_detail = self._condition_story(cur)
        round_rect(draw, (82, 416, 674, 468), 12, c["panel"], outline=c["accent"], width=2)
        draw.text((104, 430), story, font=font(17, bold=True, mono=True), fill=c["accent"])
        draw.text((660, 430), story_detail, font=font(18, bold=True), fill=c["text"], anchor="ra")

        dew = cur.get("dewpoint_f")
        if dew is None:
            dew = dew_point_f(cur.get("temperature_2m"), cur.get("relative_humidity_2m"))
        wind = f"{cur.get('wind_cardinal', '--')} {n(cur.get('wind_speed_10m'), 0, ' mph')}"
        self._metric_tile(draw, (738, 132, 974, 286), "Humidity", n(cur.get("relative_humidity_2m"), 0, "%"), c, f"Dew point {n(dew,0,'°')}", icon_name="humidity", icon_enabled=(settings.get("icon_system") or {}).get("metric_icons", True))
        self._metric_tile(draw, (990, 132, 1226, 286), "Wind", wind, c, f"Gusts {n(cur.get('wind_gusts_10m'),0,' mph')}", icon_name="wind", icon_enabled=(settings.get("icon_system") or {}).get("metric_icons", True))
        self._metric_tile(draw, (738, 302, 974, 456), "Pressure", pressure_inhg(cur.get("surface_pressure")), c, icon_name="pressure", icon_enabled=(settings.get("icon_system") or {}).get("metric_icons", True))
        cloud = n(cur.get("cloud_cover"), 0, "%")
        visibility = n(cur.get("visibility_miles"), 1, " mi") if cur.get("visibility_miles") is not None else cloud
        detail = f"Cloud cover {cloud}" if cur.get("visibility_miles") is not None else "Model cloud estimate"
        self._metric_tile(draw, (990, 302, 1226, 456), "Visibility" if cur.get("visibility_miles") is not None else "Cloud Cover", visibility, c, detail, icon_name="visibility" if cur.get("visibility_miles") is not None else "cloud_cover", icon_enabled=(settings.get("icon_system") or {}).get("metric_icons", True))

        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        pops = daily.get("precipitation_probability_max") or []
        round_rect(draw, (54, 506, 1226, 608), 16, c["panel"])
        items = [
            ("TODAY'S HIGH", n(highs[0] if highs else None,0,"°")),
            ("TONIGHT'S LOW", n(lows[0] if lows else None,0,"°")),
            ("RAIN CHANCE", n(pops[0] if pops else None,0,"%")),
            ("WIND GUST", n(cur.get("wind_gusts_10m"),0," mph")),
        ]
        cell = 1172 // 4
        for i, (label, value) in enumerate(items):
            x = 54 + i*cell
            if i: draw.line((x, 520, x, 594), fill=c["panel2"], width=2)
            draw.text((x+cell//2, 531), label, font=font(13, bold=True, mono=True), fill=c["muted"], anchor="mm")
            draw.text((x+cell//2, 570), value, font=font(29, bold=True), fill=c["text"], anchor="mm")

    def _draw_today(self, draw, w, h, settings, p, c):
        loc, daily = p["location"], p.get("daily", {})
        self._header(draw, w, "Your Forecast", location_label(loc), c)
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        pops = daily.get("precipitation_probability_max") or []
        code = codes[0] if codes else None
        draw_weather_icon(draw, code, 92, 178, 1.45, c, icon_cfg=settings.get("icon_system"))
        from app.weather import describe_weather
        draw.text((365, 170), "TODAY", font=font(33, bold=True, mono=True), fill=c["accent"])
        draw.text((365, 225), describe_weather(code), font=font(48, bold=True), fill=c["text"])
        draw.text((365, 316), f"HIGH  {n(highs[0] if highs else None, 0, '°')}", font=font(48, bold=True), fill=c["text"])
        draw.text((365, 382), f"LOW   {n(lows[0] if lows else None, 0, '°')}", font=font(38), fill=c["muted"])
        draw.text((365, 446), f"Chance of precipitation: {n(pops[0] if pops else None, 0, '%')}", font=font(29), fill=c["text"])

    def _hour_indices(self, p):
        hourly = p.get("hourly", {})
        times = hourly.get("time") or []
        if not times:
            return []
        now_local = p.get("current", {}).get("time")
        start = 0
        if now_local in times:
            start = times.index(now_local)
        else:
            # current values are usually aligned to an hourly timestamp; choose first future-ish point.
            try:
                target = dt.datetime.fromisoformat(now_local)
                for i, t in enumerate(times):
                    if dt.datetime.fromisoformat(t) >= target:
                        start = i
                        break
            except Exception:
                start = 0
        return list(range(start, min(start + 6, len(times))))

    def _hour_is_night(self, p: dict[str, Any], hour_value: Any) -> bool:
        """Return whether a local hourly forecast timestamp falls outside daylight.

        Open-Meteo's hourly and daily timestamps are requested in the location's
        local timezone.  We match the hourly calendar date to that day's sunrise
        and sunset, which also handles hours after midnight correctly.
        """
        try:
            hour_dt = dt.datetime.fromisoformat(str(hour_value))
            daily = p.get("daily") or {}
            dates = daily.get("time") or []
            sunrises = daily.get("sunrise") or []
            sunsets = daily.get("sunset") or []
            date_key = hour_dt.date().isoformat()
            idx = dates.index(date_key)
            sunrise_dt = dt.datetime.fromisoformat(str(sunrises[idx]))
            sunset_dt = dt.datetime.fromisoformat(str(sunsets[idx]))
            hour_minute = hour_dt.hour * 60 + hour_dt.minute
            sunrise_minute = sunrise_dt.hour * 60 + sunrise_dt.minute
            sunset_minute = sunset_dt.hour * 60 + sunset_dt.minute
            return hour_minute < sunrise_minute or hour_minute >= sunset_minute
        except Exception:
            # Forecast timestamps normally include a date and daily sunrise/sunset.
            # This conservative fallback still produces sensible icons if a source
            # temporarily omits one of those daily fields.
            try:
                hour = dt.datetime.fromisoformat(str(hour_value)).hour
                return hour < 6 or hour >= 18
            except Exception:
                return False

    def _draw_hourly(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Hourly Forecast", location_label(p["location"]), c)
        self._source_badge(draw, p, c, settings)
        hourly = p.get("hourly", {})
        base = self._hour_indices(p)
        if not base:
            draw.text((w//2, h//2), "Hourly forecast unavailable", font=font(36, bold=True), fill=c["text"], anchor="mm")
            return
        first = base[0]
        times = hourly.get("time") or []
        indices = list(range(first, min(first + 8, len(times))))
        temps = []
        for i in indices:
            try: temps.append(float((hourly.get("temperature_2m") or [None]*len(times))[i]))
            except Exception: temps.append(None)
        valid = [x for x in temps if x is not None]
        tmin, tmax = (min(valid), max(valid)) if valid else (0.0, 1.0)
        if abs(tmax-tmin) < 4: tmin -= 2; tmax += 2

        chart = (72, 150, 1208, 318)
        round_rect(draw, (54, 132, 1226, 604), 18, c["panel2"], outline=c["muted"], width=1)
        # Reference grid and smooth-looking connected temperature trend.
        for row in range(3):
            y = chart[1] + int((chart[3]-chart[1]) * row / 2)
            draw.line((chart[0], y, chart[2], y), fill=c["panel"], width=1)
        xs=[]; pts=[]
        step=(chart[2]-chart[0])/max(1,len(indices)-1)
        for col, i in enumerate(indices):
            x=int(chart[0]+col*step); xs.append(x)
            temp=temps[col]
            if temp is None: y=(chart[1]+chart[3])//2
            else: y=int(chart[3]-(temp-tmin)/max(1,tmax-tmin)*(chart[3]-chart[1]))
            pts.append((x,y))
        if len(pts)>1: draw.line(pts, fill=c["accent"], width=5, joint="curve")
        for col,i in enumerate(indices):
            x,y=pts[col]
            draw.ellipse((x-7,y-7,x+7,y+7),fill=c["accent"],outline="#ffffff",width=2)
            try: label=dt.datetime.fromisoformat(times[i]).strftime("%I %p").lstrip("0")
            except Exception: label="--"
            draw.text((x, 142), label, font=font(15,bold=True,mono=True), fill=c["muted"], anchor="mm")
            draw.text((x, max(164,y-25)), n(temps[col],0,"°"), font=font(20,bold=True), fill=c["text"], anchor="mm")
            is_night=self._hour_is_night(p,times[i])
            draw_weather_icon(draw,(hourly.get("weather_code") or [None]*len(times))[i],x-34,328,0.38,c,is_night=is_night,icon_cfg=settings.get("icon_system"))

        # Precipitation and wind rows make the trend useful at a glance without eight repeated cards.
        draw.text((72, 438), "RAIN", font=font(14,bold=True,mono=True), fill=c["muted"])
        draw.text((72, 526), "WIND", font=font(14,bold=True,mono=True), fill=c["muted"])
        for col,i in enumerate(indices):
            x=xs[col]
            pop=(hourly.get("precipitation_probability") or [None]*len(times))[i]
            try: pv=max(0,min(100,float(pop)))
            except Exception: pv=0
            bh=int(54*pv/100)
            draw.rounded_rectangle((x-22, 500-bh, x+22, 500), radius=6, fill=c["accent"] if pv>=40 else c["panel"])
            draw.text((x, 510), f"{int(pv)}%", font=font(13,bold=True,mono=True), fill=c["text"], anchor="ma")
            wind=(hourly.get("wind_speed_10m") or [None]*len(times))[i]
            draw.text((x, 553), n(wind,0," mph"), font=font(13,bold=True,mono=True), fill=c["text"], anchor="mm")
        draw.text((1208, 588), "TEMPERATURE TREND • RAIN CHANCE • WIND", font=font(12,bold=True,mono=True), fill=c["muted"], anchor="ra")

    def _future_hour_indices(self, p: dict[str, Any], count: int = 24) -> list[int]:
        hourly = p.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return []
        now = dt.datetime.now()
        first = 0
        for i, value in enumerate(times):
            try:
                stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if stamp.tzinfo is not None:
                    stamp = stamp.astimezone().replace(tzinfo=None)
                if stamp >= now.replace(minute=0, second=0, microsecond=0):
                    first = i
                    break
            except Exception:
                continue
        return list(range(first, min(len(times), first + max(1, count))))

    def _series_number(self, series: dict[str, Any], key: str, index: int) -> float | None:
        try:
            value = (series.get(key) or [])[index]
            return None if value is None else float(value)
        except Exception:
            return None

    def _draw_wind_arrow(self, draw, x: int, y: int, degrees: float | None, c: dict[str, str], radius: int = 24) -> None:
        try:
            deg = float(degrees)
        except Exception:
            deg = 0.0
        # Meteorological direction is where wind comes from. Point the arrow toward
        # the direction the air is moving so the graphic reads naturally on TV.
        angle = math.radians(deg + 90.0)
        dx = math.cos(angle) * radius
        dy = math.sin(angle) * radius
        x2, y2 = x + dx, y + dy
        draw.line((x - dx * .55, y - dy * .55, x2, y2), fill=c["accent"], width=4)
        head = max(7, radius // 3)
        left = angle + math.radians(145)
        right = angle - math.radians(145)
        pts = [(x2, y2), (x2 + math.cos(left)*head, y2 + math.sin(left)*head), (x2 + math.cos(right)*head, y2 + math.sin(right)*head)]
        draw.polygon(pts, fill=c["accent"])
        draw.ellipse((x-3, y-3, x+3, y+3), fill=c["text"])

    def _draw_day_ahead(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Next 24 Hours", location_label(p["location"]), c)
        self._source_badge(draw, p, c, settings)
        hourly = p.get("hourly") or {}
        hours = max(12, min(24, int((settings.get("forecast_graphics") or {}).get("day_ahead_hours", 24))))
        indices = self._future_hour_indices(p, hours)
        times = hourly.get("time") or []
        if not indices:
            draw.text((w//2, h//2), "24-hour forecast unavailable", font=font(34, bold=True), fill=c["text"], anchor="mm")
            return
        # Six representative checkpoints across the full window keep the board legible
        # from across a room while the summary cards preserve the important extremes.
        sample_count = 6
        if len(indices) <= sample_count:
            samples = indices
        else:
            samples = []
            for nidx in range(sample_count):
                pos = round(nidx * (len(indices)-1) / (sample_count-1))
                i = indices[pos]
                if i not in samples: samples.append(i)
        round_rect(draw, (50, 132, 1230, 455), 18, c["panel2"], outline=c["muted"], width=1)
        card_w = (1140 // max(1, len(samples)))
        for col, i in enumerate(samples):
            x1 = 70 + col * card_w
            x2 = x1 + card_w - 12
            if col:
                draw.line((x1-6, 155, x1-6, 432), fill=c["panel"], width=1)
            try:
                stamp = dt.datetime.fromisoformat(str(times[i]))
                label = stamp.strftime("%I %p").lstrip("0")
                day = stamp.strftime("%a").upper()
            except Exception:
                label, day = "--", ""
            temp = self._series_number(hourly, "temperature_2m", i)
            feels = self._series_number(hourly, "apparent_temperature", i)
            pop = self._series_number(hourly, "precipitation_probability", i) or 0.0
            code = (hourly.get("weather_code") or [None]*len(times))[i]
            draw.text(((x1+x2)//2, 164), f"{day} {label}", font=font(14, bold=True, mono=True), fill=c["muted"], anchor="mm")
            draw_weather_icon(draw, code, (x1+x2)//2-36, 192, .40, c, is_night=self._hour_is_night(p, times[i]), icon_cfg=settings.get("icon_system"))
            draw.text(((x1+x2)//2, 355), n(temp,0,"°"), font=font(31,bold=True), fill=c["text"], anchor="mm")
            if feels is not None and temp is not None and abs(feels-temp) >= 4:
                draw.text(((x1+x2)//2, 387), f"FEELS {feels:.0f}°", font=font(11,bold=True,mono=True), fill=c["muted"], anchor="mm")
            draw.text(((x1+x2)//2, 420), f"RAIN {pop:.0f}%", font=font(12,bold=True,mono=True), fill=c["accent"] if pop >= 40 else c["muted"], anchor="mm")

        temps=[self._series_number(hourly,"temperature_2m",i) for i in indices]
        gusts=[self._series_number(hourly,"wind_gusts_10m",i) for i in indices]
        pops=[self._series_number(hourly,"precipitation_probability",i) for i in indices]
        rains=[self._series_number(hourly,"precipitation",i) for i in indices]
        valid_t=[v for v in temps if v is not None]; valid_g=[v for v in gusts if v is not None]; valid_p=[v for v in pops if v is not None]
        total=sum(v for v in rains if v is not None)
        peak_pop=max(valid_p) if valid_p else 0
        cards=[
            ("24-HR RANGE", f"{min(valid_t):.0f}° – {max(valid_t):.0f}°" if valid_t else "--", "Temperature range"),
            ("PEAK RAIN", f"{peak_pop:.0f}%", f"{total:.2f}\" forecast total"),
            ("PEAK GUST", f"{max(valid_g):.0f} mph" if valid_g else "--", "Strongest modeled gust"),
        ]
        for j,(label,value,detail) in enumerate(cards):
            self._metric_tile(draw,(70+j*380,478,420+j*380,592),label,value,c,detail)

    def _draw_humidity_outlook(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Humidity & Dew Point", location_label(p["location"]), c)
        self._source_badge(draw, p, c, settings)
        hourly=p.get("hourly") or {}; times=hourly.get("time") or []; indices=self._future_hour_indices(p,12)
        if not indices:
            draw.text((w//2,h//2),"Humidity forecast unavailable",font=font(34,bold=True),fill=c["text"],anchor="mm"); return
        round_rect(draw,(54,132,1226,594),18,c["panel2"],outline=c["muted"],width=1)
        chart=(90,175,1188,410); step=(chart[2]-chart[0])/max(1,len(indices)-1)
        humidity=[]; dew=[]; temps=[]
        for i in indices:
            rh=self._series_number(hourly,"relative_humidity_2m",i); t=self._series_number(hourly,"temperature_2m",i)
            humidity.append(rh); temps.append(t); dew.append(dew_point_f(t,rh) if t is not None and rh is not None else None)
        # RH background bands communicate comfort without adding another legend-heavy chart.
        for pct, label in ((30,"DRY"),(60,"COMFORT"),(80,"HUMID")):
            y=int(chart[3]-(pct/100)*(chart[3]-chart[1]))
            draw.line((chart[0],y,chart[2],y),fill=c["panel"],width=1)
            draw.text((chart[0]-10,y),f"{pct}%",font=font(11,mono=True),fill=c["muted"],anchor="rm")
        rh_pts=[]
        for col,i in enumerate(indices):
            x=int(chart[0]+col*step); rh=humidity[col]
            y=int(chart[3]-((rh or 0)/100)*(chart[3]-chart[1])); rh_pts.append((x,y))
        if len(rh_pts)>1: draw.line(rh_pts,fill="#78d5ff",width=5,joint="curve")
        for col,i in enumerate(indices):
            x,y=rh_pts[col]; draw.ellipse((x-5,y-5,x+5,y+5),fill="#78d5ff")
            try: label=dt.datetime.fromisoformat(str(times[i])).strftime("%I %p").lstrip("0")
            except Exception: label="--"
            draw.text((x,430),label,font=font(12,bold=True,mono=True),fill=c["muted"],anchor="mm")
            if col % 2 == 0:
                draw.text((x,458),f"{(humidity[col] or 0):.0f}%",font=font(13,bold=True),fill=c["text"],anchor="mm")
                draw.text((x,482),f"DP {dew[col]:.0f}°" if dew[col] is not None else "DP --",font=font(12,bold=True,mono=True),fill=c["accent"],anchor="mm")
        valid_dew=[v for v in dew if v is not None]; valid_rh=[v for v in humidity if v is not None]
        peak_dew=max(valid_dew) if valid_dew else None; low_dew=min(valid_dew) if valid_dew else None; peak_rh=max(valid_rh) if valid_rh else None
        story="COMFORTABLE"
        if peak_dew is not None and peak_dew >= 70: story="OPPRESSIVE HUMIDITY"
        elif peak_dew is not None and peak_dew >= 65: story="VERY HUMID"
        elif peak_dew is not None and peak_dew <= 35: story="VERY DRY AIR"
        draw.text((88,534),story,font=font(22,bold=True,mono=True),fill=c["accent"])
        draw.text((1188,537),f"DEW POINT {n(low_dew,0,'°')} TO {n(peak_dew,0,'°')}  •  RH PEAK {n(peak_rh,0,'%')}",font=font(13,bold=True,mono=True),fill=c["muted"],anchor="ra")

    def _draw_wind_outlook(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Wind Outlook", location_label(p["location"]), c)
        self._source_badge(draw, p, c, settings)
        hourly=p.get("hourly") or {}; times=hourly.get("time") or []; indices=self._future_hour_indices(p,12)
        if not indices:
            draw.text((w//2,h//2),"Wind forecast unavailable",font=font(34,bold=True),fill=c["text"],anchor="mm"); return
        round_rect(draw,(54,132,1226,594),18,c["panel2"],outline=c["muted"],width=1)
        speeds=[self._series_number(hourly,"wind_speed_10m",i) for i in indices]
        gusts=[self._series_number(hourly,"wind_gusts_10m",i) for i in indices]
        dirs=[self._series_number(hourly,"wind_direction_10m",i) for i in indices]
        maxv=max([v for v in speeds+gusts if v is not None] or [10]); maxv=max(10,maxv*1.15)
        chart=(90,175,1188,390); step=(chart[2]-chart[0])/max(1,len(indices)-1)
        for level in (0.25,.5,.75,1.0):
            y=int(chart[3]-level*(chart[3]-chart[1])); draw.line((chart[0],y,chart[2],y),fill=c["panel"],width=1)
            draw.text((chart[0]-10,y),f"{maxv*level:.0f}",font=font(10,mono=True),fill=c["muted"],anchor="rm")
        sp=[]; gp=[]
        for col,i in enumerate(indices):
            x=int(chart[0]+col*step)
            sy=int(chart[3]-((speeds[col] or 0)/maxv)*(chart[3]-chart[1])); gy=int(chart[3]-((gusts[col] or 0)/maxv)*(chart[3]-chart[1]))
            sp.append((x,sy)); gp.append((x,gy))
        if len(sp)>1: draw.line(sp,fill="#78d5ff",width=4,joint="curve")
        if len(gp)>1: draw.line(gp,fill=c["accent"],width=4,joint="curve")
        for col,i in enumerate(indices):
            x=sp[col][0]
            if col % 2 == 0:
                self._draw_wind_arrow(draw,x,440,dirs[col],c,18)
                try: label=dt.datetime.fromisoformat(str(times[i])).strftime("%I %p").lstrip("0")
                except Exception: label="--"
                draw.text((x,476),label,font=font(11,bold=True,mono=True),fill=c["muted"],anchor="mm")
                from app.weather import wind_direction as _wd
                draw.text((x,499),_wd(dirs[col]),font=font(12,bold=True,mono=True),fill=c["text"],anchor="mm")
        max_speed=max([v for v in speeds if v is not None] or [0]); max_gust=max([v for v in gusts if v is not None] or [0])
        story="LIGHT WINDS" if max_gust < 15 else "BREEZY" if max_gust < 25 else "GUSTY" if max_gust < 40 else "STRONG WINDS"
        draw.text((88,548),story,font=font(22,bold=True,mono=True),fill=c["accent"])
        draw.text((1188,548),f"SUSTAINED PEAK {max_speed:.0f} MPH  •  GUST PEAK {max_gust:.0f} MPH",font=font(13,bold=True,mono=True),fill=c["muted"],anchor="ra")
        draw.text((1188,574),"BLUE: SUSTAINED  •  GOLD: GUSTS",font=font(11,bold=True,mono=True),fill=c["muted"],anchor="ra")

    def _draw_rain_accumulation(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Rainfall Accumulation", location_label(p["location"]), c)
        self._source_badge(draw, p, c, settings)
        hourly=p.get("hourly") or {}; times=hourly.get("time") or []; indices=self._future_hour_indices(p,24)
        if not indices:
            draw.text((w//2,h//2),"Rainfall forecast unavailable",font=font(34,bold=True),fill=c["text"],anchor="mm"); return
        vals=[max(0.0,self._series_number(hourly,"precipitation",i) or 0.0) for i in indices]
        cumulative=[]; total=0.0
        for v in vals: total+=v; cumulative.append(total)
        round_rect(draw,(54,132,1226,594),18,c["panel2"],outline=c["muted"],width=1)
        chart=(88,175,1188,405); max_total=max(.10,total*1.15); step=(chart[2]-chart[0])/max(1,len(indices)-1)
        for level in (.25,.5,.75,1.0):
            y=int(chart[3]-level*(chart[3]-chart[1])); draw.line((chart[0],y,chart[2],y),fill=c["panel"],width=1)
            draw.text((chart[0]-10,y),f"{max_total*level:.2f}\"",font=font(10,mono=True),fill=c["muted"],anchor="rm")
        pts=[]
        for col,val in enumerate(cumulative):
            x=int(chart[0]+col*step); y=int(chart[3]-(val/max_total)*(chart[3]-chart[1])); pts.append((x,y))
            if vals[col] > 0:
                bh=max(3,int((vals[col]/max_total)*(chart[3]-chart[1]))); draw.rectangle((x-4,chart[3]-bh,x+4,chart[3]),fill="#6dbcf2")
        if len(pts)>1: draw.line(pts,fill=c["accent"],width=5,joint="curve")
        checkpoints=[0,5,11,17,23]
        for pos in checkpoints:
            if pos >= len(indices): continue
            x=pts[pos][0]
            try: label=dt.datetime.fromisoformat(str(times[indices[pos]])).strftime("%I %p").lstrip("0")
            except Exception: label="--"
            draw.text((x,429),label,font=font(11,bold=True,mono=True),fill=c["muted"],anchor="mm")
        def _sum(nh): return sum(vals[:min(nh,len(vals))])
        cards=[("NEXT 6 HOURS",f"{_sum(6):.2f}\"","Forecast rainfall"),("NEXT 12 HOURS",f"{_sum(12):.2f}\"","Forecast rainfall"),("NEXT 24 HOURS",f"{_sum(24):.2f}\"","Forecast rainfall")]
        for j,(label,value,detail) in enumerate(cards): self._metric_tile(draw,(70+j*380,468,420+j*380,575),label,value,c,detail)
        if total < .01:
            draw.text((1188,155),"LITTLE OR NO MEASURABLE RAIN",font=font(13,bold=True,mono=True),fill=c["muted"],anchor="ra")

    def _forecast_bullets(self, period: dict[str, Any]) -> list[str]:
        detail = str(period.get("detailedForecast") or period.get("shortForecast") or "").strip()
        bullets=[]
        pop=period.get("precipitationProbability")
        if pop is not None:
            try:
                if float(pop) >= 20: bullets.append(f"Rain chance near {float(pop):.0f}%")
            except Exception: pass
        wind=f"{period.get('windDirection','')} {period.get('windSpeed','')}".strip()
        if wind: bullets.append(f"Wind {wind}")
        # Use the official NWS sentences, shortened only by deterministic text rules.
        for sentence in [x.strip() for x in detail.replace(";", ".").split(".") if x.strip()]:
            sentence=" ".join(sentence.split())
            if len(sentence) > 92: sentence=sentence[:89].rsplit(" ",1)[0]+"…"
            low=sentence.lower()
            if any(low in b.lower() or b.lower() in low for b in bullets): continue
            bullets.append(sentence)
            if len(bullets) >= 4: break
        return bullets[:4]

    def _draw_nws_forecast(self, draw, w, h, settings, p, c):
        self._header(draw, w, "NWS Forecast Brief", location_label(p["location"]), c)
        nws = p.get("nws") or {}
        periods = nws.get("periods") or []
        if not periods:
            draw.text((w//2, 270), "NWS FORECAST TEMPORARILY UNAVAILABLE", font=font(34, bold=True), fill=c["accent"], anchor="mm")
            msg = nws.get("error") or "WeatherStream will retry automatically."
            for row, line in enumerate(textwrap.wrap(msg, width=82)[:3]):
                draw.text((w//2, 330 + row*30), line, font=font(18), fill=c["muted"], anchor="mm")
            return
        main=periods[0]
        secondary=periods[1] if len(periods)>1 else None
        round_rect(draw,(54,126,816,586),18,c["panel2"],outline=c["muted"],width=1)
        draw.text((82,151),str(main.get("name") or "Forecast").upper(),font=font(26,bold=True,mono=True),fill=c["accent"])
        temp=main.get("temperature"); unit=main.get("temperatureUnit") or "F"
        draw.text((780,142),f"{safe(temp)}°{unit}",font=font(44,bold=True),fill=c["text"],anchor="ra")
        short=str(main.get("shortForecast") or "Forecast")
        for row,line in enumerate(textwrap.wrap(short,width=39)[:2]):
            draw.text((82,205+row*32),line,font=font(25,bold=True),fill=c["text"])
        bullets=self._forecast_bullets(main) if (settings.get("forecast_graphics") or {}).get("nws_summary_enabled",True) else []
        if bullets:
            y=291
            for bullet in bullets:
                draw.ellipse((86,y+8,94,y+16),fill=c["accent"])
                for sub,line in enumerate(textwrap.wrap(bullet,width=55)[:2]):
                    draw.text((108,y+sub*25),line,font=font(18),fill=c["text"])
                y += 55 if len(textwrap.wrap(bullet,width=55))>1 else 40
        else:
            detail=str(main.get("detailedForecast") or short)
            for row,line in enumerate(textwrap.wrap(detail,width=58)[:7]):
                draw.text((82,292+row*31),line,font=font(18),fill=c["text"])

        if secondary:
            round_rect(draw,(840,126,1226,586),18,c["panel"],outline=c["muted"],width=1)
            draw.text((865,153),str(secondary.get("name") or "Next").upper(),font=font(20,bold=True,mono=True),fill=c["accent"])
            draw.text((1198,147),f"{safe(secondary.get('temperature'))}°{secondary.get('temperatureUnit') or 'F'}",font=font(32,bold=True),fill=c["text"],anchor="ra")
            sshort=str(secondary.get("shortForecast") or "")
            for row,line in enumerate(textwrap.wrap(sshort,width=29)[:3]):
                draw.text((865,207+row*28),line,font=font(20,bold=True),fill=c["text"])
            y=310
            for bullet in self._forecast_bullets(secondary)[:3]:
                draw.text((866,y),"•",font=font(18,bold=True),fill=c["accent"])
                for sub,line in enumerate(textwrap.wrap(bullet,width=30)[:3]):
                    draw.text((889,y+sub*23),line,font=font(15),fill=c["text"])
                y += 68
        office=nws.get("office") or "NWS"
        draw.text((1196,610),f"OFFICIAL FORECAST • NATIONAL WEATHER SERVICE {office}",font=font(12,bold=True,mono=True),fill=c["muted"],anchor="ra")

    def _draw_precipitation(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Rain Timing", location_label(p["location"]), c)
        self._source_badge(draw, p, c, settings)
        hourly = p.get("hourly", {})
        base = self._hour_indices(p)
        times = hourly.get("time") or []
        if not base or not times:
            draw.text((w//2, h//2), "Precipitation forecast unavailable", font=font(34, bold=True), fill=c["text"], anchor="mm")
            return
        first=base[0]; indices=list(range(first,min(first+10,len(times))))
        round_rect(draw,(54,138,1226,594),18,c["panel2"],outline=c["muted"],width=1)
        left,right=90,1190; step=(right-left)/max(1,len(indices)-1)
        draw.line((left,270,right,270),fill=c["muted"],width=3)
        total=0.0; peak=0.0; peak_i=None
        for col,i in enumerate(indices):
            x=int(left+col*step)
            try: label=dt.datetime.fromisoformat(times[i]).strftime("%I %p").lstrip("0")
            except Exception: label="--"
            pop=(hourly.get("precipitation_probability") or [None]*len(times))[i]
            amt=(hourly.get("precipitation") or [None]*len(times))[i]
            try: pv=max(0,min(100,float(pop)))
            except Exception: pv=0.0
            try: av=max(0.0,float(amt)); total+=av
            except Exception: av=0.0
            if pv>peak: peak=pv; peak_i=i
            state="DRY" if pv<20 else "CHANCE" if pv<50 else "LIKELY" if pv<80 else "HIGH"
            draw.line((x,258,x,282),fill=c["accent"] if pv>=50 else c["muted"],width=4)
            draw.text((x,206),label,font=font(15,bold=True,mono=True),fill=c["muted"],anchor="mm")
            draw.text((x,238),f"{int(pv)}%",font=font(20,bold=True),fill=c["text"],anchor="mm")
            draw.text((x,304),state,font=font(12,bold=True,mono=True),fill=c["accent"] if pv>=50 else c["muted"],anchor="mm")
            draw.text((x,330),f"{av:.2f}\"",font=font(12,mono=True),fill=c["text"],anchor="mm")
        draw.text((90,378),"WHAT TO EXPECT",font=font(14,bold=True,mono=True),fill=c["muted"])
        peak_label="--"
        if peak_i is not None:
            try: peak_label=dt.datetime.fromisoformat(times[peak_i]).strftime("%I:%M %p").lstrip("0")
            except Exception: pass
        cards=[
            ("PEAK CHANCE",f"{peak:.0f}%",f"Near {peak_label}"),
            ("NEXT 10 HOURS",f"{total:.2f}\"","Model precipitation total"),
            ("TODAY",n((p.get("daily",{}).get("precipitation_sum") or [None])[0],2,' in'),"Forecast total"),
        ]
        cw=350
        for j,(label,value,detail) in enumerate(cards):
            x=90+j*380
            self._metric_tile(draw,(x,402,x+cw,554),label,value,c,detail)
        draw.text((1190,576),"PROBABILITY + FORECAST AMOUNT",font=font(12,bold=True,mono=True),fill=c["muted"],anchor="ra")

    def _draw_condition_focus(self, draw, w, h, settings, p, c):
        cur = p.get("current") or {}; cfg = settings.get("smart_programming") or {}
        t = cur.get("temperature_2m"); rh = cur.get("relative_humidity_2m"); wind = cur.get("wind_speed_10m"); gust = cur.get("wind_gusts_10m")
        hi = heat_index_f(t, rh); wc = wind_chill_f(t, wind); dew = dew_point_f(t, rh)
        apparent = cur.get("apparent_temperature")
        title, value, expl = "FEELS LIKE", n(apparent,0,"°"), "Apparent temperature based on the current conditions"
        try:
            if hi is not None and hi >= float(cfg.get("heat_threshold",95)):
                title, value, expl = "HEAT INDEX", n(hi,0,"°"), "Heat and humidity are combining to make it feel hotter"
            elif wc is not None and wc <= float(cfg.get("cold_threshold",32)):
                title, value, expl = "WIND CHILL", n(wc,0,"°"), "Cold air and wind are making it feel colder"
            elif float(gust or 0) >= 25:
                title, value, expl = "WIND GUSTS", n(gust,0," mph"), "Gusty winds are the standout local condition"
            elif dew is not None and dew >= 65:
                title, value, expl = "DEW POINT", n(dew,0,"°"), "Humid air is the standout local condition"
        except Exception: pass
        self._header(draw, w, title, location_label(p.get("location") or {}), c)
        round_rect(draw,(92,145,w-92,430),22,c["panel"],c["muted"],2)
        draw.text((w//2,250),value,font=font(112,bold=True),fill=c["accent"],anchor="mm")
        draw.text((w//2,355),expl,font=font(24),fill=c["text"],anchor="mm")
        cards=[("HUMIDITY",n(rh,0,"%"),"humidity"),("DEW POINT",n(dew,0,"°"),"dew_point"),("WIND",f"{safe(cur.get('wind_cardinal'))} {n(wind,0,' mph')}","wind")]
        cw=(w-220)//3
        icons_on=(settings.get("icon_system") or {}).get("metric_icons",True)
        for i,(lab,val,metric) in enumerate(cards):
            x=92+i*(cw+18); round_rect(draw,(x,458,x+cw,570),16,c["panel2"]); label_x=x+18
            if icons_on and paste_rwn_icon(getattr(draw,"_image",None),rwn_metric_icon(metric,32),x+16,472): label_x=x+55
            draw.text((label_x,480),lab,font=font(16,bold=True,mono=True),fill=c["muted"]); draw.text((x+cw-18,530),val,font=font(28,bold=True),fill=c["text"],anchor="rm")

    def _local_time_text(self, value: Any, timezone_name: str | None = None) -> str:
        if not value:
            return "--"
        try:
            stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            try:
                stamp = stamp.astimezone(ZoneInfo(str(timezone_name or "UTC")))
            except Exception:
                stamp = stamp.astimezone()
            return stamp.strftime("%I:%M %p").lstrip("0")
        except Exception:
            return "--"

    def _draw_today_so_far(self, draw, w, h, settings, p, c):
        loc=p.get("location") or {}; loc_id=loc.get("id") or settings.get("primary_location_id")
        summary=self.history_store.today_summary(loc_id,loc.get("timezone")) if self.history_store and loc_id else {"samples":0}
        self._header(draw,w,"TODAY SO FAR",location_label(loc),c)
        obs=p.get("observation") or {}; source=f"NWS OBSERVED • {obs.get('station_id')}" if obs.get("station_id") else "LOCAL HISTORY"
        badge=dict(p); badge["fetched_at"]=obs.get("timestamp") or p.get("fetched_at"); self._source_badge(draw,badge,c,settings,source=source)
        if not summary.get("samples"):
            draw.text((w//2,300),"TODAY'S OBSERVATIONS ARE BUILDING",font=font(38,bold=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,354),"This screen fills automatically as real observations are collected.",font=font(20),fill=c["text"],anchor="mm"); return
        daily=p.get("daily") or {}; rain=(daily.get("precipitation_sum") or [None])[0]
        cards=[
            ("HIGH",n(summary.get("high"),0,"°"),self._local_time_text(summary.get("high_time"),loc.get("timezone")),"temperature"),
            ("LOW",n(summary.get("low"),0,"°"),self._local_time_text(summary.get("low_time"),loc.get("timezone")),"temperature"),
            ("PEAK GUST",n(summary.get("max_gust"),0," mph"),self._local_time_text(summary.get("max_gust_time"),loc.get("timezone")),"gust"),
            ("FORECAST RAIN",n(rain,2,' in'),"Forecast-day accumulation","rainfall"),
        ]
        cw=274
        for i,(lab,val,detail,metric) in enumerate(cards):
            x=54+i*(cw+18); self._metric_tile(draw,(x,142,x+cw,306),lab,val,c,detail,icon_name=metric,icon_enabled=(settings.get("icon_system") or {}).get("metric_icons",True))
        round_rect(draw,(54,334,1226,492),18,c["panel2"],outline=c["muted"],width=2)
        change=summary.get("temperature_change"); pdelta=summary.get("pressure_delta_hpa")
        change_text="--" if change is None else f"{float(change):+.0f}°"
        ptext="--" if pdelta is None else f"{float(pdelta):+.1f} hPa"
        cols=[("TEMPERATURE CHANGE",change_text,"Since first observation today"),("PRESSURE",safe(summary.get("pressure_trend"),"--"),ptext),("OBSERVATIONS",str(summary.get("samples",0)),"Stored locally today")]
        cell=1172//3
        for i,(lab,val,detail) in enumerate(cols):
            x=54+i*cell
            if i: draw.line((x,352,x,472),fill=c["panel"],width=2)
            draw.text((x+cell//2,365),lab,font=font(14,bold=True,mono=True),fill=c["muted"],anchor="mm")
            draw.text((x+cell//2,414),val,font=font(34,bold=True),fill=c["accent"] if i<2 else c["text"],anchor="mm")
            draw.text((x+cell//2,456),detail,font=font(13,mono=True),fill=c["text"],anchor="mm")
        draw.text((w//2,540),"OBSERVATIONAL SUMMARY • HIGH/LOW/GUST FROM LOCAL HISTORY",font=font(13,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_past_24_hours(self, draw, w, h, settings, p, c):
        loc=p.get("location") or {}; loc_id=loc.get("id") or settings.get("primary_location_id")
        rows=self.history_store.recent(loc_id,24,1000) if self.history_store and loc_id else []
        summary=self.history_store.summary(loc_id,24) if self.history_store and loc_id else {"samples":0}
        self._header(draw,w,"PAST 24 HOURS",location_label(loc),c)
        if len(rows)<2:
            draw.text((w//2,310),"24-HOUR HISTORY IS BUILDING",font=font(40,bold=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,362),"Real station observations will appear here as they accumulate.",font=font(21),fill=c["text"],anchor="mm"); return
        graph=(74,146,1206,430); round_rect(draw,graph,18,c["panel2"],outline=c["muted"],width=2)
        temp_rows=[r for r in rows if r.get("temperature") is not None]
        pressure_rows=[r for r in rows if r.get("pressure_hpa") is not None]
        if temp_rows:
            vals=[float(r["temperature"]) for r in temp_rows]; lo=min(vals)-2; hi=max(vals)+2; pts=[]
            for i,r in enumerate(temp_rows):
                x=graph[0]+36+(graph[2]-graph[0]-72)*(i/max(1,len(temp_rows)-1)); y=graph[3]-38-(float(r["temperature"])-lo)/max(1,hi-lo)*(graph[3]-graph[1]-76); pts.append((x,y))
            if len(pts)>1: draw.line(pts,fill=c["accent"],width=5,joint="curve")
            draw.text((graph[0]+18,graph[1]+18),f"TEMP  {min(vals):.0f}°–{max(vals):.0f}°",font=font(14,bold=True,mono=True),fill=c["accent"])
        if pressure_rows:
            vals=[float(r["pressure_hpa"]) for r in pressure_rows]; lo=min(vals)-0.5; hi=max(vals)+0.5; pts=[]
            for i,r in enumerate(pressure_rows):
                x=graph[0]+36+(graph[2]-graph[0]-72)*(i/max(1,len(pressure_rows)-1)); y=graph[3]-38-(float(r["pressure_hpa"])-lo)/max(0.1,hi-lo)*(graph[3]-graph[1]-76); pts.append((x,y))
            if len(pts)>1: draw.line(pts,fill=c["text"],width=2)
            draw.text((graph[2]-18,graph[1]+18),f"PRESSURE {min(vals):.0f}–{max(vals):.0f} hPa",font=font(13,bold=True,mono=True),fill=c["text"],anchor="ra")
        cards=[("HIGH",n(summary.get("high"),0,"°")),("LOW",n(summary.get("low"),0,"°")),("MAX GUST",n(summary.get("max_gust"),0," mph")),("TEMP CHANGE",("--" if summary.get("temperature_change") is None else f"{float(summary['temperature_change']):+.0f}°")),("PRESSURE",safe(summary.get("pressure_trend"),"--"))]
        cw=1170//5
        for i,(lab,val) in enumerate(cards):
            x=55+i*cw; draw.text((x+cw//2,477),lab,font=font(13,bold=True,mono=True),fill=c["muted"],anchor="mm"); draw.text((x+cw//2,522),val,font=font(25,bold=True),fill=c["text"],anchor="mm")
        draw.text((w//2,568),f"{summary.get('samples',0)} OBSERVATIONS • GOLD = TEMPERATURE • WHITE = PRESSURE",font=font(13,mono=True),fill=c["muted"],anchor="mm")

    def _draw_air_quality(self, draw, w, h, settings, p, c):
        aq=p.get("air_quality") or {}; loc=p.get("location") or {}
        self._header(draw,w,"AIR QUALITY",location_label(loc),c)
        badge=dict(p); badge["fetched_at"]=aq.get("fetched_at"); self._source_badge(draw,badge,c,settings,source=str(aq.get("source") or "AIR QUALITY"))
        aqi=aq.get("aqi"); category=str(aq.get("category") or "UNAVAILABLE")
        if aqi is None:
            draw.text((w//2,310),"AIR QUALITY TEMPORARILY UNAVAILABLE",font=font(38,bold=True),fill=c["accent"],anchor="mm"); return
        try: av=float(aqi)
        except Exception: av=0
        aqcol="#55b96b" if av<=50 else "#d7c83f" if av<=100 else "#e48b35" if av<=150 else "#cf4b54" if av<=200 else "#914da3" if av<=300 else "#7a303c"
        round_rect(draw,(72,142,540,510),24,c["panel2"],outline=aqcol,width=4)
        draw.text((306,194),"CURRENT US AQI",font=font(20,bold=True,mono=True),fill=c["muted"],anchor="mm")
        draw.ellipse((166,228,446,508),fill=aqcol,outline=c["text"],width=4)
        draw.text((306,333),f"{av:.0f}",font=font(92,bold=True),fill="#ffffff",anchor="mm")
        size=22 if len(category)<20 else 16
        draw.text((306,414),category,font=font(size,bold=True,mono=True),fill="#ffffff",anchor="mm")
        metrics=[("PRIMARY",str(aq.get("primary_pollutant") or "--")),("PM2.5",n(aq.get("pm2_5"),1," µg/m³")),("PM10",n(aq.get("pm10"),1," µg/m³")),("OZONE",n(aq.get("ozone"),1," µg/m³"))]
        for i,(lab,val) in enumerate(metrics):
            y=154+i*92; round_rect(draw,(590,y,1206,y+72),12,c["panel"],outline=c["panel2"],width=2); draw.text((612,y+15),lab,font=font(14,bold=True,mono=True),fill=c["muted"]); draw.text((1180,y+36),val,font=font(25,bold=True),fill=c["text"],anchor="rm")
        draw.text((w//2,557),"MODELED AIR-QUALITY GUIDANCE • NOT A SUBSTITUTE FOR LOCAL HEALTH ADVISORIES",font=font(12,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_local_rivers(self, draw, w, h, settings, p, c):
        river=p.get("rivers") or {}; gauges=river.get("gauges") or []; loc=p.get("location") or {}
        self._header(draw,w,"LOCAL RIVERS & STREAMS",location_label(loc),c)
        badge=dict(p); badge["fetched_at"]=river.get("fetched_at"); self._source_badge(draw,badge,c,settings,source="USGS WATER DATA")
        if not gauges:
            draw.text((w//2,302),"NO NEARBY USGS GAGE-HEIGHT DATA",font=font(38,bold=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,356),f"Search radius: {river.get('radius_miles',60)} miles",font=font(20),fill=c["text"],anchor="mm"); return
        primary=gauges[0]; history=river.get("primary_history") or []; trend="STEADY"; delta=None
        if len(history)>=2:
            delta=float(history[-1].get("value",0))-float(history[0].get("value",0)); trend="RISING" if delta>0.05 else "FALLING" if delta<-0.05 else "STEADY"
        round_rect(draw,(58,140,744,446),20,c["panel2"],outline=c["muted"],width=2)
        draw.text((82,165),str(primary.get("name") or "USGS Gauge")[:54].upper(),font=font(23,bold=True),fill=c["title"])
        draw.text((82,208),f"{primary.get('distance_miles',0):.1f} MI FROM {str(loc.get('name') or 'LOCAL').upper()} • {primary.get('number','')}",font=font(13,bold=True,mono=True),fill=c["muted"])
        draw.text((82,270),n(primary.get("gage_height_ft"),2," ft"),font=font(66,bold=True),fill=c["accent"])
        detail=trend if delta is None else f"{trend}  {delta:+.2f} FT / {int((settings.get('local_data') or {}).get('rivers',{}).get('trend_hours',6))}H"
        draw.text((82,350),detail,font=font(22,bold=True,mono=True),fill=c["text"])
        draw.text((82,395),f"STREAMFLOW  {n(primary.get('streamflow_cfs'),0,' cfs')}",font=font(20,bold=True),fill=c["text"])
        if len(history)>=2:
            box=(780,160,1206,430); round_rect(draw,box,15,c["panel"],outline=c["panel2"],width=2); vals=[float(x["value"]) for x in history if x.get("value") is not None]
            if vals:
                lo=min(vals)-0.05; hi=max(vals)+0.05; pts=[]
                for i,row in enumerate(history):
                    if row.get("value") is None: continue
                    x=box[0]+22+(box[2]-box[0]-44)*(i/max(1,len(history)-1)); y=box[3]-28-(float(row["value"])-lo)/max(0.1,hi-lo)*(box[3]-box[1]-56); pts.append((x,y))
                if len(pts)>1: draw.line(pts,fill=c["accent"],width=4,joint="curve")
                draw.text((box[0]+18,box[1]+14),"GAGE HEIGHT TREND",font=font(13,bold=True,mono=True),fill=c["muted"])
        for i,g in enumerate(gauges[1:3]):
            y=474+i*54; draw.text((72,y),str(g.get("name") or "USGS Gauge")[:54],font=font(16,bold=True),fill=c["text"]); draw.text((1190,y),f"{n(g.get('gage_height_ft'),2,' ft')}  •  {g.get('distance_miles',0):.0f} mi",font=font(15,bold=True,mono=True),fill=c["accent"],anchor="ra")
        draw.text((w//2,590),"USGS PROVISIONAL REAL-TIME DATA • FLOOD STAGE IS NOT INFERRED BY WEATHERSTREAM",font=font(11,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_climate_context(self, draw, w, h, settings, p, c):
        loc=p.get("location") or {}; climate=p.get("climate") or {}; daily=p.get("daily") or {}; loc_id=loc.get("id") or settings.get("primary_location_id")
        self._header(draw,w,"CLIMATE CONTEXT",location_label(loc),c)
        forecast_high=(daily.get("temperature_2m_max") or [None])[0]; forecast_low=(daily.get("temperature_2m_min") or [None])[0]
        if climate.get("normal_high_f") is not None or climate.get("normal_low_f") is not None:
            badge=dict(p); badge["fetched_at"]=climate.get("fetched_at"); self._source_badge(draw,badge,c,settings,source="NOAA NCEI • 1991–2020 NORMALS")
            nh=climate.get("normal_high_f"); nl=climate.get("normal_low_f")
            dh=(float(forecast_high)-float(nh)) if forecast_high is not None and nh is not None else None
            dl=(float(forecast_low)-float(nl)) if forecast_low is not None and nl is not None else None
            rows=[("FORECAST HIGH",n(forecast_high,0,"°"),"NORMAL HIGH",n(nh,0,"°"),dh),("FORECAST LOW",n(forecast_low,0,"°"),"NORMAL LOW",n(nl,0,"°"),dl)]
            for i,(lab1,val1,lab2,val2,delta) in enumerate(rows):
                y=150+i*166; round_rect(draw,(76,y,1204,y+138),18,c["panel2"],outline=c["muted"],width=2)
                draw.text((104,y+25),lab1,font=font(15,bold=True,mono=True),fill=c["muted"]); draw.text((104,y+62),val1,font=font(42,bold=True),fill=c["text"])
                draw.text((520,y+25),lab2,font=font(15,bold=True,mono=True),fill=c["muted"]); draw.text((520,y+62),val2,font=font(42,bold=True),fill=c["text"])
                dtext="--" if delta is None else f"{delta:+.0f}° VS NORMAL"
                draw.text((1148,y+68),dtext,font=font(24,bold=True,mono=True),fill=c["accent"],anchor="ra")
            draw.text((w//2,522),f"CLIMATE STATION  {climate.get('station_name') or climate.get('station_id')}",font=font(15,bold=True,mono=True),fill=c["muted"],anchor="mm")
        else:
            summary=self.history_store.summary(loc_id,24*30) if self.history_store and loc_id else {"samples":0}
            draw.text((w//2,154),"OFFICIAL CLIMATE STATION NOT CONFIGURED",font=font(26,bold=True,mono=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,196),"Add an NCEI 1991–2020 normals station ID in Admin to enable official normals.",font=font(18),fill=c["text"],anchor="mm")
            round_rect(draw,(100,244,1180,478),20,c["panel2"],outline=c["muted"],width=2)
            draw.text((w//2,278),"LOCAL 30-DAY CONTEXT",font=font(18,bold=True,mono=True),fill=c["muted"],anchor="mm")
            cards=[("RECENT HIGH",n(summary.get("high"),0,"°")),("RECENT LOW",n(summary.get("low"),0,"°")),("PEAK GUST",n(summary.get("max_gust"),0," mph")),("OBSERVATIONS",str(summary.get("samples",0)))]
            cell=1000//4
            for i,(lab,val) in enumerate(cards):
                x=140+i*cell; draw.text((x+cell//2,335),lab,font=font(13,bold=True,mono=True),fill=c["muted"],anchor="mm"); draw.text((x+cell//2,390),val,font=font(32,bold=True),fill=c["text"],anchor="mm")
            draw.text((w//2,520),"LOCAL HISTORY IS NOT AN OFFICIAL CLIMATE NORMAL OR RECORD",font=font(13,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_weather_history(self, draw, w, h, settings, p, c):
        self._header(draw,w,"24-HOUR WEATHER HISTORY",location_label(p.get("location") or {}),c)
        loc_id=(p.get("location") or {}).get("id") or settings.get("primary_location_id")
        rows=self.history_store.recent(loc_id,24) if self.history_store and loc_id else []
        summary=self.history_store.summary(loc_id,24) if self.history_store and loc_id else {"samples":0}
        if len(rows)<2:
            draw.text((w//2,310),"HISTORY IS BUILDING",font=font(42,bold=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,362),"WeatherStream stores a new local sample on each weather refresh.",font=font(22),fill=c["text"],anchor="mm"); return
        temps=[float(r["temperature"]) for r in rows if r.get("temperature") is not None]
        if not temps: return
        left,top,right,bottom=90,165,w-90,440; round_rect(draw,(left,top,right,bottom),18,c["panel2"],c["muted"],1)
        lo=min(temps)-2; hi=max(temps)+2
        pts=[]; usable=[r for r in rows if r.get("temperature") is not None]
        for i,r in enumerate(usable):
            x=left+24+(right-left-48)*(i/max(1,len(usable)-1)); y=bottom-28-(float(r["temperature"])-lo)/max(1,hi-lo)*(bottom-top-56); pts.append((x,y))
        if len(pts)>1: draw.line(pts,fill=c["accent"],width=5,joint="curve")
        for x,y in pts[::max(1,len(pts)//8)]: draw.ellipse((x-4,y-4,x+4,y+4),fill=c["text"])
        draw.text((left+18,top+16),f"{hi-2:.0f}°",font=font(16,mono=True),fill=c["muted"]); draw.text((left+18,bottom-34),f"{lo+2:.0f}°",font=font(16,mono=True),fill=c["muted"])
        cards=[("HIGH",n(summary.get("high"),0,"°")),("LOW",n(summary.get("low"),0,"°")),("MAX GUST",n(summary.get("max_gust"),0," mph")),("RAIN",n(summary.get("precipitation"),2,' in')), ("PRESSURE",safe(summary.get("pressure_trend"),"--"))]
        cw=(w-180)//5
        for i,(lab,val) in enumerate(cards):
            x=70+i*cw; draw.text((x+cw//2,490),lab,font=font(14,bold=True,mono=True),fill=c["muted"],anchor="mm"); draw.text((x+cw//2,532),val,font=font(24,bold=True),fill=c["text"],anchor="mm")
        draw.text((w//2,572),f"{summary.get('samples',0)} LOCAL OBSERVATIONS • STORED IN /config/weatherstream.db",font=font(13,mono=True),fill=c["muted"],anchor="mm")

    def _draw_spc_outlook(self, draw, w, h, settings, p, c):
        self._header(draw,w,"SPC SEVERE WEATHER OUTLOOK",location_label(p.get("location") or {}),c)
        outlook=settings.get("_spc_outlook") or {}
        risk_colors={"NONE":"#6d7b85","TSTM":"#4ca65b","MRGL":"#4e9e64","SLGT":"#d4c83a","ENH":"#e18a31","MDT":"#c94c58","HIGH":"#c05aa5"}
        if not outlook:
            draw.text((w//2,320),"SPC OUTLOOK UNAVAILABLE",font=font(38,bold=True),fill=c["accent"],anchor="mm"); draw.text((w//2,370),"WeatherStream will retry the NOAA outlook service automatically.",font=font(20),fill=c["text"],anchor="mm"); return
        for i,key in enumerate(("day1","day2","day3")):
            data=outlook.get(key) or {"risk":"NONE","name":"No Categorical Risk","rank":0}; x=90+i*380
            round_rect(draw,(x,170,x+340,455),20,c["panel2"],c["muted"],2)
            draw.text((x+170,205),key.upper().replace("DAY","DAY "),font=font(21,bold=True,mono=True),fill=c["muted"],anchor="mm")
            risk=data.get("risk","NONE"); col=risk_colors.get(risk,c["muted"])
            draw.ellipse((x+110,245,x+230,365),fill=col,outline=c["text"],width=3)
            draw.text((x+170,305),risk,font=font(31,bold=True),fill="#ffffff",anchor="mm")
            draw.text((x+170,405),data.get("name","No Risk"),font=font(21,bold=True),fill=c["text"],anchor="mm")
        d1=outlook.get("day1") or {}; draw.text((w//2,500),f"LOCAL DAY 1 CATEGORY: {d1.get('name','No Categorical Risk').upper()}",font=font(25,bold=True),fill=c["accent"],anchor="mm")
        draw.text((w//2,548),"NOAA / NWS STORM PREDICTION CENTER • CATEGORICAL OUTLOOK",font=font(15,mono=True),fill=c["muted"],anchor="mm")
        draw.text((w//2,575),"OUTLOOK GUIDANCE IS NOT A WARNING • ACTIVE NWS ALERTS TAKE PRIORITY",font=font(13,mono=True),fill=c["muted"],anchor="mm")

    def _draw_tropical_update(self, draw, w, h, settings, p, c):
        tropical=settings.get("_tropical") or {}; systems=tropical.get("systems") or []; outlook=tropical.get("outlook") or {}
        self._header(draw,w,"Tropical Weather Update","Atlantic • Caribbean • Gulf",c)
        if systems:
            draw.text((72,142),f"{len(systems)} ACTIVE ATLANTIC SYSTEM{'S' if len(systems)!=1 else ''}",font=font(27,bold=True,mono=True),fill=c["accent"])
            for idx,storm in enumerate(systems[:3]):
                x=70+idx*400; round_rect(draw,(x,196,x+370,442),16,c["panel2"],outline=c["muted"],width=2)
                draw.text((x+185,238),str(storm.get("name") or "SYSTEM").upper(),font=font(31,bold=True),fill=c["title"],anchor="mm")
                draw.text((x+185,284),str(storm.get("classification_name") or "Tropical Cyclone").upper(),font=font(16,bold=True,mono=True),fill=c["accent"],anchor="mm")
                draw.text((x+28,337),f"WIND  {safe(storm.get('intensity_mph'))} MPH",font=font(22,bold=True,mono=True),fill=c["text"])
                draw.text((x+28,380),f"PRESSURE  {safe(storm.get('pressure_mb'))} MB",font=font(19,mono=True),fill=c["muted"])
                draw.text((x+28,415),f"MOVEMENT  {safe(storm.get('movement_degrees'))}° AT {safe(storm.get('movement_mph'))} MPH",font=font(15,mono=True),fill=c["muted"])
        else:
            round_rect(draw,(70,160,w-70,390),18,c["panel2"],outline=c["muted"],width=2)
            draw.text((w//2,225),"NO ACTIVE ATLANTIC TROPICAL CYCLONES",font=font(34,bold=True),fill=c["accent"],anchor="mm")
            risk=int(outlook.get("development_max") or 0)
            draw.text((w//2,294),f"HIGHEST OUTLOOK DEVELOPMENT CHANCE  {risk}%",font=font(23,bold=True,mono=True),fill=c["text"],anchor="mm")
            draw.text((w//2,346),"WeatherStream continues monitoring official NHC feeds.",font=font(19),fill=c["muted"],anchor="mm")
        text=str(outlook.get("text") or "Official Atlantic outlook will appear after the next NHC refresh.")
        summary=" ".join(textwrap.wrap(text,92)[:2])
        for row,line in enumerate(textwrap.wrap(summary,105)[:2]): draw.text((w//2,500+row*31),line,font=font(17),fill=c["text"],anchor="mm")
        draw.text((w//2,592),"SOURCE: NOAA NATIONAL HURRICANE CENTER • CHECK OFFICIAL LOCAL INSTRUCTIONS",font=font(13,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_tropical_systems(self, draw, w, h, settings, p, c):
        tropical=settings.get("_tropical") or {}; systems=tropical.get("systems") or []
        self._header(draw,w,"Active Tropical Systems","Official NHC Status",c)
        if not systems:
            draw.text((w//2,310),"NO ACTIVE ATLANTIC SYSTEMS",font=font(44,bold=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,375),"The Tropics Watch channel remains ready on demand.",font=font(22),fill=c["text"],anchor="mm"); return
        for idx,storm in enumerate(systems[:4]):
            y=132+idx*113; round_rect(draw,(64,y,w-64,y+94),13,c["panel2"],outline=c["muted"],width=2)
            draw.text((88,y+18),str(storm.get("name") or "SYSTEM").upper(),font=font(28,bold=True),fill=c["title"])
            draw.text((395,y+22),str(storm.get("classification_name") or "CYCLONE").upper(),font=font(16,bold=True,mono=True),fill=c["accent"])
            draw.text((88,y+61),f"{safe(storm.get('latitude'))}°, {safe(storm.get('longitude'))}°",font=font(16,mono=True),fill=c["muted"])
            draw.text((w-88,y+25),f"{safe(storm.get('intensity_mph'))} MPH",font=font(30,bold=True,mono=True),fill=c["text"],anchor="ra")
            draw.text((w-88,y+65),f"{safe(storm.get('pressure_mb'))} MB  •  MOVING {safe(storm.get('movement_degrees'))}° / {safe(storm.get('movement_mph'))} MPH",font=font(14,mono=True),fill=c["muted"],anchor="ra")

    def _draw_tropical_track(self, draw, w, h, settings, p, c, now):
        tropical=settings.get("_tropical") or {}; systems=tropical.get("systems") or []
        storm=systems[int(now//15)%len(systems)] if systems else None
        self._header(draw,w,"Tropical Forecast Track",str((storm or {}).get("name") or "Atlantic Basin"),c)
        box=(65,120,w-65,h-105); round_rect(draw,box,15,"#08233a",outline=c["muted"],width=2)
        x1,y1,x2,y2=box
        def project(lat,lon): return (x1+(float(lon)+105.0)/60.0*(x2-x1),y2-(float(lat)-5.0)/40.0*(y2-y1))
        for lon in range(-100,-44,10):
            x,_=project(5,lon); draw.line((x,y1,x,y2),fill="#17415a",width=1); draw.text((x,y2-15),f"{abs(lon)}W",font=font(11,mono=True),fill=c["muted"],anchor="ms")
        for lat in range(10,46,10):
            _,y=project(lat,-105); draw.line((x1,y,x2,y),fill="#17415a",width=1); draw.text((x1+5,y),f"{lat}N",font=font(11,mono=True),fill=c["muted"],anchor="lm")
        gulf=[project(lat,lon) for lat,lon in [(18,-98.8),(31.8,-98.8),(31.8,-79),(18,-79),(18,-98.8)]]
        draw.line(gulf,fill="#d0a42e",width=2); draw.text(project(24,-89),"GULF",font=font(14,bold=True,mono=True),fill="#f4c84b",anchor="mm")
        coast=[project(lat,lon) for lat,lon in [(25,-97),(29,-96),(30,-90),(30,-85),(26,-82),(25,-80),(31,-80),(35,-76),(41,-70)]]
        draw.line(coast,fill="#8fb6c8",width=3)
        if not storm:
            draw.text((w//2,330),"NO ACTIVE NHC FORECAST TRACK",font=font(37,bold=True),fill=c["accent"],anchor="mm"); return
        points=list(storm.get("track") or [])
        if storm.get("latitude") is not None and storm.get("longitude") is not None: points.insert(0,[storm["latitude"],storm["longitude"]])
        plotted=[project(lat,lon) for lat,lon in points if 5<=float(lat)<=45 and -105<=float(lon)<=-45]
        if len(plotted)>1: draw.line(plotted,fill=c["accent"],width=5,joint="curve")
        for idx,(x,y) in enumerate(plotted):
            r=9 if idx==0 else 6; draw.ellipse((x-r,y-r,x+r,y+r),fill="#ffdc4a" if idx==0 else "#ffffff",outline="#081520",width=2)
        loc=(p or {}).get("location") or {}
        try:
            lx,ly=project(float(loc["latitude"]),float(loc["longitude"])); draw.rectangle((lx-6,ly-6,lx+6,ly+6),fill="#55e7ff",outline="#ffffff"); draw.text((lx+11,ly-8),str(loc.get("name") or "LOCAL").upper(),font=font(12,bold=True,mono=True),fill="#ffffff")
        except Exception: pass
        draw.text((w//2,h-117),"TRACK POINTS ARE OFFICIAL NHC FORECAST POSITIONS • IMPACTS CAN OCCUR OUTSIDE THE TRACK",font=font(12,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_tropical_local(self, draw, w, h, settings, p, c):
        tropical=settings.get("_tropical") or {}; activation=tropical.get("activation") or {}; loc=(p or {}).get("location") or {}
        self._header(draw,w,"Local Tropical Impact Monitor",location_label(loc),c)
        active=bool(activation.get("active")); color="#ffbf3f" if active else c["accent"]
        draw.text((w//2,155),"TROPICS WATCH ACTIVE" if active else "NO LOCAL/GULF ACTIVATION TRIGGER",font=font(34,bold=True),fill=color,anchor="mm")
        nearest=activation.get("nearest") or {}
        if nearest:
            round_rect(draw,(75,202,530,352),16,c["panel2"],outline=c["muted"],width=2)
            draw.text((102,225),"NEAREST FORECAST/CENTER POINT",font=font(15,bold=True,mono=True),fill=c["muted"])
            draw.text((102,270),str(nearest.get("name") or "SYSTEM").upper(),font=font(31,bold=True),fill=c["title"])
            draw.text((102,316),f"ABOUT {safe(nearest.get('distance_miles'))} MILES",font=font(24,bold=True,mono=True),fill=c["accent"])
        reasons=activation.get("reasons") or ["No official NHC/NWS Gulf or local proximity trigger is active."]
        round_rect(draw,(560,202,w-75,470),16,c["panel2"],outline=c["muted"],width=2)
        draw.text((590,226),"ACTIVATION BASIS",font=font(16,bold=True,mono=True),fill=c["muted"])
        y=270
        for reason in reasons[:4]:
            for line in textwrap.wrap(str(reason),50)[:2]: draw.text((600,y),line,font=font(18,bold=True),fill=c["text"]); y+=28
            y+=10
        draw.text((76,515),"LOCAL WATCHES/WARNINGS COME FROM THE NATIONAL WEATHER SERVICE",font=font(18,bold=True,mono=True),fill=c["accent"])
        draw.text((76,558),"Follow evacuation and protective-action instructions from local officials.",font=font(20),fill=c["text"])
        draw.text((76,591),"The forecast track shows center positions, not the full size of hazardous impacts.",font=font(16),fill=c["muted"])

    def _draw_event_summary(self, draw, w, h, settings, snapshot, p, c):
        status=settings.get("_event") or {}; event_type=str(status.get("event_type") or settings.get("_channel_mode","").removeprefix("event_"))
        definition=EVENT_TYPES.get(event_type) or {}; identity_row=event_identity(settings) or {}
        title=str(identity_row.get("name") or definition.get("name") or "Weather Event")
        self._header(draw,w,title,"DEDICATED RWN EVENT CHANNEL",c)
        alerts=status.get("alerts") or snapshot.get("alerts") or []; active=bool(status.get("active")); cooling=bool(status.get("cooldown_active"))
        state="ACTIVE OFFICIAL ALERTS" if active else "POST-EVENT MONITORING" if cooling else "STANDING BY"
        color=str(c.get("accent") or definition.get("color") or "#ff3344")
        event_for_icon={"tornado":"Tornado Warning","flood":"Flash Flood Warning","winter":"Winter Storm Warning","wildfire":"Red Flag Warning","heat":"Excessive Heat Warning"}.get(event_type,title)
        target=getattr(draw,"_image",None)
        if target is not None: paste_rwn_icon(target,rwn_alert_icon(event_for_icon,118),74,132)
        round_rect(draw,(215,126,w-64,226),16,c["panel2"],outline=color,width=4)
        draw.text((245,146),state,font=font(29,bold=True,mono=True),fill=color)
        draw.text((245,184),str(identity_row.get("slug") or "OFFICIAL ALERT MONITORING"),font=font(15,bold=True,mono=True),fill=c["muted"])

        if alerts:
            alert=alerts[0]
            round_rect(draw,(64,248,w-64,386),16,c["panel"],outline=color,width=2)
            draw.text((88,266),str(alert.get("event") or title).upper()[:72],font=font(29,bold=True),fill=c["title"])
            area=str(alert.get("areaDesc") or "Local service area")
            draw.text((88,309),area[:100],font=font(18,bold=True),fill=c["text"])
            headline=str(alert.get("headline") or alert.get("description") or "Official National Weather Service alert is active.").replace("\n"," ")
            yy=342
            for line in textwrap.wrap(headline,width=102)[:2]: draw.text((88,yy),line,font=font(16),fill=c["muted"]); yy+=23
        else:
            round_rect(draw,(64,248,w-64,386),16,c["panel"],outline=c["panel2"],width=2)
            draw.text((w//2,297),f"No active {title.lower()} alert is currently matched.",font=font(25,bold=True),fill=c["text"],anchor="mm")
            draw.text((w//2,341),"RWN remains in event-channel monitoring mode through the configured cooldown.",font=font(16),fill=c["muted"],anchor="mm")

        cur=p.get("current") or {}; hourly=p.get("hourly") or {}; aq=p.get("air_quality") or {}; rivers=p.get("rivers") or {}
        rain_prob=self._max_next(hourly,"precipitation_probability",12); rain_24=self._sum_next(hourly,"precipitation",24)
        temp=cur.get("temperature_2m"); feels=cur.get("apparent_temperature"); gust=cur.get("wind_gusts_10m"); rh=cur.get("relative_humidity_2m")
        spc=(settings.get("_spc_outlook") or {}).get("day1") or {}
        if event_type=="tornado":
            metrics=[("SPC DAY 1",str(spc.get("name") or "--").upper()),("PEAK GUST",n(gust,0," mph")),("RAIN NEXT 12H",f"{rain_prob:.0f}%")]
        elif event_type=="flood":
            metrics=[("24H MODEL RAIN",f"{rain_24:.2f} in"),("RAIN NEXT 12H",f"{rain_prob:.0f}%"),("USGS GAUGES",str(len(rivers.get("gauges") or [])))]
        elif event_type=="winter":
            metrics=[("TEMPERATURE",n(temp,0,"°")),("FEELS LIKE",n(feels,0,"°")),("PRECIP CHANCE",f"{rain_prob:.0f}%")]
        elif event_type=="heat":
            metrics=[("TEMPERATURE",n(temp,0,"°")),("FEELS LIKE",n(feels,0,"°")),("HUMIDITY",n(rh,0,"%"))]
        elif event_type=="wildfire":
            metrics=[("HUMIDITY",n(rh,0,"%")),("PEAK GUST",n(gust,0," mph")),("AIR QUALITY",f"AQI {safe(aq.get('aqi'))}")]
        else:
            metrics=[("TEMPERATURE",n(temp,0,"°")),("WIND",n(cur.get("wind_speed_10m"),0," mph")),("RAIN",f"{rain_prob:.0f}%")]
        cell=(w-128)//3
        for i,(label,value) in enumerate(metrics):
            x1=64+i*cell; x2=64+(i+1)*cell-10
            round_rect(draw,(x1,414,x2,535),14,c["panel2"],outline=c.get("accent2",c["accent"]),width=2)
            draw.text((x1+20,434),label,font=font(13,bold=True,mono=True),fill=c["muted"])
            draw.text((x1+20,472),value,font=font(28,bold=True),fill=c["text"])
        draw.text((w//2,584),"SOURCE: NOAA / NATIONAL WEATHER SERVICE • FOLLOW LOCAL OFFICIAL INSTRUCTIONS",font=font(12,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_goes_product(self, img, draw, w, h, settings, c, product):
        title="GOES-19 GeoColor Satellite" if product=="satellite" else "GOES-19 Lightning Mapper"
        image=self.imagery_manager.snapshot(product) if self.imagery_manager else None
        box=(45,108,w-45,h-94); round_rect(draw,(37,100,w-37,h-86),12,c["panel2"],outline=c["muted"],width=2)
        if image:
            target_w,target_h=box[2]-box[0],box[3]-box[1]; image.thumbnail((target_w,target_h),Image.Resampling.LANCZOS)
            canvas=Image.new("RGB",(target_w,target_h),"#061523"); canvas.paste(image,((target_w-image.width)//2,(target_h-image.height)//2)); img.paste(canvas,(box[0],box[1]))
        else:
            draw.text((w//2,h//2),"OFFICIAL NOAA IMAGERY TEMPORARILY UNAVAILABLE",font=font(30,bold=True),fill=c["accent"],anchor="mm")
        draw=ImageDraw.Draw(img); draw.rectangle((45,108,w-45,172),fill="#071a2ce6")
        draw.text((70,121),title,font=font(30,bold=True),fill="#ffffff"); draw.text((w-70,132),"NOAA NESDIS / STAR",font=font(14,bold=True,mono=True),fill=c["accent"],anchor="ra")

    def _map_engine_config(self, settings):
        maps=settings.get("maps") or {}
        return maps.get("engine3") or maps.get("engine2") or {}

    def _draw_map_engine_badge(self, draw, w, settings, c):
        layers=(self._map_engine_config(settings).get("layers") or {})
        active=" • ".join(key.upper().replace("_"," ") for key,value in layers.items() if value)
        draw.rectangle((55,112,w-55,150),fill="#061827")
        draw.text((72,121),"MAP ENGINE 3.0",font=font(17,bold=True,mono=True),fill=c["accent"])
        draw.text((w-72,122),active[:92],font=font(12,bold=True,mono=True),fill="#ffffff",anchor="ra")

    def _draw_map_engine(self, img, draw, w, h, settings, snapshot, p, c, now, progress):
        layers=(self._map_engine_config(settings).get("layers") or {})
        scoped=copy.deepcopy(settings)
        scoped.setdefault("maps",{})["auto_city_labels"]=bool(layers.get("city_labels",True))
        scoped.setdefault("alerts",{})["show_polygons"]=bool(layers.get("alerts",True))
        if layers.get("radar",True): self._draw_radar(img,draw,w,h,scoped,p,c,now,progress,view="regional",snapshot=snapshot)
        else: self._draw_regional_map(img,draw,w,h,scoped,snapshot,p,c)
        draw=ImageDraw.Draw(img); map_box=(58,116,w-58,h-112); loc=p.get("location") or {}; zoom=int((((settings.get("radar") or {}).get("views") or {}).get("regional") or {}).get("zoom",6))
        if layers.get("tropical_tracks",True):
            for storm in ((settings.get("_tropical") or {}).get("systems") or [])[:4]:
                points=list(storm.get("track") or [])
                if storm.get("latitude") is not None and storm.get("longitude") is not None: points.insert(0,[storm["latitude"],storm["longitude"]])
                plotted=[]
                for lat,lon in points:
                    try:
                        x,y=self._project_to_map(lat,lon,loc["latitude"],loc["longitude"],zoom,map_box)
                        if map_box[0]<=x<=map_box[2] and map_box[1]<=y<=map_box[3]: plotted.append((x,y))
                    except Exception: pass
                if len(plotted)>1: draw.line(plotted,fill="#ffda44",width=5,joint="curve")
                for x,y in plotted: draw.ellipse((x-5,y-5,x+5,y+5),fill="#ffda44",outline="#071820",width=2)
        self._draw_map_engine_badge(draw,w,settings,c)

    def _draw_official_map_product(self, img, draw, w, h, settings, p, c, product, title, subtitle):
        self._header(draw,w,title,location_label(p.get("location") or {}),c)
        loc=p.get("location") or {}; lid=loc.get("id") or settings.get("primary_location_id")
        row=self.radar_manager.official_product(product, location_id=lid, copy_image=False) if self.radar_manager and hasattr(self.radar_manager,"official_product") else {}
        overlay=row.get("image"); view=row.get("view") or "wide"
        box=(48,126,w-48,h-110); target_w,target_h=box[2]-box[0],box[3]-box[1]
        base=self.radar_manager.resized_map(view,target_w,target_h,location_id=lid) if self.radar_manager and hasattr(self.radar_manager,"resized_map") else None
        round_rect(draw,(40,118,w-40,h-102),12,c["panel2"],outline=c["muted"],width=2)
        if base is not None:
            img.paste(base,(box[0],box[1]))
        else:
            draw.rectangle(box,fill="#102538")
        if overlay is not None:
            ov=overlay.resize((target_w,target_h),Image.Resampling.LANCZOS)
            img.paste(ov,(box[0],box[1]),ov)
        else:
            draw.text((w//2,h//2),"OFFICIAL NOAA MAP LAYER TEMPORARILY UNAVAILABLE",font=font(27,bold=True),fill=c["accent"],anchor="mm")
        draw=ImageDraw.Draw(img)
        draw.rectangle((box[0]+14,box[1]+14,box[0]+620,box[1]+66),fill="#071723")
        draw.text((box[0]+28,box[1]+25),subtitle,font=font(17,bold=True,mono=True),fill="#ffffff")
        if product=="spc_day1":
            d1=(settings.get("_spc_outlook") or {}).get("day1") or {}
            risk=str(d1.get("risk") or "NONE"); risk_colors={"NONE":"#6d7b85","TSTM":"#4ca65b","MRGL":"#4e9e64","SLGT":"#d4c83a","ENH":"#e18a31","MDT":"#c94c58","HIGH":"#c05aa5"}
            color=risk_colors.get(risk,"#6d7b85")
            draw.rectangle((box[2]-300,box[1]+14,box[2]-14,box[1]+82),fill="#071723",outline=color,width=3)
            draw.text((box[2]-282,box[1]+25),"LOCAL DAY 1 RISK",font=font(12,bold=True,mono=True),fill="#ffffff")
            draw.text((box[2]-282,box[1]+47),str(d1.get("name") or "No Categorical Risk").upper(),font=font(18,bold=True),fill=color)
            labels=[("TSTM","#4ca65b"),("MRGL","#4e9e64"),("SLGT","#d4c83a"),("ENH","#e18a31"),("MDT","#c94c58"),("HIGH","#c05aa5")]
            lx=box[0]+20; ly=box[3]-44
            draw.rectangle((box[0]+10,ly-12,box[0]+550,box[3]-8),fill="#071723")
            for label,col in labels:
                draw.rectangle((lx,ly,lx+19,ly+14),fill=col); draw.text((lx+25,ly-2),label,font=font(11,bold=True,mono=True),fill="#ffffff"); lx+=82
        elif product=="wpc_surface":
            draw.rectangle((box[0]+14,box[3]-48,box[0]+630,box[3]-14),fill="#071723")
            draw.text((box[0]+28,box[3]-40),"H/L • COLD/WARM/STATIONARY FRONTS • RAIN • SNOW • SIGNIFICANT WEATHER",font=font(12,bold=True,mono=True),fill="#ffffff")
        elif product=="wpc_qpf_day1":
            draw.rectangle((box[0]+14,box[3]-48,box[0]+600,box[3]-14),fill="#071723")
            draw.text((box[0]+28,box[3]-40),"COLOR SHADING = FORECAST LIQUID PRECIPITATION THROUGH DAY 1",font=font(12,bold=True,mono=True),fill="#ffffff")
        status="CACHED" if row.get("last_error") and overlay is not None else "CURRENT" if overlay is not None else "UNAVAILABLE"
        draw.text((w-62,h-101),f"{row.get('source') or 'NOAA'} • {status}",font=font(11,bold=True,mono=True),fill=c["muted"],anchor="ra")

    def _draw_spc_hazard_probabilities(self, img, draw, w, h, settings, p, c):
        self._header(draw,w,"SPC DAY 1 HAZARD PROBABILITIES",location_label(p.get("location") or {}),c)
        loc=p.get("location") or {}; lid=loc.get("id") or settings.get("primary_location_id")
        products=[("spc_tornado","TORNADO"),("spc_hail","HAIL"),("spc_wind","DAMAGING WIND")]
        panel_w=372; panel_h=405; start_x=52; gap=28; y=150
        for idx,(product,label) in enumerate(products):
            x=start_x+idx*(panel_w+gap)
            round_rect(draw,(x,y,x+panel_w,y+panel_h),15,c["panel2"],outline=c["muted"],width=2)
            row=self.radar_manager.official_product(product,location_id=lid,copy_image=False) if self.radar_manager and hasattr(self.radar_manager,"official_product") else {}
            view=row.get("view") or "wide"; overlay=row.get("image")
            map_box=(x+10,y+58,x+panel_w-10,y+panel_h-42); mw,mh=map_box[2]-map_box[0],map_box[3]-map_box[1]
            base=self.radar_manager.resized_map(view,mw,mh,location_id=lid) if self.radar_manager and hasattr(self.radar_manager,"resized_map") else None
            if base is not None: img.paste(base,(map_box[0],map_box[1]))
            else: draw.rectangle(map_box,fill="#102538")
            if overlay is not None:
                ov=overlay.resize((mw,mh),Image.Resampling.LANCZOS); img.paste(ov,(map_box[0],map_box[1]),ov)
            draw=ImageDraw.Draw(img)
            draw.text((x+panel_w//2,y+30),label,font=font(20,bold=True,mono=True),fill=c["accent"],anchor="mm")
            status="CURRENT" if overlay is not None and not row.get("last_error") else "CACHED" if overlay is not None else "UNAVAILABLE"
            draw.text((x+panel_w//2,y+panel_h-21),status,font=font(11,bold=True,mono=True),fill=c["muted"],anchor="mm")
        draw.text((w//2,585),"SPC PROBABILISTIC GUIDANCE • PERCENT CONTOURS ARE NOT WARNINGS",font=font(13,bold=True,mono=True),fill=c["muted"],anchor="mm")

    def _draw_hazard_map(self, img, draw, w, h, settings, snapshot, p, c, now, progress):
        scoped=copy.deepcopy(settings)
        scoped.setdefault("alerts",{})["show_polygons"]=True
        self._draw_radar(img,draw,w,h,scoped,p,c,now,progress,view="regional",snapshot=snapshot,alert_mode=False)
        draw=ImageDraw.Draw(img)
        self._header(draw,w,"SEVERE WEATHER HAZARD MAP",location_label(p.get("location") or {}),c)
        alerts=(snapshot or {}).get("alerts") or []
        if alerts:
            x=62; y=120
            draw.rectangle((x,y,x+455,y+54+min(3,len(alerts))*36),fill="#071723")
            draw.text((x+16,y+12),"ACTIVE WARNING POLYGONS",font=font(15,bold=True,mono=True),fill="#ffffff")
            for i,a in enumerate(alerts[:3]):
                event=str(a.get("event") or "Weather Alert").upper(); col=self._alert_color(event)
                draw.rectangle((x+16,y+48+i*36,x+30,y+62+i*36),fill=col)
                draw.text((x+40,y+44+i*36),event[:42],font=font(13,bold=True,mono=True),fill="#ffffff")
        d1=(settings.get("_spc_outlook") or {}).get("day1") or {}
        draw.rectangle((w-360,120,w-62,177),fill="#071723")
        draw.text((w-344,131),"SPC DAY 1",font=font(12,bold=True,mono=True),fill="#ffffff")
        draw.text((w-344,151),str(d1.get("name") or "No Categorical Risk").upper(),font=font(16,bold=True),fill=c["accent"])

    def _draw_studio_bumper(self, draw, w, h, settings, bumper_id, c):
        item=bumper(settings,bumper_id) or {"title":"ROLLER WEATHER NETWORK","subtitle":"WEATHER UPDATE","accent":c["accent"]}
        accent=str(item.get("accent") or c["accent"]); draw.rectangle((0,0,w,18),fill=accent); draw.rectangle((0,h-104,w,h-86),fill=accent)
        draw.text((w//2,245),str(item.get("title") or "ROLLER WEATHER NETWORK").upper()[:48],font=font(54,bold=True),fill=c["title"],anchor="mm")
        draw.line((250,310,w-250,310),fill=accent,width=4)
        draw.text((w//2,375),str(item.get("subtitle") or "WEATHER UPDATE").upper()[:64],font=font(28,bold=True,mono=True),fill=accent,anchor="mm")
        draw.text((w//2,475),str(settings.get("service_area") or "LOCAL • REGIONAL • ALWAYS READY").upper(),font=font(19,mono=True),fill=c["muted"],anchor="mm")

    def _draw_seven_day(self, draw, w, h, settings, p, c):
        self._header(draw, w, "7-Day Outlook", location_label(p["location"]), c)
        self._source_badge(draw, p, c, settings)
        daily=p.get("daily",{}); dates=daily.get("time") or []; count=min(7,len(dates))
        if not count:
            draw.text((w//2,h//2),"Extended forecast unavailable",font=font(34,bold=True),fill=c["text"],anchor="mm"); return
        highs=(daily.get("temperature_2m_max") or [None]*count)[:count]
        lows=(daily.get("temperature_2m_min") or [None]*count)[:count]
        pops=(daily.get("precipitation_probability_max") or [None]*count)[:count]
        codes=(daily.get("weather_code") or [None]*count)[:count]
        round_rect(draw,(48,132,1232,604),18,c["panel2"],outline=c["muted"],width=1)
        left,right=105,1150; step=(right-left)/max(1,count-1)
        xs=[int(left+i*step) for i in range(count)]
        vals=[]
        for x in highs+lows:
            try: vals.append(float(x))
            except Exception: pass
        lo=min(vals) if vals else 0; hi=max(vals) if vals else 1
        if hi-lo<8: lo-=4; hi+=4
        high_pts=[]; low_pts=[]
        for i in range(count):
            try: hv=float(highs[i]); hy=int(288-(hv-lo)/max(1,hi-lo)*112)
            except Exception: hy=230
            try: lv=float(lows[i]); ly=int(288-(lv-lo)/max(1,hi-lo)*112)
            except Exception: ly=270
            high_pts.append((xs[i],hy)); low_pts.append((xs[i],ly))
        if count>1:
            draw.line(high_pts,fill=c["accent"],width=5,joint="curve")
            draw.line(low_pts,fill=c["muted"],width=3,joint="curve")
        for i,x in enumerate(xs):
            try: day=dt.date.fromisoformat(dates[i]).strftime("%a").upper()
            except Exception: day="DAY"
            draw.text((x,154),day,font=font(18,bold=True,mono=True),fill=c["accent"],anchor="mm")
            draw.ellipse((x-6,high_pts[i][1]-6,x+6,high_pts[i][1]+6),fill=c["accent"])
            draw.text((x,high_pts[i][1]-18),n(highs[i],0,"°"),font=font(18,bold=True),fill=c["text"],anchor="mm")
            draw.ellipse((x-5,low_pts[i][1]-5,x+5,low_pts[i][1]+5),fill=c["muted"])
            draw.text((x,low_pts[i][1]+18),n(lows[i],0,"°"),font=font(15,bold=True),fill=c["muted"],anchor="mm")
            draw_weather_icon(draw,codes[i],x-34,322,0.38,c,icon_cfg=settings.get("icon_system"))
            try: pv=max(0,min(100,float(pops[i])))
            except Exception: pv=0
            bh=int(72*pv/100)
            draw.rounded_rectangle((x-24,518-bh,x+24,518),radius=7,fill=c["accent"] if pv>=50 else c["panel"])
            draw.text((x,541),f"{int(pv)}%",font=font(13,bold=True,mono=True),fill=c["text"],anchor="mm")
            if pv>=60:
                draw.text((x,574),"WET",font=font(11,bold=True,mono=True),fill=c["accent"],anchor="mm")
        draw.text((75,194),"HIGH",font=font(12,bold=True,mono=True),fill=c["accent"])
        draw.text((75,272),"LOW",font=font(12,bold=True,mono=True),fill=c["muted"])
        draw.text((75,520),"RAIN",font=font(12,bold=True,mono=True),fill=c["muted"])

    def _draw_regional(self, draw, w, h, settings, snapshot, primary, c):
        self._header(draw, w, "Regional Conditions", "LOCAL AREA", c)
        items = list(snapshot.get("locations", {}).values())[:8]
        if not items:
            return
        cols = 2
        card_w = 540
        card_h = 96
        start_x = 70
        start_y = 132
        for idx, item in enumerate(items):
            row, col = divmod(idx, cols)
            x = start_x + col*600
            y = start_y + row*112
            cur = item.get("current", {})
            loc = item.get("location", {})
            round_rect(draw, (x, y, x+card_w, y+card_h), 12, c["panel"])
            draw.text((x+20, y+16), loc.get("name", loc.get("postal_code", "")), font=font(29, bold=True), fill=c["text"])
            draw.text((x+20, y+56), cur.get("description", "Unavailable"), font=font(18), fill=c["muted"])
            draw.text((x+card_w-24, y+14), n(cur.get("temperature_2m"), 0, "°"), font=font(52, bold=True), fill=c["accent"], anchor="ra")

    def _draw_radar(self, img, draw, w, h, settings, p, c, now, progress, view="local", snapshot=None, alert_mode=False):
        loc = p["location"]
        view_titles = {"local": "Local Radar", "regional": "Regional Radar", "wide": "Wide Area Radar"}
        view_tags = {"local": "LOCAL", "regional": "REGIONAL", "wide": "WIDE AREA"}
        if alert_mode:
            alert = ((snapshot or {}).get("alerts") or [{}])[0]
            self._header(draw, w, "Severe Weather Radar", alert.get("event", "WEATHER ALERT").upper(), c)
        else:
            self._header(draw, w, view_titles.get(view, "Local Radar"), location_label(loc), c)
        location_id=str(loc.get("id") or settings.get("_render_location_id") or "")
        radar = self.radar_manager.snapshot(view, copy_images=False, location_id=location_id) if self.radar_manager else {"frames": [], "last_error": "Radar manager unavailable"}
        frames = radar.get("frames") or []
        radar_cfg = settings.get("radar", {})
        view_cfg = (radar_cfg.get("views") or {}).get(view) or {}
        map_box = (58, 116, w-58, h-112)

        round_rect(draw, (map_box[0]-8, map_box[1]-8, map_box[2]+8, map_box[3]+8), 14, c["panel2"], outline=c["muted"], width=2)
        if frames:
            frame_seconds = float(radar_cfg.get("frame_seconds", 0.8))
            idx = int(now / max(0.25, frame_seconds)) % len(frames)
            frame = frames[idx]
            target_w, target_h = map_box[2]-map_box[0], map_box[3]-map_box[1]
            radar_img = self.radar_manager.resized_frame(view, frame, target_w, target_h) if self.radar_manager and hasattr(self.radar_manager, "resized_frame") else frame["image"].resize((target_w, target_h), Image.Resampling.LANCZOS)
            img.paste(radar_img, (map_box[0], map_box[1]))
            draw = ImageDraw.Draw(img)

            # Optional visual sweep is deliberately a presentation effect; radar data remains the RainViewer frame.
            if radar_cfg.get("sweep_enabled", True):
                self._draw_radar_sweep(img, map_box, now, float(radar_cfg.get("sweep_seconds", 6.0)))
                draw = ImageDraw.Draw(img)

            if radar_cfg.get("show_range_rings", True):
                self._draw_range_rings(draw, map_box, p, int(view_cfg.get("zoom", {"local":7,"regional":6,"wide":5}.get(view,7))), radar_cfg, c)
            if (settings.get("maps") or {}).get("auto_city_labels", True):
                self._draw_auto_city_labels(draw, map_box, p, int(view_cfg.get("zoom", {"local":7,"regional":6,"wide":5}.get(view,7))), settings, c, compact=True)
            if (settings.get("alerts", {}) or {}).get("show_polygons", True) and (snapshot or {}).get("alerts"):
                self._draw_alert_polygons(img, draw, map_box, p, int(view_cfg.get("zoom", {"local":7,"regional":6,"wide":5}.get(view,7))), (snapshot or {}).get("alerts") or [])
            if alert_mode:
                alert = ((snapshot or {}).get("alerts") or [{}])[0]
                self._draw_radar_alert_banner(draw, map_box, alert, c)

            try:
                tz_name = loc.get("timezone")
                tz = ZoneInfo(tz_name) if tz_name and tz_name != "auto" else dt.datetime.now().astimezone().tzinfo
                stamp = dt.datetime.fromtimestamp(frame["time"], tz=dt.timezone.utc).astimezone(tz)
                ts = stamp.strftime("%I:%M %p").lstrip("0")
            except Exception:
                ts = dt.datetime.fromtimestamp(frame["time"]).strftime("%I:%M %p").lstrip("0")

            # Upper-left timestamp/view badge.
            draw.rectangle((map_box[0]+16, map_box[1]+16, map_box[0]+260, map_box[1]+66), fill=(8, 20, 34))
            draw.text((map_box[0]+28, map_box[1]+25), ts, font=font(22, bold=True, mono=True), fill="#ffffff")
            draw.text((map_box[0]+170, map_box[1]+27), view_tags.get(view, "LOCAL"), font=font(14, bold=True, mono=True), fill=c["accent"])

            # Compact reflectivity key.
            key_x = map_box[2]-302
            key_y = map_box[1]+20
            draw.rectangle((key_x-14, key_y-10, map_box[2]-16, key_y+45), fill=(8,20,34))
            colors = ["#33aa33", "#70d12b", "#e6dd22", "#f18a22", "#e63227", "#a400bf"]
            for i, color in enumerate(colors):
                draw.rectangle((key_x+i*39, key_y, key_x+i*39+36, key_y+14), fill=color)
            draw.text((key_x, key_y+19), "LIGHT                         HEAVY", font=font(12, bold=True, mono=True), fill="#ffffff")

            # Viewer-facing timing replaces v0.3.0's operator/debug values.
            try:
                oldest = min(float(row.get("time", frame["time"])) for row in frames)
                newest = max(float(row.get("time", frame["time"])) for row in frames)
                span = max(0, int(round((newest-oldest)/60.0)))
            except Exception:
                span = 0
            info = f"PAST {span or len(frames)*5} MINUTES   •   {len(frames)} FRAME LOOP   •   LATEST {ts}"
            draw.rectangle((map_box[0]+16, map_box[3]-48, map_box[0]+650, map_box[3]-14), fill=(8,20,34))
            draw.text((map_box[0]+28, map_box[3]-40), info, font=font(14, bold=True, mono=True), fill="#ffffff")
        else:
            round_rect(draw, map_box, 12, c["panel"])
            draw.text((w//2, 292), f"{view_tags.get(view, 'LOCAL')} RADAR TEMPORARILY UNAVAILABLE", font=font(36, bold=True), fill=c["accent"], anchor="mm")
            message = radar.get("last_error") or "WeatherStream will retry automatically and use cached imagery when available."
            for row, line in enumerate(textwrap.wrap(message, width=88)[:3]):
                draw.text((w//2, 352 + row*31), line, font=font(19), fill=c["muted"], anchor="mm")

        attribution = "Radar: RainViewer   •   Map: © OpenStreetMap contributors"
        if radar_cfg.get("show_boundaries", True):
            attribution += "   •   Boundaries: U.S. Census Bureau"
        draw.text((58, h-100), attribution, font=font(13, mono=True), fill=c["muted"])

    def _draw_range_rings(self, draw, map_box, p, zoom, radar_cfg, c):
        loc = p.get("location", {})
        try:
            lat = float(loc.get("latitude"))
        except Exception:
            return
        x1, y1, x2, y2 = map_box
        cx, cy = (x1+x2)//2, (y1+y2)//2
        meters_per_source_pixel = 156543.03392 * max(0.10, math.cos(math.radians(lat))) / (2 ** int(zoom))
        display_scale = ((x2-x1) / 1180.0 + (y2-y1) / 500.0) / 2.0
        for miles in radar_cfg.get("range_rings_miles", [25, 50, 100]):
            try:
                radius = (float(miles) * 1609.344 / meters_per_source_pixel) * display_scale
            except Exception:
                continue
            if radius < 18 or radius > max(x2-x1, y2-y1) * 0.85:
                continue
            r = int(radius)
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline="#d7f2ff", width=1)
            # Put each label on the top edge of its own ring so nested ranges do not overlap.
            draw.text((cx, cy-r+8), f"{int(float(miles))} mi", font=font(11, bold=True, mono=True), fill="#ffffff", stroke_width=2, stroke_fill="#102030", anchor="ma")

    def _alert_color(self, event: str) -> str:
        e = (event or "").lower()
        if "tornado" in e: return "#ff2c2c"
        if "severe thunderstorm" in e: return "#ff9f1c"
        if "flash flood" in e: return "#22d15f"
        if "warning" in e: return "#ffcf33"
        if "watch" in e: return "#f5e642"
        return "#58d8ff"

    def _draw_alert_polygons(self, img, draw, map_box, p, zoom, alerts):
        loc = p.get("location", {})
        try:
            center_x, center_y = latlon_to_world(float(loc["latitude"]), float(loc["longitude"]), int(zoom))
        except Exception:
            return
        source_w, source_h = 1180.0, 500.0
        left = center_x*256.0 - source_w/2.0
        top = center_y*256.0 - source_h/2.0
        x1, y1, x2, y2 = map_box
        sx = (x2-x1) / source_w
        sy = (y2-y1) / source_h

        def project(coord):
            lon, lat = float(coord[0]), float(coord[1])
            wx, wy = latlon_to_world(lat, lon, int(zoom))
            return (x1 + (wx*256.0-left)*sx, y1 + (wy*256.0-top)*sy)

        for alert in alerts[:6]:
            geometry = alert.get("geometry") or {}
            gtype = geometry.get("type")
            coords = geometry.get("coordinates") or []
            if gtype == "Polygon": polygons = [coords]
            elif gtype == "MultiPolygon": polygons = coords
            else: continue
            color = self._alert_color(alert.get("event", ""))
            for polygon in polygons:
                if not polygon: continue
                ring = polygon[0] or []
                points = []
                for coord in ring:
                    try: points.append(project(coord))
                    except Exception: pass
                if len(points) >= 3:
                    try:
                        rgb=ImageColor.getrgb(color)
                        overlay=Image.new("RGBA",img.size,(0,0,0,0)); od=ImageDraw.Draw(overlay)
                        od.polygon(points,fill=(*rgb,42))
                        od.line(points + [points[0]],fill=(*rgb,235),width=6,joint="curve")
                        img.paste(overlay,(0,0),overlay); draw=ImageDraw.Draw(img)
                    except Exception:
                        draw.line(points + [points[0]], fill=color, width=5, joint="curve")
                    draw.line(points + [points[0]], fill="#ffffff", width=1, joint="curve")
                    try:
                        cx=sum(x for x,_ in points)/len(points); cy=sum(y for _,y in points)/len(points)
                        label=str(alert.get("event") or "WARNING").upper().replace(" WARNING","")[:24]
                        if x1+60<cx<x2-60 and y1+35<cy<y2-35:
                            tw=draw.textbbox((0,0),label,font=font(11,bold=True,mono=True))[2]
                            draw.rectangle((cx-tw/2-7,cy-11,cx+tw/2+7,cy+10),fill="#071723",outline=color,width=2)
                            draw.text((cx,cy),label,font=font(11,bold=True,mono=True),fill="#ffffff",anchor="mm")
                    except Exception:
                        pass

    def _draw_radar_alert_banner(self, draw, map_box, alert, c):
        event = alert.get("event", "WEATHER ALERT").upper()
        color = self._alert_color(event)
        x1, y1, x2, _ = map_box
        draw.rectangle((x1+300, y1+16, x2-320, y1+72), fill="#111820", outline=color, width=4)
        draw.text(((x1+x2)//2, y1+44), event, font=font(22, bold=True, mono=True), fill=color, anchor="mm")

    def _draw_radar_sweep(self, img: Image.Image, map_box, now: float, sweep_seconds: float) -> None:
        x1, y1, x2, y2 = map_box
        mw, mh = x2-x1, y2-y1
        overlay = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        cx, cy = mw//2, mh//2
        period = max(2.0, sweep_seconds)
        angle = ((now % period) / period) * math.tau - math.pi/2
        radius = int(math.hypot(mw, mh))

        # A faint trailing fan plus a bright leading edge evokes a classic display sweep without altering data.
        for trail in range(9, -1, -1):
            a = angle - trail * 0.025
            ex = cx + math.cos(a) * radius
            ey = cy + math.sin(a) * radius
            alpha = max(5, 42 - trail*4)
            d.line((cx, cy, ex, ey), fill=(175, 255, 220, alpha), width=2 if trail else 3)
        ex = cx + math.cos(angle) * radius
        ey = cy + math.sin(angle) * radius
        d.line((cx, cy, ex, ey), fill=(220, 255, 235, 118), width=3)
        d.ellipse((cx-5, cy-5, cx+5, cy+5), fill=(230,255,240,180))
        img.paste(overlay, (x1, y1), overlay)

    def _moon_phase(self, now: dt.datetime | None = None):
        now = now or dt.datetime.now(dt.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.timezone.utc)
        epoch = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)
        synodic = 29.53058867
        phase = ((now.astimezone(dt.timezone.utc) - epoch).total_seconds() / 86400.0) % synodic / synodic
        illumination = (1 - math.cos(math.tau * phase)) / 2.0
        names = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon", "Waning Gibbous", "Last Quarter", "Waning Crescent"]
        return names[int((phase*8)+0.5) % 8], int(round(illumination*100))

    def _draw_almanac(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Weather Almanac", location_label(p["location"]), c)
        daily, cur = p.get("daily", {}), p.get("current", {})
        sunrise = (daily.get("sunrise") or [None])[0]
        sunset = (daily.get("sunset") or [None])[0]
        def tm(value):
            try: return dt.datetime.fromisoformat(value).strftime("%I:%M %p").lstrip("0")
            except Exception: return "--"
        moon, illum = self._moon_phase()
        uv = (daily.get("uv_index_max") or [None])[0]
        rain = (daily.get("precipitation_sum") or [None])[0]
        rows = [
            ("SUNRISE", tm(sunrise)), ("SUNSET", tm(sunset)),
            ("HUMIDITY", n(cur.get("relative_humidity_2m"), 0, "%")), ("CLOUD COVER", n(cur.get("cloud_cover"), 0, "%")),
            ("WIND GUST", n(cur.get("wind_gusts_10m"), 0, " mph")), ("RAIN TODAY", n(rain, 2, " in")),
            ("UV INDEX", n(uv, 1)), ("MOON", f"{moon} {illum}%"),
        ]
        for i, (label, value) in enumerate(rows):
            col = i % 2; row = i // 2
            x = 85 + col*600; y = 126 + row*106
            round_rect(draw, (x, y, x+520, y+86), 11, c["panel"])
            draw.text((x+24, y+13), label, font=font(18, bold=True, mono=True), fill=c["muted"])
            size = 28 if len(str(value)) < 22 else 20
            draw.text((x+496, y+40), value, font=font(size, bold=True), fill=c["text"], anchor="ra")

    def _draw_alert(self, draw, w, h, settings, snapshot, primary, c):
        alert=(snapshot.get("alerts") or [{}])[0]
        event=str(alert.get("event") or "Weather Alert")
        color=self._alert_color(event)
        # Strong alert identity with a persistent left severity rail rather than a page of text.
        draw.rectangle((0,0,w,94),fill=color)
        draw.text((38,22),"RWN WEATHER ALERT",font=font(34,bold=True,mono=True),fill="#ffffff")
        draw.text((w-38,30),str(alert.get("severity") or "Unknown").upper(),font=font(18,bold=True,mono=True),fill="#ffffff",anchor="ra")
        draw.rectangle((42,120,58,600),fill=color)
        alert_icon_on=(settings.get("icon_system") or {}).get("alert_icons",True)
        title_x=88
        if alert_icon_on and paste_rwn_icon(getattr(draw,"_image",None),rwn_alert_icon(event,104),82,118):
            title_x=205
        draw.text((title_x,126),event.upper(),font=font(36,bold=True),fill=c["accent"])
        headline=str(alert.get("headline") or event or "Weather alert in effect")
        y=184
        for line in textwrap.wrap(headline,width=66)[:2]:
            draw.text((88,y),line,font=font(26,bold=True),fill=c["text"]); y+=38
        area=str(alert.get("areaDesc") or "")
        round_rect(draw,(88,278,1190,354),13,c["panel"],outline=color,width=2)
        draw.text((108,292),"AFFECTED AREA",font=font(14,bold=True,mono=True),fill=c["muted"])
        draw.text((108,321),area[:92] or "See National Weather Service alert",font=font(20,bold=True),fill=c["text"])
        instruction=(alert.get("instruction") or alert.get("description") or "").replace("\n"," ")
        draw.text((88,386),"WHAT YOU NEED TO KNOW",font=font(15,bold=True,mono=True),fill=c["muted"])
        iy=418
        for line in textwrap.wrap(instruction,width=86)[:4]:
            draw.text((88,iy),line,font=font(20),fill=c["text"]); iy+=29
        expiry=alert.get("expires") or alert.get("ends") or ""
        try: expiry=dt.datetime.fromisoformat(str(expiry).replace("Z","+00:00")).astimezone().strftime("%I:%M %p").lstrip("0")
        except Exception: expiry="--"
        round_rect(draw,(88,548,1190,596),10,c["panel2"],outline=c["muted"],width=1)
        footer=f"{str(alert.get('urgency') or 'Unknown').upper()}  •  EXPIRES {expiry}  •  NATIONAL WEATHER SERVICE"
        draw.text((639,572),footer,font=font(15,bold=True,mono=True),fill=c["accent"],anchor="mm")

    def _is_takeover_active(self, settings, snapshot):
        return self._takeover_alert(settings, snapshot) is not None

    def _ticker_text(self, settings, snapshot):
        alerts = snapshot.get("alerts") or []
        if alerts and (settings.get("alerts", {}) or {}).get("ticker_takeover", True):
            severe = self._takeover_alert(settings, snapshot)
            if severe:
                expiry = severe.get("expires") or severe.get("ends") or ""
                try: expiry = dt.datetime.fromisoformat(expiry.replace("Z", "+00:00")).astimezone().strftime("%I:%M %p").lstrip("0")
                except Exception: expiry = "UNTIL FURTHER NOTICE"
                return f"⚠ {severe.get('event','WEATHER ALERT').upper()}  •  {severe.get('areaDesc','')}  •  UNTIL {expiry}     •     "
        primary = self._primary(settings, snapshot); parts=[]
        if primary:
            cur=primary.get("current",{}); loc=primary.get("location",{}); hourly=primary.get("hourly",{}); daily=primary.get("daily",{})
            parts.append(f"CURRENT  {loc.get('name','LOCAL')} {n(cur.get('temperature_2m'),0,'°')}  {cur.get('description','')}")
            precip=self._max_next(hourly,"precipitation_probability",12)
            if precip >= int((settings.get("smart_programming") or {}).get("rain_threshold",20)): parts.append(f"RAIN CHANCE  UP TO {precip:.0f}% NEXT 12 HOURS")
            spc=settings.get("_spc_outlook") or {}; day1=spc.get("day1") or {}
            if int(day1.get("rank") or 0)>=2: parts.append(f"SPC OUTLOOK  {day1.get('name','').upper()}")
            highs=daily.get("temperature_2m_max") or []; lows=daily.get("temperature_2m_min") or []
            if highs and lows: parts.append(f"TODAY  HIGH {n(highs[0],0,'°')}  LOW {n(lows[0],0,'°')}")
            if self.history_store:
                hist=self.history_store.summary(loc.get("id") or settings.get("primary_location_id"),24)
                if hist.get("pressure_trend") and hist.get("pressure_trend")!="STEADY": parts.append(f"PRESSURE {hist.get('pressure_trend')}")
        else: parts.append("SETUP  OPEN THE ADMIN PAGE AND ADD A U.S. ZIP CODE")
        if alerts: parts.append(f"ALERT  {alerts[0].get('event','WEATHER ALERT').upper()}")
        else: parts.append("ALERTS  NO ACTIVE NWS ALERTS FOR THIS CHANNEL")
        return "     •     ".join(parts)+"     •     "

    def _draw_footer(self, draw, w, h, settings, snapshot, c, now):
        ticker_top=h-86; bug_w=268; gutter=0
        draw.rectangle((0,ticker_top,w,h),fill=c["ticker"])
        draw.rectangle((0,ticker_top,w,ticker_top+3),fill=c["accent"])
        clock=dt.datetime.fromtimestamp(now).strftime("%I:%M %p").lstrip("0")
        station=settings.get("station_name","Roller Weather Network"); callsign=(settings.get("station_callsign") or "").strip().upper()
        draw.rectangle((0,ticker_top,bug_w,h),fill=c["panel2"])
        draw.line((bug_w,ticker_top,bug_w,h),fill=c["muted"],width=2)
        draw.text((bug_w//2,ticker_top+24),clock,font=font(26,bold=True,mono=True),fill=c["accent"],anchor="mm")
        if c.get("event_key"):
            draw.text((bug_w//2,ticker_top+54),f"RWN {str(c.get('event_key')).upper()}",font=font(15,bold=True,mono=True),fill=c["text"],anchor="mm")
            draw.text((bug_w//2,ticker_top+74),str(c.get("event_desk") or station)[:31],font=font(9,bold=True,mono=True),fill=c.get("accent2",c["muted"]),anchor="mm")
        else:
            draw.text((bug_w//2,ticker_top+57),callsign or station[:24],font=font(15,bold=True,mono=True),fill=c["text"],anchor="mm")
            if callsign: draw.text((bug_w//2,ticker_top+75),station[:27],font=font(10,mono=True),fill=c["muted"],anchor="mm")

        severe=self._takeover_alert(settings,snapshot)
        mode=self._visual(settings).get("footer_mode","data_ribbon")
        if severe:
            color=self._alert_color(str(severe.get("event") or ""))
            draw.rectangle((bug_w,ticker_top,w,h),fill="#111820")
            draw.rectangle((bug_w,ticker_top,bug_w+10,h),fill=color)
            draw.text((bug_w+30,ticker_top+20),str(severe.get("event") or "WEATHER ALERT").upper(),font=font(18,bold=True,mono=True),fill=color)
            draw.text((bug_w+30,ticker_top+51),str(severe.get("areaDesc") or "")[:74],font=font(18,bold=True),fill="#ffffff")
        elif mode == "crawl":
            ticker_layer=Image.new("RGBA",(max(1,w-bug_w-14),86),(0,0,0,0)); td=ImageDraw.Draw(ticker_layer)
            text=self._ticker_text(settings,snapshot); f=font(24,bold=True,mono=True); bbox=td.textbbox((0,0),text,font=f); text_w=max(1,bbox[2]-bbox[0]); area_w=ticker_layer.size[0]
            offset=int(now*115)%(text_w+100); x=area_w-offset
            while x<area_w:
                td.text((x,30),text,font=f,fill=c["text"]); x+=text_w+100
            draw._image.paste(ticker_layer,(bug_w+14,ticker_top),ticker_layer)
        else:
            primary=self._primary(settings,snapshot); items=[]
            if primary:
                cur=primary.get("current",{}); daily=primary.get("daily",{}); hourly=primary.get("hourly",{}); loc=primary.get("location",{})
                highs=daily.get("temperature_2m_max") or []; lows=daily.get("temperature_2m_min") or []
                event_key=str(c.get("event_key") or "")
                if event_key:
                    rain12=self._max_next(hourly,"precipitation_probability",12); rain24=self._sum_next(hourly,"precipitation",24)
                    aq=primary.get("air_quality") or {}; gauges=((primary.get("rivers") or {}).get("gauges") or [])
                    temp=n(cur.get("temperature_2m"),0,"°"); feels=n(cur.get("apparent_temperature"),0,"°"); gust=n(cur.get("wind_gusts_10m"),0," mph")
                    if event_key=="severe":
                        items=[("SEVERE DESK","ALERTS • RADAR"),("CURRENT",f"{temp} {str(cur.get('description') or '')[:14]}"),("PEAK GUST",gust),("RAIN NEXT 12H",f"{rain12:.0f}%")]
                    elif event_key=="flood":
                        items=[("FLOOD DESK","HYDROLOGY MONITOR"),("24H MODEL RAIN",f"{rain24:.2f} in"),("RAIN NEXT 12H",f"{rain12:.0f}%"),("USGS GAUGES",str(len(gauges)))]
                    elif event_key=="winter":
                        items=[("WINTER DESK","SNOW • ICE • COLD"),("TEMPERATURE",temp),("FEELS LIKE",feels),("PRECIP NEXT 12H",f"{rain12:.0f}%")]
                    elif event_key=="heat":
                        items=[("HEAT DESK","HEAT INDEX • SAFETY"),("TEMPERATURE",temp),("FEELS LIKE",feels),("HUMIDITY",n(cur.get("relative_humidity_2m"),0,"%"))]
                    elif event_key=="wildfire":
                        items=[("FIRE WEATHER","WIND • HUMIDITY • AQI"),("HUMIDITY",n(cur.get("relative_humidity_2m"),0,"%")),("PEAK GUST",gust),("AIR QUALITY",f"AQI {safe(aq.get('aqi'))} {str(aq.get('category') or '')[:10]}")]
                    else:
                        tropical=settings.get("_tropical") or {}; active=bool(tropical.get("segment_active") or tropical.get("channel_active"))
                        items=[("TROPICS WATCH","NHC • GULF • ATLANTIC"),("LOCAL",f"{temp} {str(cur.get('description') or '')[:14]}"),("TROPICS","OFFICIAL UPDATE" if active else "MONITORING"),("WIND",f"{cur.get('wind_cardinal','--')} {n(cur.get('wind_speed_10m'),0,' mph')}")]
                else:
                    story_cfg=settings.get("story_engine") or {}
                    story=self._weather_story(settings,snapshot,primary,now) if story_cfg.get("enabled",True) else {"id":"quiet","title":"Quiet Weather","metrics":{}}
                    metrics=story.get("metrics") or {}; sid=str(story.get("id") or "quiet")
                    items=[(str(loc.get("name") or "LOCAL").upper(),f"{n(cur.get('temperature_2m'),0,'°')} {str(cur.get('description') or '')[:18]}")]
                    if story_cfg.get("context_ribbon",True):
                        items.append(("WEATHER STORY",str(story.get("title") or "LOCAL WEATHER").upper()[:22]))
                        if sid in {"rain","flood","storms","severe"}:
                            items.append(("RAIN NEXT 12H",f"{metrics.get('rain_probability_12h',0):.0f}% • {metrics.get('rain_24h_inches',0):.2f} in"))
                        elif sid == "heat":
                            items.append(("HEAT",f"PEAK {metrics.get('peak_heat_f') or 0:.0f}°"))
                        elif sid == "wind":
                            items.append(("WIND",f"GUST {metrics.get('peak_gust_mph',0):.0f} mph"))
                        elif sid == "air_quality":
                            aq=(primary.get("air_quality") or {}); items.append(("AIR QUALITY",f"AQI {aq.get('aqi','--')} {str(aq.get('category') or '')[:12]}"))
                        elif sid in {"cold","winter"}:
                            items.append(("COLD",f"LOW {metrics.get('low_24h_f') or 0:.0f}°"))
                        elif sid == "tropical":
                            items.append(("TROPICS","OFFICIAL UPDATE ACTIVE"))
                        else:
                            items.append(("TODAY",f"{n(highs[0] if highs else None,0,'°')} / {n(lows[0] if lows else None,0,'°')}"))
                        items.append(("WIND",f"{cur.get('wind_cardinal','--')} {n(cur.get('wind_speed_10m'),0,' mph')}") if len(items)<4 else ("NEXT", "RWN STORY DIRECTOR"))
                        items=items[:4]
                    else:
                        items.extend([
                            ("TODAY",f"{n(highs[0] if highs else None,0,'°')} / {n(lows[0] if lows else None,0,'°')}"),
                            ("RAIN NEXT 12H",f"{self._max_next(hourly,'precipitation_probability',12):.0f}%"),
                            ("WIND",f"{cur.get('wind_cardinal','--')} {n(cur.get('wind_speed_10m'),0,' mph')}")
                        ])
            else: items=[("SETUP","ADD A ZIP CODE")]
            area_w=w-bug_w; cell=area_w/max(1,len(items))
            for i,(label,value) in enumerate(items):
                x1=int(bug_w+i*cell); x2=int(bug_w+(i+1)*cell)
                if i: draw.line((x1,ticker_top+12,x1,h-12),fill=c["panel2"],width=2)
                draw.text((x1+14,ticker_top+17),label,font=font(11,bold=True,mono=True),fill=c["muted"])
                size=18 if len(value)<14 else 14 if len(value)<19 else 12
                value_font=font(size,bold=True)
                # Event-desk ribbons often use compact editorial phrases. Fit once
                # against the available cell width so labels never collide.
                while size>10 and draw.textbbox((0,0),value,font=value_font)[2] > max(40,x2-x1-26):
                    size-=1; value_font=font(size,bold=True)
                draw.text((x1+14,ticker_top+44),value,font=value_font,fill=c["text"])

        if bool(settings.get("_local8_active")) and not self._is_takeover_active(settings,snapshot):
            draw.rectangle((bug_w-82,ticker_top+4,bug_w-5,ticker_top+20),fill=c["accent"])
            draw.text((bug_w-43,ticker_top+12),"LOCAL 8s",font=font(9,bold=True,mono=True),fill=c["panel2"],anchor="mm")
        lid=settings.get("_render_location_id") or settings.get("primary_location_id")
        weather_state=((((snapshot.get("location_status") or {}).get(lid) or {}).get("weather") or {}).get("state"))
        if weather_state=="cached":
            draw.rectangle((5,ticker_top+4,78,ticker_top+20),fill="#b36b18")
            draw.text((41,ticker_top+12),"CACHED",font=font(9,bold=True,mono=True),fill="#ffffff",anchor="mm")
