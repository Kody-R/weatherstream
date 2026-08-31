from __future__ import annotations

import json
import threading
import time
from collections import Counter, deque
from typing import Any, Callable


class Observability:
    def __init__(self, event_limit: int = 300) -> None:
        self._lock = threading.RLock()
        self._started = time.time()
        self._counters: Counter[str] = Counter()
        self._route_counts: Counter[tuple[str, str, int]] = Counter()
        self._route_seconds: Counter[tuple[str, str]] = Counter()
        self._events: deque[dict[str, Any]] = deque(maxlen=max(50, event_limit))
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []

    def count_request(self, method: str, route: str, status: int, elapsed: float) -> None:
        method = method.upper()[:12]
        route = route[:160]
        with self._lock:
            self._counters["http_requests_total"] += 1
            self._route_counts[(method, route, int(status))] += 1
            self._route_seconds[(method, route)] += max(0.0, elapsed)

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def event(self, kind: str, message: str, **fields: Any) -> None:
        row = {"time": time.time(), "kind": kind[:48], "message": message[:500]}
        row.update({k: v for k, v in fields.items() if v is not None})
        with self._lock:
            self._events.append(row)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(dict(row))
            except Exception:
                self.increment("event_subscriber_errors_total")

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[-max(1, min(300, limit)) :]

    def prometheus(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            route_counts = dict(self._route_counts)
            route_seconds = dict(self._route_seconds)
        lines = [
            "# HELP weatherstream_uptime_seconds Seconds since this process started.",
            "# TYPE weatherstream_uptime_seconds gauge",
            f"weatherstream_uptime_seconds {max(0.0, time.time() - self._started):.3f}",
        ]
        for name, value in sorted(counters.items()):
            metric = "weatherstream_" + "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
            lines.extend([f"# TYPE {metric} counter", f"{metric} {value}"])
        lines.extend([
            "# HELP weatherstream_http_requests_by_route_total HTTP responses by method, route, and status.",
            "# TYPE weatherstream_http_requests_by_route_total counter",
        ])
        for (method, route, status), value in sorted(route_counts.items()):
            labels = f'method={json.dumps(method)},route={json.dumps(route)},status={json.dumps(str(status))}'
            lines.append(f"weatherstream_http_requests_by_route_total{{{labels}}} {value}")
        lines.extend([
            "# HELP weatherstream_http_route_seconds_total Total request time by method and route.",
            "# TYPE weatherstream_http_route_seconds_total counter",
        ])
        for (method, route), value in sorted(route_seconds.items()):
            labels = f'method={json.dumps(method)},route={json.dumps(route)}'
            lines.append(f"weatherstream_http_route_seconds_total{{{labels}}} {value:.6f}")
        lines.append("")
        return "\n".join(lines)


observability = Observability()
