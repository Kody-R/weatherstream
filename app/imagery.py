from __future__ import annotations

import io
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from PIL import Image

from app.config import CONFIG_DIR


PRODUCTS = {
    "satellite": "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/CONUS/GEOCOLOR/1250x750.jpg",
    "lightning": "https://cdn.star.nesdis.noaa.gov/GOES19/GLM/CONUS/EXTENT3/latest.jpg",
}

# Persistent last-known-good imagery.
#
# Do NOT put this under /config/cache because WeatherStream's CacheManager
# intentionally deletes old cache files according to retention_hours.
IMAGERY_DIR = CONFIG_DIR / "imagery"


class ImageryManager:
    """Caches official NOAA GOES imagery with persistent last-known-good fallback."""

    def __init__(self, config_store) -> None:
        self.config_store = config_store

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()

        self._thread: threading.Thread | None = None

        self._images: dict[str, Image.Image] = {}
        self._updated: dict[str, float] = {}
        self._errors: dict[str, str] = {}

        IMAGERY_DIR.mkdir(parents=True, exist_ok=True)

        # Restore last-known-good images before attempting any network access.
        self._load_disk_cache()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()

        self._thread = threading.Thread(
            target=self._run,
            name="goes-imagery",
            daemon=True,
        )

        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    def request_refresh(self) -> None:
        self._wake.set()

    def _cache_path(self, name: str):
        return IMAGERY_DIR / f"{name}.jpg"

    def _load_disk_cache(self) -> None:
        """
        Load persistent last-known-good imagery at startup.

        This lets WeatherStream continue displaying the previous NOAA image
        when NOAA is temporarily unavailable during a container restart.
        """

        for name in PRODUCTS:
            path = self._cache_path(name)

            if not path.exists():
                continue

            try:
                with Image.open(path) as source:
                    image = source.convert("RGB")
                    image.load()

                with self._lock:
                    self._images[name] = image
                    self._updated[name] = path.stat().st_mtime

            except Exception:
                # A bad cache file should never stop WeatherStream.
                try:
                    path.unlink()
                except Exception:
                    pass

    def _save_disk_cache(self, name: str, body: bytes) -> None:
        """
        Atomically save a validated NOAA image.

        The image has already been successfully opened by Pillow before
        this method is called, so we know the response is actually an image.
        """

        path = self._cache_path(name)
        temp_path = path.with_suffix(".tmp")

        temp_path.write_bytes(body)
        temp_path.replace(path)

    def refresh_now(self) -> dict[str, Any]:
        settings = self.config_store.get()

        cfg = (
            ((settings.get("maps") or {}).get("engine2") or {})
        )

        engine_enabled = bool(cfg.get("enabled", True))
        layers = cfg.get("layers") or {}

        # Map Engine completely disabled: do not contact NOAA.
        if not engine_enabled:
            with self._lock:
                self._errors.pop("satellite", None)
                self._errors.pop("lightning", None)

            return self.status()

        for name, url in PRODUCTS.items():

            # IMPORTANT:
            # Do not download products the user has disabled.
            if not bool(layers.get(name, True)):
                with self._lock:
                    self._errors.pop(name, None)

                continue

            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": (
                            "WeatherStream/0.3.0 "
                            "(Roller Weather Network local weather display)"
                        )
                    },
                )

                with urllib.request.urlopen(req, timeout=20) as response:
                    # These products are far smaller than 12 MB.
                    # Read one extra byte so an unexpectedly huge response
                    # can be rejected rather than silently truncated.
                    body = response.read((12 * 1024 * 1024) + 1)

                if len(body) > 12 * 1024 * 1024:
                    raise ValueError(
                        f"{name} imagery response exceeded 12 MB"
                    )

                # Validate that NOAA really returned an image.
                image = Image.open(io.BytesIO(body))
                image.load()
                image = image.convert("RGB")

                # Save only after Pillow has successfully validated it.
                try:
                    self._save_disk_cache(name, body)
                except Exception:
                    # Disk caching is useful, but a disk-cache failure should
                    # not reject otherwise-good live imagery.
                    pass

                with self._lock:
                    self._images[name] = image
                    self._updated[name] = time.time()
                    self._errors.pop(name, None)

            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    message = (
                        "NOAA product temporarily unavailable "
                        "(HTTP 404)"
                    )
                else:
                    message = (
                        f"NOAA HTTP {exc.code}: "
                        f"{exc.reason}"
                    )

                with self._lock:
                    self._errors[name] = message

            except urllib.error.URLError as exc:
                with self._lock:
                    self._errors[name] = (
                        f"NOAA connection error: {exc.reason}"
                    )

            except Exception as exc:
                with self._lock:
                    self._errors[name] = str(exc)

        return self.status()

    def snapshot(self, name: str) -> Image.Image | None:
        with self._lock:
            image = self._images.get(name)

            return image.copy() if image else None

    def status(self) -> dict[str, Any]:
        settings = self.config_store.get()

        cfg = (
            ((settings.get("maps") or {}).get("engine2") or {})
        )

        engine_enabled = bool(cfg.get("enabled", True))
        layers = cfg.get("layers") or {}

        with self._lock:
            products = {}

            for name, url in PRODUCTS.items():
                enabled = (
                    engine_enabled
                    and bool(layers.get(name, True))
                )

                available = name in self._images
                error = self._errors.get(name)

                if not enabled:
                    state = "DISABLED"

                elif available and error:
                    # We still have last-known-good imagery.
                    state = "STALE"

                elif available:
                    state = "HEALTHY"

                elif error and "404" in error:
                    state = "UNAVAILABLE"

                elif error:
                    state = "ERROR"

                else:
                    state = "WAITING"

                products[name] = {
                    "enabled": enabled,
                    "available": available,
                    "state": state,
                    "last_update": self._updated.get(name),
                    "last_error": error,
                    "url": url,
                }

            return {
                "products": products,
            }

    def _run(self) -> None:
        while not self._stop.is_set():

            cfg = (
                (
                    (
                        self.config_store
                        .get()
                        .get("maps") or {}
                    )
                    .get("engine2") or {}
                )
            )

            if cfg.get("enabled", True):
                self.refresh_now()

            refresh_seconds = max(
                120,
                int(cfg.get("refresh_seconds", 300)),
            )

            self._wake.wait(timeout=refresh_seconds)
            self._wake.clear()
