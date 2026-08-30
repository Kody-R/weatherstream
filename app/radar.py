from __future__ import annotations

import io
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

RAINVIEWER_META = "https://api.rainviewer.com/public/weather-maps.json"
OSM_TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TIGER_EXPORT = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/export"
CACHE_ROOT = Path(os.environ.get("WEATHERSTREAM_RADAR_CACHE", "/config/cache/radar"))
WEB_MERCATOR_ORIGIN = 20037508.342789244
VIEW_NAMES = ("local", "regional", "wide")


def _font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def latlon_to_world(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def world_to_latlon(x: float, y: float, zoom: int) -> tuple[float, float]:
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def _pixel_to_mercator(px: float, py: float, zoom: int) -> tuple[float, float]:
    world_pixels = (2 ** zoom) * 256.0
    mx = (px / world_pixels) * (WEB_MERCATOR_ORIGIN * 2.0) - WEB_MERCATOR_ORIGIN
    my = WEB_MERCATOR_ORIGIN - (py / world_pixels) * (WEB_MERCATOR_ORIGIN * 2.0)
    return mx, my


class RadarManager:
    """Fetches and caches multi-scale radar composites outside the video render loop."""

    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._frames: dict[str, list[dict[str, Any]]] = {name: [] for name in VIEW_NAMES}
        self._basemaps: dict[str, Image.Image | None] = {name: None for name in VIEW_NAMES}
        self._last_update: float | None = None
        self._last_error: str | None = None
        self._view_errors: dict[str, str | None] = {name: None for name in VIEW_NAMES}
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="radar-manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_refresh(self) -> None:
        self._wake.set()

    def snapshot(self, view: str = "local") -> dict[str, Any]:
        view = view if view in VIEW_NAMES else "local"
        with self._lock:
            return {
                "view": view,
                "frames": [{"time": f["time"], "image": f["image"].copy()} for f in self._frames.get(view, [])],
                "last_update": self._last_update,
                "last_error": self._view_errors.get(view) or self._last_error,
            }

    def map_snapshot(self, view: str = "regional") -> Image.Image | None:
        view = view if view in VIEW_NAMES else "regional"
        with self._lock:
            image = self._basemaps.get(view)
            return image.copy() if image is not None else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            counts = {name: len(self._frames.get(name, [])) for name in VIEW_NAMES}
            return {
                "frames_loaded": max(counts.values()) if counts else 0,
                "views": counts,
                "last_update": self._last_update,
                "last_error": self._last_error,
                "view_errors": dict(self._view_errors),
                "maps_ready": {name: self._basemaps.get(name) is not None for name in VIEW_NAMES},
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            settings = self.config_store.get()
            radar = settings.get("radar", {})
            maps = settings.get("maps", {})
            if radar.get("enabled", True):
                try:
                    self._refresh(settings)
                    with self._lock:
                        self._last_error = None
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)
                    self._load_cached(settings)
            elif maps.get("regional_map_enabled", True):
                # The v0.1.6 regional conditions map is useful even when animated
                # RainViewer radar is disabled. Build only the selected OSM/TIGER
                # basemap in that case so map rendering does not depend on radar.
                try:
                    self._refresh_basemap_only(settings)
                    with self._lock:
                        self._last_error = None
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)
                    loc = self._primary(settings)
                    if loc:
                        view = str(maps.get("regional_map_view", "regional"))
                        view = view if view in VIEW_NAMES else "regional"
                        views = radar.get("views") or {}
                        default_zoom = {"local": 7, "regional": 6, "wide": 5}[view]
                        zoom = max(3, min(7, int((views.get(view) or {}).get("zoom", default_zoom))))
                        self._load_cached_basemap(loc, view, zoom)
            refresh = max(120, int(radar.get("refresh_seconds", 300)))
            self._wake.wait(refresh)
            self._wake.clear()

    def _refresh_basemap_only(self, settings: dict[str, Any]) -> None:
        loc = self._primary(settings)
        if not loc:
            return
        maps = settings.get("maps", {})
        radar = settings.get("radar", {})
        view = str(maps.get("regional_map_view", "regional"))
        view = view if view in VIEW_NAMES else "regional"
        views = radar.get("views") or {}
        default_zoom = {"local": 7, "regional": 6, "wide": 5}[view]
        zoom = max(3, min(7, int((views.get(view) or {}).get("zoom", default_zoom))))
        width, height = 1180, 500
        ua = settings.get("nws_user_agent") or "WeatherStream/0.1.7.1 (Roller Weather Network local weather display)"
        headers = {"User-Agent": ua}
        with httpx.Client(timeout=18.0, follow_redirects=True, headers=headers) as client:
            base = self._build_basemap(client, float(loc["latitude"]), float(loc["longitude"]), zoom, width, height)
            if radar.get("show_boundaries", True):
                boundaries = self._build_boundary_overlay(
                    client, loc, view, float(loc["latitude"]), float(loc["longitude"]), zoom, width, height
                )
                if boundaries is not None:
                    base.alpha_composite(boundaries)
            map_rgb = base.convert("RGB")
            with self._lock:
                self._basemaps[view] = map_rgb
                self._last_update = time.time()
            self._save_basemap(loc, view, zoom, map_rgb)

    def _primary(self, settings: dict[str, Any]) -> dict[str, Any] | None:
        pid = settings.get("primary_location_id")
        return next((x for x in settings.get("locations", []) if x.get("id") == pid), None)

    def _refresh(self, settings: dict[str, Any]) -> None:
        loc = self._primary(settings)
        if not loc:
            return

        radar = settings.get("radar", {})
        frame_count = max(3, min(12, int(radar.get("frame_count", 8))))
        width, height = 1180, 500
        ua = settings.get("nws_user_agent") or "WeatherStream/0.1.7.1 (Roller Weather Network local weather display)"
        headers = {"User-Agent": ua}

        with httpx.Client(timeout=18.0, follow_redirects=True, headers=headers) as client:
            meta_resp = client.get(RAINVIEWER_META)
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            all_frames = (meta.get("radar") or {}).get("past") or []
            if not all_frames:
                raise RuntimeError("RainViewer returned no radar frames.")
            selected = all_frames[-frame_count:]

            enabled_views = self._enabled_views(settings)
            if not enabled_views:
                return

            built_any = False
            errors: dict[str, str | None] = {name: None for name in VIEW_NAMES}
            for view, zoom in enabled_views:
                try:
                    base = self._build_basemap(client, float(loc["latitude"]), float(loc["longitude"]), zoom, width, height)
                    boundaries = None
                    if radar.get("show_boundaries", True):
                        boundaries = self._build_boundary_overlay(
                            client, loc, view, float(loc["latitude"]), float(loc["longitude"]), zoom, width, height
                        )
                    map_base = base.copy()
                    if boundaries is not None:
                        map_base.alpha_composite(boundaries)
                    map_rgb = map_base.convert("RGB")
                    with self._lock:
                        self._basemaps[view] = map_rgb
                    self._save_basemap(loc, view, zoom, map_rgb)
                    composed: list[dict[str, Any]] = []
                    for frame in selected:
                        image = self._compose_frame(
                            client=client,
                            base=base,
                            boundary_overlay=boundaries,
                            host=meta["host"],
                            path=frame["path"],
                            lat=float(loc["latitude"]),
                            lon=float(loc["longitude"]),
                            zoom=zoom,
                            width=width,
                            height=height,
                            settings=settings,
                        )
                        composed.append({"time": int(frame["time"]), "image": image})
                        self._save_composite(loc, view, zoom, frame["time"], image)

                    with self._lock:
                        self._frames[view] = composed
                    built_any = built_any or bool(composed)
                    self._prune_cache(loc, view, zoom, keep=24)
                except Exception as exc:
                    errors[view] = str(exc)
                    self._load_cached_view(settings, view)

            with self._lock:
                self._view_errors = errors
                if built_any:
                    self._last_update = time.time()
                failed = [f"{name}: {err}" for name, err in errors.items() if err]
                self._last_error = "; ".join(failed) if failed else None

    def _enabled_views(self, settings: dict[str, Any]) -> list[tuple[str, int]]:
        views = (settings.get("radar") or {}).get("views") or {}
        result: list[tuple[str, int]] = []
        defaults = {"local": 7, "regional": 6, "wide": 5}
        for name in VIEW_NAMES:
            cfg = views.get(name) or {}
            if cfg.get("enabled", True):
                result.append((name, max(3, min(7, int(cfg.get("zoom", defaults[name]))))))
        return result

    def _viewport(self, lat: float, lon: float, zoom: int, width: int, height: int):
        cx, cy = latlon_to_world(lat, lon, zoom)
        px = cx * 256.0
        py = cy * 256.0
        left = px - width / 2
        top = py - height / 2
        min_tx = math.floor(left / 256)
        min_ty = math.floor(top / 256)
        max_tx = math.floor((left + width - 1) / 256)
        max_ty = math.floor((top + height - 1) / 256)
        return left, top, min_tx, min_ty, max_tx, max_ty

    def _get_tile(self, client: httpx.Client, url: str, cache_path: Path) -> Image.Image:
        if cache_path.exists():
            try:
                return Image.open(cache_path).convert("RGBA")
            except Exception:
                pass
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        response = client.get(url)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGBA")
        tmp = cache_path.with_suffix(".tmp.png")
        image.save(tmp, "PNG")
        tmp.replace(cache_path)
        return image

    def _build_basemap(self, client, lat, lon, zoom, width, height) -> Image.Image:
        left, top, min_tx, min_ty, max_tx, max_ty = self._viewport(lat, lon, zoom, width, height)
        out = Image.new("RGBA", (width, height), (22, 28, 36, 255))
        n = 2 ** zoom
        for ty in range(min_ty, max_ty + 1):
            if ty < 0 or ty >= n:
                continue
            for tx in range(min_tx, max_tx + 1):
                wrapped_x = tx % n
                cache = CACHE_ROOT / "osm" / str(zoom) / str(wrapped_x) / f"{ty}.png"
                tile = self._get_tile(client, OSM_TILE.format(z=zoom, x=wrapped_x, y=ty), cache)
                dx = int(tx * 256 - left)
                dy = int(ty * 256 - top)
                out.alpha_composite(tile, (dx, dy))
        return out

    def _build_boundary_overlay(self, client, loc, view, lat, lon, zoom, width, height) -> Image.Image | None:
        cache = CACHE_ROOT / "boundaries" / str(loc.get("postal_code", "local")) / f"{view}-z{zoom}-{width}x{height}.png"
        if cache.exists():
            try:
                return Image.open(cache).convert("RGBA")
            except Exception:
                pass

        left, top, *_ = self._viewport(lat, lon, zoom, width, height)
        right, bottom = left + width, top + height
        xmin, ymax = _pixel_to_mercator(left, top, zoom)
        xmax, ymin = _pixel_to_mercator(right, bottom, zoom)
        params = {
            "bbox": f"{xmin},{ymin},{xmax},{ymax}",
            "bboxSR": "3857",
            "imageSR": "3857",
            "size": f"{width},{height}",
            "format": "png32",
            "transparent": "true",
            "layers": "show:" + ",".join(str(i) for i in range(17)),
            "f": "image",
        }
        try:
            response = client.get(TIGER_EXPORT, params=params)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert("RGBA")
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp.png")
            image.save(tmp, "PNG")
            tmp.replace(cache)
            return image
        except Exception:
            return None

    def _compose_frame(self, client, base, boundary_overlay, host, path, lat, lon, zoom, width, height, settings):
        left, top, min_tx, min_ty, max_tx, max_ty = self._viewport(lat, lon, zoom, width, height)
        radar_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        n = 2 ** zoom
        for ty in range(min_ty, max_ty + 1):
            if ty < 0 or ty >= n:
                continue
            for tx in range(min_tx, max_tx + 1):
                wrapped_x = tx % n
                url = f"{host}{path}/256/{zoom}/{wrapped_x}/{ty}/2/1_1.png"
                cache = CACHE_ROOT / "rainviewer" / path.strip("/").replace("/", "_") / str(zoom) / str(wrapped_x) / f"{ty}.png"
                try:
                    tile = self._get_tile(client, url, cache)
                except Exception:
                    continue
                dx = int(tx * 256 - left)
                dy = int(ty * 256 - top)
                radar_layer.alpha_composite(tile, (dx, dy))

        radar_cfg = settings.get("radar", {})
        opacity = max(0.10, min(1.0, float(radar_cfg.get("opacity", 0.82))))
        contrast = max(0.50, min(2.50, float(radar_cfg.get("contrast", 1.25))))
        alpha = radar_layer.getchannel("A").point(lambda value: int(value * opacity))
        rgb = radar_layer.convert("RGB")
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
        rgb = ImageEnhance.Color(rgb).enhance(1.0 + max(0.0, contrast - 1.0) * 0.35)
        radar_layer = Image.merge("RGBA", (*rgb.split(), alpha))

        out = base.copy()
        out.alpha_composite(radar_layer)
        if boundary_overlay is not None:
            out.alpha_composite(boundary_overlay)

        if radar_cfg.get("show_city_markers", True):
            self._draw_location_markers(out, settings, left, top, zoom)
        else:
            self._draw_primary_crosshair(out)
        return out.convert("RGB")

    def _draw_location_markers(self, image: Image.Image, settings: dict[str, Any], left: float, top: float, zoom: int) -> None:
        draw = ImageDraw.Draw(image)
        primary_id = settings.get("primary_location_id")
        width, height = image.size
        placed: list[tuple[int, int, int, int]] = []

        locations = list(settings.get("locations", []))
        locations.sort(key=lambda x: 0 if x.get("id") == primary_id else 1)
        for item in locations:
            try:
                wx, wy = latlon_to_world(float(item["latitude"]), float(item["longitude"]), zoom)
            except Exception:
                continue
            x = int(wx * 256.0 - left)
            y = int(wy * 256.0 - top)
            if x < 18 or x > width - 18 or y < 18 or y > height - 18:
                continue
            is_primary = item.get("id") == primary_id
            radius = 8 if is_primary else 5
            fill = "#ffffff" if is_primary else "#ffd94a"
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=fill, outline="#101820", width=2)
            if is_primary:
                draw.line((x-16, y, x+16, y), fill="#ffffff", width=2)
                draw.line((x, y-16, x, y+16), fill="#ffffff", width=2)

            label = str(item.get("name") or item.get("postal_code") or "LOCAL").upper()
            text_font = _font(15 if is_primary else 13, bold=True)
            box = draw.textbbox((x + 12, y - 8), label, font=text_font, stroke_width=2)
            # Avoid stacking configured-city labels directly over one another.
            if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed):
                continue
            placed.append(box)
            draw.text((x + 12, y - 8), label, font=text_font, fill="#ffffff", stroke_width=3, stroke_fill="#101820")

    def _draw_primary_crosshair(self, image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        cx, cy = image.size[0] // 2, image.size[1] // 2
        draw.ellipse((cx-9, cy-9, cx+9, cy+9), fill="#ffffff", outline="#111111", width=3)
        draw.line((cx-18, cy, cx+18, cy), fill="#ffffff", width=2)
        draw.line((cx, cy-18, cx, cy+18), fill="#ffffff", width=2)

    def _basemap_path(self, loc, view, zoom):
        return CACHE_ROOT / "basemaps" / str(loc.get("postal_code", "local")) / f"{view}-z{zoom}.jpg"

    def _save_basemap(self, loc, view, zoom, image):
        path = self._basemap_path(loc, view, zoom)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "JPEG", quality=90)

    def _load_cached_basemap(self, loc, view, zoom):
        path = self._basemap_path(loc, view, zoom)
        if not path.exists():
            return
        try:
            image = Image.open(path).convert("RGB")
            with self._lock:
                self._basemaps[view] = image
        except Exception:
            pass

    def _cache_dir(self, loc, view, zoom):
        return CACHE_ROOT / "composites" / str(loc.get("postal_code", "local")) / f"{view}-z{zoom}"

    def _save_composite(self, loc, view, zoom, timestamp, image):
        directory = self._cache_dir(loc, view, zoom)
        directory.mkdir(parents=True, exist_ok=True)
        image.save(directory / f"{int(timestamp)}.jpg", "JPEG", quality=90)

    def _load_cached(self, settings):
        for view in VIEW_NAMES:
            self._load_cached_view(settings, view)

    def _load_cached_view(self, settings, view: str):
        loc = self._primary(settings)
        if not loc:
            return
        views = (settings.get("radar") or {}).get("views") or {}
        default_zoom = {"local": 7, "regional": 6, "wide": 5}[view]
        zoom = max(3, min(7, int((views.get(view) or {}).get("zoom", default_zoom))))
        count = max(3, min(12, int(settings.get("radar", {}).get("frame_count", 8))))
        self._load_cached_basemap(loc, view, zoom)
        directory = self._cache_dir(loc, view, zoom)
        if not directory.exists():
            return
        files = sorted(directory.glob("*.jpg"), key=lambda p: int(p.stem))[-count:]
        frames: list[dict[str, Any]] = []
        for path in files:
            try:
                frames.append({"time": int(path.stem), "image": Image.open(path).convert("RGB")})
            except Exception:
                pass
        if frames:
            with self._lock:
                self._frames[view] = frames

    def _prune_cache(self, loc, view, zoom, keep=24):
        directory = self._cache_dir(loc, view, zoom)
        if not directory.exists():
            return
        files = sorted(directory.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[keep:]:
            try:
                path.unlink()
            except Exception:
                pass
