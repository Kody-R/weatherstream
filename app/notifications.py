from __future__ import annotations

import queue
import threading
import time
from typing import Any

import httpx

from app.observability import observability
from app.webhook_security import validate_webhook_url


ALLOWED_EVENT_KINDS = {"severe", "source", "stream", "settings", "lifecycle", "refresh"}

class NotificationManager:
    """Bounded webhook delivery for selected structured WeatherStream events."""

    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self._stop = threading.Event()
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        self._thread: threading.Thread | None = None
        self._client = httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=False)
        self._lock = threading.RLock()
        self._last_sent_by_kind: dict[str, float] = {}
        self._last_success: float | None = None
        self._last_error: str | None = None
        self._sent = 0
        self._dropped = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        observability.subscribe(self._on_event)
        self._thread = threading.Thread(target=self._run, name="webhook-notifications", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        observability.unsubscribe(self._on_event)
        self._stop.set()
        try:
            self._queue.put_nowait({"_stop": True})
        except queue.Full:
            pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        self._client.close()

    def _cfg(self) -> dict[str, Any]:
        return dict(self.config_store.get().get("notifications") or {})

    def _on_event(self, event: dict[str, Any]) -> None:
        cfg = self._cfg()
        kind = str(event.get("kind") or "")
        selected = set(cfg.get("events") or [])
        if not cfg.get("enabled", False) or not cfg.get("webhook_url") or kind not in selected:
            return
        minimum = max(0, min(3600, int(cfg.get("minimum_interval_seconds", 30))))
        now = time.monotonic()
        with self._lock:
            previous = self._last_sent_by_kind.get(kind, 0.0)
            if minimum and now - previous < minimum:
                self._dropped += 1
                observability.increment("notification_cooldown_drops_total")
                return
            self._last_sent_by_kind[kind] = now
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            observability.increment("notification_queue_drops_total")

    def enqueue_test(self) -> None:
        cfg = self._cfg()
        if not cfg.get("enabled") or not cfg.get("webhook_url"):
            raise ValueError("Enable notifications and configure a webhook URL first.")
        validate_webhook_url(str(cfg.get("webhook_url") or ""), bool(cfg.get("allow_private_targets", False)))
        self._queue.put_nowait({"time": time.time(), "kind": "test", "message": "WeatherStream webhook test", "test": True})

    def _deliver(self, event: dict[str, Any]) -> None:
        cfg = self._cfg()
        url = validate_webhook_url(str(cfg.get("webhook_url") or ""), bool(cfg.get("allow_private_targets", False)))
        payload = {"product": "WeatherStream", "version": "0.2.5", "event": event}
        response = self._client.post(url, json=payload, headers={"User-Agent": "WeatherStream/0.2.5 Webhook"})
        response.raise_for_status()
        with self._lock:
            self._last_success = time.time()
            self._last_error = None
            self._sent += 1
        observability.increment("notifications_sent_total")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if event.get("_stop"):
                break
            try:
                self._deliver(event)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)[:500]
                observability.increment("notification_delivery_errors_total")

    def status(self) -> dict[str, Any]:
        cfg = self._cfg()
        with self._lock:
            return {
                "enabled": bool(cfg.get("enabled", False)),
                "configured": bool(cfg.get("webhook_url")),
                "events": list(cfg.get("events") or []),
                "queue_depth": self._queue.qsize(),
                "sent": self._sent,
                "dropped": self._dropped,
                "last_success": self._last_success,
                "last_error": self._last_error,
            }
