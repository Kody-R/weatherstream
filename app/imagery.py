from __future__ import annotations

import io
import threading
import time
import urllib.request
from typing import Any

from PIL import Image


PRODUCTS = {
    "satellite": "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/con/GEOCOLOR/1800x1080.jpg",
    "lightning": "https://cdn.star.nesdis.noaa.gov/GOES19/GLM/SECTOR/con/EXTENT3/1800x1080.jpg",
}


class ImageryManager:
    """Caches official NOAA GOES imagery with last-known-good fallback."""

    def __init__(self, config_store) -> None:
        self.config_store = config_store; self._lock = threading.RLock(); self._stop = threading.Event(); self._wake = threading.Event()
        self._thread: threading.Thread | None = None; self._images: dict[str, Image.Image] = {}; self._updated: dict[str, float] = {}; self._errors: dict[str, str] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread = threading.Thread(target=self._run, name="goes-imagery", daemon=True); self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()
        if self._thread and self._thread is not threading.current_thread(): self._thread.join(timeout=5)

    def request_refresh(self) -> None: self._wake.set()

    def refresh_now(self) -> dict[str, Any]:
        for name, url in PRODUCTS.items():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "WeatherStream/0.3.0"})
                with urllib.request.urlopen(req, timeout=20) as response:
                    body = response.read(12 * 1024 * 1024)
                image = Image.open(io.BytesIO(body)); image.load(); image = image.convert("RGB")
                with self._lock: self._images[name] = image; self._updated[name] = time.time(); self._errors.pop(name, None)
            except Exception as exc:
                with self._lock: self._errors[name] = str(exc)
        return self.status()

    def snapshot(self, name: str) -> Image.Image | None:
        with self._lock:
            image = self._images.get(name)
            return image.copy() if image else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"products": {name: {"available": name in self._images, "last_update": self._updated.get(name), "last_error": self._errors.get(name), "url": url} for name, url in PRODUCTS.items()}}

    def _run(self) -> None:
        while not self._stop.is_set():
            cfg = ((self.config_store.get().get("maps") or {}).get("engine2") or {})
            if cfg.get("enabled", True): self.refresh_now()
            self._wake.wait(timeout=max(120, int(cfg.get("refresh_seconds", 300)))); self._wake.clear()
