from __future__ import annotations

import base64
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


ADMIN_USER = os.environ.get("WEATHERSTREAM_ADMIN_USER", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("WEATHERSTREAM_ADMIN_PASSWORD", "")
TRUST_PROXY_HEADERS = os.environ.get("WEATHERSTREAM_TRUST_PROXY_HEADERS", "false").lower() in {"1", "true", "yes", "on"}


def authentication_enabled() -> bool:
    return bool(ADMIN_PASSWORD)


def valid_basic_authorization(value: str | None) -> bool:
    if not authentication_enabled():
        return True
    if not value or not value.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(value[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(username, ADMIN_USER) and hmac.compare_digest(password, ADMIN_PASSWORD)


def client_address(client_host: str | None, forwarded_for: str | None = None) -> str:
    if TRUST_PROXY_HEADERS and forwarded_for:
        candidate = forwarded_for.split(",", 1)[0].strip()
        if candidate:
            return candidate[:128]
    return (client_host or "unknown")[:128]


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int = 0


class SlidingWindowLimiter:
    """Small in-process limiter for expensive administrative operations.

    WeatherStream intentionally runs one Uvicorn worker, so process-local state is
    sufficient. Entries are pruned on every check and the key count is bounded.
    """

    def __init__(self, max_keys: int = 2048) -> None:
        self.max_keys = max(32, max_keys)
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()
        cutoff = now - max(1, window_seconds)
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= max(1, limit):
                retry = max(1, int(window_seconds - (now - bucket[0])) + 1)
                return RateLimitResult(False, retry)
            bucket.append(now)
            if len(self._hits) > self.max_keys:
                empty = [name for name, values in self._hits.items() if not values]
                for name in empty[: len(self._hits) - self.max_keys]:
                    self._hits.pop(name, None)
                if len(self._hits) > self.max_keys:
                    oldest = min(self._hits, key=lambda name: self._hits[name][-1] if self._hits[name] else 0)
                    if oldest != key:
                        self._hits.pop(oldest, None)
            return RateLimitResult(True)

