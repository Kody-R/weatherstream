from __future__ import annotations

import threading
import time
from typing import Any

from app.network import normalized_regions
from app.observability import observability


EVENT_TYPES: dict[str, dict[str, Any]] = {
    "tornado": {"name": "Tornado Watch", "terms": ("tornado",), "color": "#d61f2c"},
    "flood": {"name": "Flood Watch", "terms": ("flood", "flash flood"), "color": "#1f9d55"},
    "winter": {"name": "Winter Weather", "terms": ("winter", "snow", "ice storm", "blizzard"), "color": "#4aa8ff"},
    "wildfire": {"name": "Wildfire Watch", "terms": ("red flag", "fire weather", "wildfire"), "color": "#ff7a21"},
    "heat": {"name": "Extreme Heat", "terms": ("heat", "excessive heat"), "color": "#ffcc33"},
}


class WeatherEventManager:
    """Turns official alerts into region-scoped, cooldown-aware event channels."""

    def __init__(self, config_store, weather_manager) -> None:
        self.config_store = config_store
        self.weather_manager = weather_manager
        self._lock = threading.RLock()
        self._last_active: dict[tuple[str, str], float] = {}
        self._last_state: dict[tuple[str, str], bool] = {}

    @staticmethod
    def matches(event_type: str, alert: dict[str, Any]) -> bool:
        definition = EVENT_TYPES.get(event_type) or {}
        haystack = " ".join(str(alert.get(k) or "") for k in ("event", "headline", "description")).lower()
        return any(term in haystack for term in definition.get("terms", ()))

    def evaluate(self, region_id: str, event_type: str) -> dict[str, Any]:
        settings = self.config_store.get(); snapshot = self.weather_manager.snapshot()
        region = next((r for r in normalized_regions(settings) if r["id"] == region_id), None)
        cfg = settings.get("event_channels") or {}; now = time.time()
        alerts: list[dict[str, Any]] = []
        if region and cfg.get("enabled", True) and (cfg.get("types") or {}).get(event_type, True):
            for location_id in region.get("location_ids", []):
                alerts.extend((snapshot.get("alerts_by_location") or {}).get(location_id) or [])
        matched = [a for a in alerts if self.matches(event_type, a)]
        key = (region_id, event_type)
        with self._lock:
            if matched:
                self._last_active[key] = now
            last = self._last_active.get(key)
            previous = self._last_state.get(key)
            current = bool(matched)
            self._last_state[key] = current
        cooldown = max(0, int(cfg.get("cooldown_seconds", 7200)))
        cooling = bool(not matched and last and now - last < cooldown)
        if previous is not None and previous != current:
            observability.event("event", f"{(EVENT_TYPES.get(event_type) or {}).get('name',event_type)} channel {'activated' if current else 'entered cooldown'}", region_id=region_id, event_type=event_type, alert_count=len(matched))
        return {
            "active": bool(matched), "cooldown_active": cooling, "should_run": bool(matched or cooling),
            "alerts": matched, "alert_count": len(matched), "last_active": last,
            "region_id": region_id, "event_type": event_type, **(EVENT_TYPES.get(event_type) or {}),
        }

    def status(self) -> dict[str, Any]:
        settings = self.config_store.get()
        rows = []
        for region in normalized_regions(settings):
            for event_type in EVENT_TYPES:
                rows.append(self.evaluate(region["id"], event_type))
        return {"channels": rows, "active": sum(1 for row in rows if row["active"]), "definitions": EVENT_TYPES}
