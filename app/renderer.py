from __future__ import annotations

import datetime as dt
import math
import textwrap
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageEnhance, ImageFilter

from app.config import CONFIG_DIR
from app.radar import latlon_to_world

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


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_MONO_BOLD if mono and bold else FONT_MONO if mono else FONT_BOLD if bold else FONT_REG
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


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


def draw_weather_icon(draw: ImageDraw.ImageDraw, code: int | None, x: int, y: int, scale: float, c: dict[str, str], *, is_night: bool = False) -> None:
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
    def __init__(self, config_store, weather_manager, radar_manager=None, place_manager=None, history_store=None, spc_manager=None) -> None:
        self.config_store = config_store
        self.weather_manager = weather_manager
        self.radar_manager = radar_manager
        self.place_manager = place_manager
        self.history_store = history_store
        self.spc_manager = spc_manager
        self.cycle_started = dt.datetime.now().timestamp()
        self._logo_cache = None
        self._logo_mtime = None

    def _theme(self, settings: dict[str, Any]) -> dict[str, str]:
        return THEMES.get(settings.get("theme", "local-90s"), THEMES["local-90s"])

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

    def _smart_wanted(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float) -> list[str]:
        pres = settings.get("presentation") or {}; smart = settings.get("smart_programming") or {}; dayparts = settings.get("dayparts") or {}
        if dayparts.get("enabled", True):
            part = self._daypart(settings, snapshot, now)
            wanted = list(((dayparts.get("sequences") or {}).get(part)) or (pres.get("sequence") or []))
        else:
            wanted = list(pres.get("sequence") or [])
        if not smart.get("enabled", True): return wanted
        primary = self._primary(settings, snapshot) or {}; hourly = primary.get("hourly") or {}
        rain = self._max_next(hourly, "precipitation_probability", 12)
        storm_hourly = ((snapshot.get("storm_guidance") or {}).get("hourly") or {})
        storm = self._max_next(storm_hourly, "thunderstorm_probability", 12)
        spc = self.spc_manager.snapshot().get("outlook", {}) if self.spc_manager else {}
        rank = int(((spc.get("day1") or {}).get("rank")) or 0)
        min_risk = {"TSTM":1,"MRGL":2,"SLGT":3,"ENH":4,"MDT":5,"HIGH":6}.get((settings.get("spc") or {}).get("minimum_smart_risk", "MRGL"), 2)
        if rain < int(smart.get("rain_threshold", 20)):
            wanted = [x for x in wanted if x != "precipitation"]
        if storm < int(smart.get("storm_threshold", 15)):
            wanted = [x for x in wanted if x != "storm_outlook"]
        if rank < min_risk:
            wanted = [x for x in wanted if x != "spc_outlook"]
        elif "spc_outlook" not in wanted:
            idx = wanted.index("radar_local") if "radar_local" in wanted else min(5, len(wanted)); wanted.insert(idx, "spc_outlook")
        if not (settings.get("history") or {}).get("enabled", True):
            wanted = [x for x in wanted if x != "weather_history"]
        return wanted

    def programming_status(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        now = now or dt.datetime.now().timestamp()
        return {"daypart": self._daypart(settings, snapshot, now), "smart_enabled": bool((settings.get("smart_programming") or {}).get("enabled", True)), "sequence": [x for x,_ in self._sequence(settings, snapshot, now)]}

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
        elif now is not None and self._scheduled_update_elapsed(settings, snapshot, now) is not None:
            wanted = ((pres.get("scheduled_updates") or {}).get("sequence") or [
                "station_id", "current", "radar_local", "today", "seven_day"
            ])
        else:
            wanted = self._smart_wanted(settings, snapshot, now if now is not None else dt.datetime.now().timestamp())

        seq = []
        for name in wanted:
            if name == "station_id" and not pres.get("show_station_id", True):
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
            fallback = durations.get("radar", 16) if name.startswith("radar") or name == "alert_radar" else 10
            seq.append((name, max(3, int(durations.get(name, fallback)))))

        if snapshot.get("alerts") and not takeover and not any(name == "alert" for name, _ in seq):
            seq.insert(1 if seq else 0, ("alert", max(3, int(durations.get("alert", 14)))))
        return seq or [("current", 12)]

    def _timeline(self, settings: dict[str, Any], snapshot: dict[str, Any], now: float):
        scheduled_elapsed = self._scheduled_update_elapsed(settings, snapshot, now)
        seq = self._sequence(settings, snapshot, now)
        total = sum(d for _, d in seq)
        offset = (scheduled_elapsed if scheduled_elapsed is not None else (now - self.cycle_started)) % max(1, total)
        cursor = 0.0
        for idx, (name, duration) in enumerate(seq):
            if offset < cursor + duration:
                elapsed = offset - cursor
                return seq, idx, name, elapsed / duration, elapsed, duration
            cursor += duration
        return seq, 0, seq[0][0], 0.0, 0.0, seq[0][1]

    def _paint_background(self, img: Image.Image, c: dict[str, str], settings: dict[str, Any], now: float) -> None:
        draw = ImageDraw.Draw(img)
        w, h = img.size
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
        w = int(settings["video"].get("width", 1280))
        h = int(settings["video"].get("height", 720))
        c = self._theme(settings)
        img = Image.new("RGB", (w, h), c["bg"])
        self._paint_background(img, c, settings, now)
        draw = ImageDraw.Draw(img)
        if name == "station_id":
            self._draw_station_id(draw, w, h, settings, primary, c, now)
        elif name == "current": self._draw_current(draw, w, h, settings, primary, c)
        elif name == "today": self._draw_today(draw, w, h, settings, primary, c)
        elif name == "nws_forecast": self._draw_nws_forecast(draw, w, h, settings, primary, c)
        elif name == "temperature_trend": self._draw_temperature_trend(draw, w, h, settings, primary, c)
        elif name == "hourly": self._draw_hourly(draw, w, h, settings, primary, c)
        elif name == "precipitation": self._draw_precipitation(draw, w, h, settings, primary, c)
        elif name == "storm_outlook": self._draw_storm_outlook(draw, w, h, settings, snapshot, primary, c)
        elif name == "spc_outlook": self._draw_spc_outlook(draw, w, h, settings, primary, c)
        elif name == "condition_focus": self._draw_condition_focus(draw, w, h, settings, primary, c)
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
        return img

    def render(self, now: float | None = None) -> Image.Image:
        now = now or dt.datetime.now().timestamp()
        settings = self.config_store.get()
        snapshot = self.weather_manager.snapshot()
        w = int(settings["video"].get("width", 1280)); h = int(settings["video"].get("height", 720))
        c = self._theme(settings)
        primary = self._primary(settings, snapshot)
        if not primary:
            out = Image.new("RGB", (w, h), c["bg"]); self._paint_background(out, c, settings, now)
            draw = ImageDraw.Draw(out); self._draw_setup(draw, w, h, settings, c); self._draw_footer(draw, w, h, settings, snapshot, c, now)
            return self._apply_retro_effects(out, settings, now)

        seq, idx, name, progress, elapsed, duration = self._timeline(settings, snapshot, now)
        current = self._render_slide(name, settings, snapshot, primary, now, progress)
        pres = settings.get("presentation", {})
        kind = pres.get("transition", "crossfade")
        transition = min(float(pres.get("transition_seconds", 0.75)), duration / 3)
        out = current
        if kind != "cut" and transition > 0:
            if elapsed < transition:
                prev_name, _ = seq[(idx-1) % len(seq)]
                prev = self._render_slide(prev_name, settings, snapshot, primary, now, 1.0)
                alpha = max(0.0, min(1.0, elapsed / transition))
                out = self._transition(prev, current, alpha, kind)
            elif duration - elapsed < transition:
                next_name, _ = seq[(idx+1) % len(seq)]
                nxt = self._render_slide(next_name, settings, snapshot, primary, now, 0.0)
                alpha = max(0.0, min(1.0, (transition - (duration-elapsed)) / transition))
                out = self._transition(current, nxt, alpha, kind)
        return self._apply_retro_effects(out, settings, now)

    def render_preview(self, slide_name: str, test_alert: bool = False) -> Image.Image:
        settings = self.config_store.get(); snapshot = self.weather_manager.snapshot(); primary = self._primary(settings, snapshot)
        w = int(settings["video"].get("width", 1280)); h = int(settings["video"].get("height", 720)); c = self._theme(settings)
        if not primary:
            out = Image.new("RGB", (w,h), c["bg"]); self._paint_background(out,c,settings,dt.datetime.now().timestamp()); d=ImageDraw.Draw(out); self._draw_setup(d,w,h,settings,c); self._draw_footer(d,w,h,settings,snapshot,c,dt.datetime.now().timestamp()); return out
        valid = {"station_id","current","condition_focus","today","nws_forecast","temperature_trend","hourly","precipitation","storm_outlook","spc_outlook","radar_local","radar_regional","radar_wide","seven_day","regional_map","regional","weather_history","almanac","alert","alert_radar"}
        if slide_name not in valid: slide_name = "current"
        snap = snapshot
        if test_alert:
            snap = dict(snapshot); snap["alerts"] = list(snapshot.get("alerts") or [])
            loc = primary.get("location") or {}; lat=float(loc.get("latitude",0)); lon=float(loc.get("longitude",0)); d=0.35
            snap["alerts"] = [{"id":"weatherstream-test","event":"TEST Tornado Warning","headline":"TEST MODE — NOT A REAL WEATHER ALERT","severity":"Severe","urgency":"Immediate","certainty":"Observed","areaDesc":"TEST MODE — Roller Weather Network","description":"This synthetic warning is only for testing WeatherStream graphics and alert presentation.","instruction":"No action is required. This is not a real weather warning.","expires":(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=30)).isoformat(),"geometry":{"type":"Polygon","coordinates":[[[lon-d,lat-d],[lon+d,lat-d],[lon+d,lat+d],[lon-d,lat+d],[lon-d,lat-d]]]}}]
            if slide_name not in {"alert","alert_radar"}: slide_name="alert"
        out=self._render_slide(slide_name,settings,snap,primary,dt.datetime.now().timestamp(),0.35)
        if test_alert:
            d=ImageDraw.Draw(out); d.rectangle((0,88,w,126),fill="#f0c400"); d.text((w//2,107),"TEST MODE • NOT A REAL WEATHER ALERT",font=font(19,bold=True,mono=True),fill="#111111",anchor="mm")
        return out

    def _pattern_mask(self, size: tuple[int, int], alpha: float, block: int) -> Image.Image:
        w, h = size
        sw, sh = max(1, (w + block - 1) // block), max(1, (h + block - 1) // block)
        mask = Image.new("L", (sw, sh), 0)
        pix = mask.load()
        threshold = int(max(0.0, min(1.0, alpha)) * 10000)
        for y in range(sh):
            for x in range(sw):
                # Deterministic pseudo-random field: stable between adjacent render frames.
                v = ((x * 92821) ^ (y * 68917) ^ ((x+y) * 31337)) % 10000
                pix[x, y] = 255 if v < threshold else 0
        return mask.resize((w, h), Image.Resampling.NEAREST)

    def _transition(self, a: Image.Image, b: Image.Image, alpha: float, kind: str) -> Image.Image:
        alpha = max(0.0, min(1.0, alpha))
        w, h = a.size
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
        if style == "terminal80":
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
        draw_weather_icon(draw, cur.get("weather_code"), 350, 308, 0.85, c)
        draw.text((615, 345), location_label(loc), font=font(31, bold=True), fill=c["text"])
        draw.text((615, 402), f"{n(cur.get('temperature_2m'),0,'°')}  {cur.get('description','')}", font=font(39, bold=True), fill=c["text"])
        date_text = dt.datetime.fromtimestamp(now).strftime("%A %B %d • %I:%M %p").upper().replace(" 0", " ")
        draw.text((w//2, 552), date_text, font=font(22, bold=True, mono=True), fill=c["muted"], anchor="mm")

    def _load_logo(self, settings):
        cfg = settings.get("branding") or {}
        path = BRANDING_LOGO if BRANDING_LOGO.exists() else (BUILTIN_RWN_LOGO if cfg.get("use_builtin_logo", True) else BRANDING_LOGO)
        try:
            mtime = path.stat().st_mtime
            cache_key = (str(path), mtime)
            if self._logo_cache is None or self._logo_mtime != cache_key:
                self._logo_cache = Image.open(path).convert("RGBA")
                self._logo_mtime = cache_key
            return self._logo_cache.copy()
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
        scale = min(max_w / max(1, logo.width), max_h / max(1, logo.height), 1.0)
        logo = logo.resize((max(1, int(logo.width*scale)), max(1, int(logo.height*scale))), Image.Resampling.LANCZOS)
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
        base=self.radar_manager.map_snapshot(view) if self.radar_manager else None
        if base is None:
            draw.text((w//2,330),"REGIONAL MAP IS LOADING",font=font(36,bold=True),fill=c["accent"],anchor="mm")
            draw.text((w//2,385),"It will appear after the first map/radar refresh.",font=font(20),fill=c["muted"],anchor="mm")
            return
        base=base.resize((map_box[2]-map_box[0],map_box[3]-map_box[1]),Image.Resampling.LANCZOS)
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

    def _draw_current(self, draw, w, h, settings, p, c):
        loc = p["location"]
        cur = p.get("current", {})
        self._header(draw, w, "Current Conditions", location_label(loc), c)
        draw_weather_icon(draw, cur.get("weather_code"), 72, 155, 1.28, c)
        draw.text((330, 160), n(cur.get("temperature_2m"), 0, "°"), font=font(122, bold=True), fill=c["text"])
        draw.text((340, 300), cur.get("description", "Weather Unavailable"), font=font(38, bold=True), fill=c["accent"])
        draw.text((340, 358), f"Feels Like {n(cur.get('apparent_temperature'), 0, '°')}", font=font(27), fill=c["muted"])

        x1, x2 = 760, 1018
        rows = [
            ("HUMIDITY", n(cur.get("relative_humidity_2m"), 0, "%")),
            ("WIND", f"{cur.get('wind_cardinal', '--')} {n(cur.get('wind_speed_10m'), 0, ' mph')}"),
            ("GUSTS", n(cur.get("wind_gusts_10m"), 0, " mph")),
            ("PRESSURE", pressure_inhg(cur.get("surface_pressure"))),
        ]
        for i, (label, value) in enumerate(rows):
            y = 154 + i*92
            round_rect(draw, (x1, y, w-55, y+70), 10, c["panel"])
            draw.text((x1+22, y+13), label, font=font(21, bold=True, mono=True), fill=c["muted"])
            draw.text((w-78, y+12), value, font=font(28, bold=True), fill=c["text"], anchor="ra")

    def _draw_today(self, draw, w, h, settings, p, c):
        loc, daily = p["location"], p.get("daily", {})
        self._header(draw, w, "Your Forecast", location_label(loc), c)
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        pops = daily.get("precipitation_probability_max") or []
        code = codes[0] if codes else None
        draw_weather_icon(draw, code, 92, 178, 1.45, c)
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
        self._header(draw, w, "Hour by Hour", location_label(p["location"]), c)
        hourly = p.get("hourly", {})
        indices = self._hour_indices(p)
        if not indices:
            draw.text((w//2, h//2), "Hourly forecast unavailable", font=font(36, bold=True), fill=c["text"], anchor="mm")
            return
        card_w = 184
        gap = 16
        start_x = (w - (len(indices)*card_w + (len(indices)-1)*gap)) // 2
        for col, i in enumerate(indices):
            x = start_x + col*(card_w+gap)
            round_rect(draw, (x, 140, x+card_w, 550), 16, c["panel"])
            try:
                t = dt.datetime.fromisoformat(hourly["time"][i]).strftime("%I %p").lstrip("0")
            except Exception:
                t = safe(hourly.get("time", [""])[i])
            draw.text((x+card_w//2, 170), t, font=font(25, bold=True, mono=True), fill=c["accent"], anchor="mm")
            hour_value = (hourly.get("time") or [None] * (i + 1))[i]
            is_night = self._hour_is_night(p, hour_value)
            draw_weather_icon(draw, hourly.get("weather_code", [None])[i], x+31, 205, 0.72, c, is_night=is_night)
            draw.text((x+card_w//2, 386), n(hourly.get("temperature_2m", [None])[i], 0, "°"), font=font(52, bold=True), fill=c["text"], anchor="mm")
            pop = hourly.get("precipitation_probability", [None])[i]
            draw.text((x+card_w//2, 454), f"RAIN {n(pop, 0, '%')}", font=font(20, bold=True, mono=True), fill=c["muted"], anchor="mm")
            wind = hourly.get("wind_speed_10m", [None])[i]
            draw.text((x+card_w//2, 503), f"WIND {n(wind, 0)}", font=font(18, mono=True), fill=c["muted"], anchor="mm")

    def _draw_nws_forecast(self, draw, w, h, settings, p, c):
        self._header(draw, w, "NWS Detailed Forecast", location_label(p["location"]), c)
        nws = p.get("nws") or {}
        periods = nws.get("periods") or []
        if not periods:
            draw.text((w//2, 270), "NWS FORECAST TEMPORARILY UNAVAILABLE", font=font(34, bold=True), fill=c["accent"], anchor="mm")
            msg = nws.get("error") or "WeatherStream will retry automatically."
            for row, line in enumerate(textwrap.wrap(msg, width=82)[:3]):
                draw.text((w//2, 330 + row*30), line, font=font(18), fill=c["muted"], anchor="mm")
            return
        cards = periods[:2]
        for i, period in enumerate(cards):
            x1 = 55 + i*610
            x2 = x1 + 555
            round_rect(draw, (x1, 128, x2, 558), 16, c["panel"], outline=c["muted"], width=2)
            draw.text((x1+24, 154), str(period.get("name", "Forecast")).upper(), font=font(29, bold=True, mono=True), fill=c["accent"])
            temp = period.get("temperature")
            temp_unit = period.get("temperatureUnit") or "F"
            draw.text((x2-24, 146), f"{safe(temp)}°{temp_unit}", font=font(38, bold=True), fill=c["text"], anchor="ra")
            short = period.get("shortForecast") or ""
            draw.text((x1+24, 204), short[:44], font=font(25, bold=True), fill=c["text"])
            wind = f"Wind {period.get('windDirection','')} {period.get('windSpeed','')}".strip()
            draw.text((x1+24, 246), wind, font=font(18, bold=True, mono=True), fill=c["muted"])
            detail = period.get("detailedForecast") or short or "Forecast unavailable."
            lines = textwrap.wrap(detail, width=43)[:8]
            for row, line in enumerate(lines):
                draw.text((x1+24, 294 + row*31), line, font=font(20), fill=c["text"])
        office = nws.get("office") or "NWS"
        draw.text((w-42, 590), f"Official forecast: National Weather Service • {office}", font=font(14, mono=True), fill=c["muted"], anchor="ra")

    def _draw_precipitation(self, draw, w, h, settings, p, c):
        self._header(draw, w, "Rain Chances", location_label(p["location"]), c)
        hourly = p.get("hourly", {})
        indices = self._hour_indices(p)
        if not indices:
            draw.text((w//2, h//2), "Precipitation forecast unavailable", font=font(34, bold=True), fill=c["text"], anchor="mm")
            return
        # Extend to eight points when data is available.
        first = indices[0]
        times = hourly.get("time") or []
        indices = list(range(first, min(first+8, len(times))))
        chart = (80, 160, w-70, 535)
        draw.line((chart[0], chart[3], chart[2], chart[3]), fill=c["muted"], width=2)
        for pct in (25, 50, 75, 100):
            y = chart[3] - int((chart[3]-chart[1]) * pct/100)
            draw.line((chart[0], y, chart[2], y), fill=c["panel"], width=1)
            draw.text((chart[0]-12, y), f"{pct}%", font=font(14, mono=True), fill=c["muted"], anchor="rm")
        gap = (chart[2]-chart[0]) / max(1, len(indices))
        bar_w = max(28, int(gap*0.55))
        for col, i in enumerate(indices):
            pop = (hourly.get("precipitation_probability") or [None]*len(times))[i]
            try: value = max(0, min(100, float(pop)))
            except Exception: value = 0
            cx = int(chart[0] + gap*(col+0.5))
            top = chart[3] - int((chart[3]-chart[1]) * value/100)
            round_rect(draw, (cx-bar_w//2, top, cx+bar_w//2, chart[3]), 7, c["panel"] if value < 40 else c["accent"])
            draw.text((cx, max(chart[1]+8, top-28)), f"{int(value)}%", font=font(18, bold=True, mono=True), fill=c["text"], anchor="mm")
            try: label = dt.datetime.fromisoformat(times[i]).strftime("%I %p").lstrip("0")
            except Exception: label = "--"
            draw.text((cx, chart[3]+28), label, font=font(17, bold=True, mono=True), fill=c["muted"], anchor="mm")
        daily_pop = (p.get("daily", {}).get("precipitation_probability_max") or [None])[0]
        draw.text((w//2, 595), f"TODAY'S MAXIMUM PRECIPITATION CHANCE: {n(daily_pop,0,'%')}", font=font(20, bold=True, mono=True), fill=c["accent"], anchor="mm")

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
        cards=[("HUMIDITY",n(rh,0,"%")),("DEW POINT",n(dew,0,"°")),("WIND",f"{safe(cur.get('wind_cardinal'))} {n(wind,0,' mph')}")]
        cw=(w-220)//3
        for i,(lab,val) in enumerate(cards):
            x=92+i*(cw+18); round_rect(draw,(x,458,x+cw,570),16,c["panel2"]); draw.text((x+18,480),lab,font=font(16,bold=True,mono=True),fill=c["muted"]); draw.text((x+cw-18,530),val,font=font(28,bold=True),fill=c["text"],anchor="rm")

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
        outlook=self.spc_manager.snapshot().get("outlook",{}) if self.spc_manager else {}
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

    def _draw_seven_day(self, draw, w, h, settings, p, c):
        self._header(draw, w, "7-Day Forecast", location_label(p["location"]), c)
        daily = p.get("daily", {})
        dates = daily.get("time") or []
        card_w = 158
        gap = 13
        count = min(7, len(dates))
        start_x = (w - (count*card_w + max(0,count-1)*gap)) // 2
        for i in range(count):
            x = start_x + i*(card_w+gap)
            round_rect(draw, (x, 132, x+card_w, 555), 14, c["panel"])
            try:
                day = dt.date.fromisoformat(dates[i]).strftime("%a").upper()
            except Exception:
                day = "DAY"
            draw.text((x+card_w//2, 166), day, font=font(27, bold=True, mono=True), fill=c["accent"], anchor="mm")
            draw_weather_icon(draw, (daily.get("weather_code") or [None]*count)[i], x+24, 205, 0.64, c)
            high = (daily.get("temperature_2m_max") or [None]*count)[i]
            low = (daily.get("temperature_2m_min") or [None]*count)[i]
            pop = (daily.get("precipitation_probability_max") or [None]*count)[i]
            draw.text((x+card_w//2, 382), n(high, 0, "°"), font=font(45, bold=True), fill=c["text"], anchor="mm")
            draw.text((x+card_w//2, 438), n(low, 0, "°"), font=font(33), fill=c["muted"], anchor="mm")
            draw.text((x+card_w//2, 507), f"RAIN {n(pop, 0, '%')}", font=font(17, bold=True, mono=True), fill=c["muted"], anchor="mm")

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
        radar = self.radar_manager.snapshot(view) if self.radar_manager else {"frames": [], "last_error": "Radar manager unavailable"}
        frames = radar.get("frames") or []
        radar_cfg = settings.get("radar", {})
        view_cfg = (radar_cfg.get("views") or {}).get(view) or {}
        map_box = (58, 116, w-58, h-112)

        round_rect(draw, (map_box[0]-8, map_box[1]-8, map_box[2]+8, map_box[3]+8), 14, c["panel2"], outline=c["muted"], width=2)
        if frames:
            frame_seconds = float(radar_cfg.get("frame_seconds", 0.8))
            idx = int(now / max(0.25, frame_seconds)) % len(frames)
            frame = frames[idx]
            radar_img = frame["image"].resize((map_box[2]-map_box[0], map_box[3]-map_box[1]), Image.Resampling.LANCZOS)
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
                self._draw_alert_polygons(draw, map_box, p, int(view_cfg.get("zoom", {"local":7,"regional":6,"wide":5}.get(view,7))), (snapshot or {}).get("alerts") or [])
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

            # Lower information strip helps distinguish the three radar products at a glance.
            zoom = int(view_cfg.get("zoom", {"local": 7, "regional": 6, "wide": 5}.get(view, 7)))
            info = f"ZOOM {zoom}   •   {len(frames)} FRAME LOOP   •   OPACITY {int(float(radar_cfg.get('opacity', 0.82))*100)}%   •   CONTRAST {float(radar_cfg.get('contrast', 1.25)):.2f}x"
            draw.rectangle((map_box[0]+16, map_box[3]-48, map_box[0]+690, map_box[3]-14), fill=(8,20,34))
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

    def _draw_alert_polygons(self, draw, map_box, p, zoom, alerts):
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
                    draw.line(points + [points[0]], fill=color, width=5, joint="curve")
                    # second dark edge improves visibility on bright radar returns.
                    draw.line(points + [points[0]], fill="#ffffff", width=1, joint="curve")

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
        alert = (snapshot.get("alerts") or [{}])[0]
        event = alert.get("event", "Weather Alert")
        color = self._alert_color(event)
        draw.rectangle((0, 0, w, 104), fill=color)
        draw.text((w//2, 52), "SEVERE WEATHER ALERT" if self._takeover_alert(settings, snapshot) else "WEATHER ALERT", font=font(43, bold=True), fill="#ffffff", anchor="mm")
        draw.text((w//2, 158), event.upper(), font=font(38, bold=True), fill=c["accent"], anchor="mm")
        headline = alert.get("headline") or event or "Weather alert in effect"
        lines = textwrap.wrap(headline, width=62)[:2]
        y = 214
        for line in lines:
            draw.text((w//2, y), line, font=font(27, bold=True), fill=c["text"], anchor="mm")
            y += 40
        area = alert.get("areaDesc", "")
        for line in textwrap.wrap(area, width=78)[:2]:
            draw.text((w//2, y+12), line, font=font(21), fill=c["muted"], anchor="mm")
            y += 30
        instruction = (alert.get("instruction") or alert.get("description") or "").replace("\n", " ")
        if instruction:
            y += 18
            for line in textwrap.wrap(instruction, width=92)[:3]:
                draw.text((w//2, y), line, font=font(18), fill=c["text"], anchor="mm")
                y += 27
        expiry = alert.get("expires") or alert.get("ends") or ""
        try:
            expiry = dt.datetime.fromisoformat(expiry.replace("Z", "+00:00")).astimezone().strftime("%I:%M %p").lstrip("0")
        except Exception:
            expiry = "--"
        draw.text((w//2, 548), f"SEVERITY {alert.get('severity','Unknown').upper()}   •   URGENCY {alert.get('urgency','Unknown').upper()}   •   EXPIRES {expiry}", font=font(19, bold=True, mono=True), fill=c["accent"], anchor="mm")
        draw.text((w//2, 584), "Source: National Weather Service", font=font(14, mono=True), fill=c["muted"], anchor="mm")

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
            spc=self.spc_manager.snapshot().get("outlook",{}) if self.spc_manager else {}; day1=spc.get("day1") or {}
            if int(day1.get("rank") or 0)>=2: parts.append(f"SPC OUTLOOK  {day1.get('name','').upper()}")
            highs=daily.get("temperature_2m_max") or []; lows=daily.get("temperature_2m_min") or []
            if highs and lows: parts.append(f"TODAY  HIGH {n(highs[0],0,'°')}  LOW {n(lows[0],0,'°')}")
            if self.history_store:
                hist=self.history_store.summary(loc.get("id") or settings.get("primary_location_id"),24)
                if hist.get("pressure_trend") and hist.get("pressure_trend")!="STEADY": parts.append(f"PRESSURE {hist.get('pressure_trend')}")
        else: parts.append("SETUP  OPEN THE ADMIN PAGE AND ADD A U.S. ZIP CODE")
        if alerts: parts.append(f"ALERT  {alerts[0].get('event','WEATHER ALERT').upper()}")
        else: parts.append("ALERTS  NO ACTIVE NWS ALERTS FOR PRIMARY LOCATION")
        return "     •     ".join(parts)+"     •     "

    def _draw_footer(self, draw, w, h, settings, snapshot, c, now):
        ticker_top = h - 86
        bug_w = 272
        gutter = 14
        # Base lower third.
        draw.rectangle((0, ticker_top, w, h), fill=c["ticker"])
        draw.rectangle((0, ticker_top, w, ticker_top+3), fill=c["accent"])

        # Draw the scrolling ticker into its own clipped layer. This is the v0.1.4
        # station-bug fix: ticker pixels physically cannot exist under the left bug.
        ticker_layer = Image.new("RGBA", (max(1, w - bug_w - gutter), 86), (0, 0, 0, 0))
        td = ImageDraw.Draw(ticker_layer)
        text = self._ticker_text(settings, snapshot)
        f = font(24, bold=True, mono=True)
        bbox = td.textbbox((0, 0), text, font=f)
        text_w = max(1, bbox[2] - bbox[0])
        area_w = ticker_layer.size[0]
        speed = 115
        offset = int(now * speed) % (text_w + 100)
        x = area_w - offset
        while x < area_w:
            td.text((x, 30), text, font=f, fill=c["text"])
            x += text_w + 100
        # Composite strictly to the right of the station bug.
        draw._image.paste(ticker_layer, (bug_w + gutter, ticker_top), ticker_layer)

        # Station/time bug is opaque and rendered last.
        clock = dt.datetime.fromtimestamp(now).strftime("%I:%M %p").lstrip("0")
        station = settings.get("station_name", "Roller Weather Network")
        callsign = (settings.get("station_callsign") or "").strip().upper()
        draw.rectangle((0, ticker_top, bug_w, h), fill=c["panel2"])
        draw.line((bug_w, ticker_top, bug_w, h), fill=c["muted"], width=2)
        draw.text((bug_w//2, ticker_top+23), clock, font=font(27, bold=True, mono=True), fill=c["accent"], anchor="mm")
        bug_station = callsign if callsign else station[:26]
        draw.text((bug_w//2, ticker_top+54), bug_station, font=font(15, bold=True, mono=True), fill=c["text"], anchor="mm")
        if callsign:
            draw.text((bug_w//2, ticker_top+73), station[:28], font=font(11, mono=True), fill=c["muted"], anchor="mm")
        if self.scheduled_update_active(settings, snapshot, now) and not self._is_takeover_active(settings, snapshot):
            draw.rectangle((bug_w-70, ticker_top+4, bug_w-5, ticker_top+20), fill=c["accent"])
            draw.text((bug_w-37, ticker_top+12), "LOCAL", font=font(9, bold=True, mono=True), fill=c["panel2"], anchor="mm")

