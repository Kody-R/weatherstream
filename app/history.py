from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.config import CONFIG_DIR

DB_PATH = CONFIG_DIR / "weatherstream.db"


class HistoryStore:
    """Small SQLite observation history used by the broadcast director and graphs."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    temperature REAL,
                    apparent_temperature REAL,
                    humidity REAL,
                    pressure_hpa REAL,
                    wind_speed REAL,
                    wind_gust REAL,
                    precipitation REAL,
                    cloud_cover REAL,
                    weather_code INTEGER
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_location_time ON observations(location_id, observed_at)")
            conn.commit()

    def record(self, location_id: str, current: dict[str, Any], when: dt.datetime | None = None) -> None:
        when = when or dt.datetime.now(dt.timezone.utc)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations (
                    location_id, observed_at, temperature, apparent_temperature, humidity,
                    pressure_hpa, wind_speed, wind_gust, precipitation, cloud_cover, weather_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    location_id,
                    when.astimezone(dt.timezone.utc).isoformat(),
                    current.get("temperature_2m"),
                    current.get("apparent_temperature"),
                    current.get("relative_humidity_2m"),
                    current.get("surface_pressure"),
                    current.get("wind_speed_10m"),
                    current.get("wind_gusts_10m"),
                    current.get("precipitation"),
                    current.get("cloud_cover"),
                    current.get("weather_code"),
                ),
            )
            conn.commit()

    def recent(self, location_id: str, hours: int = 24, limit: int = 500) -> list[dict[str, Any]]:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=max(1, min(24 * 31, int(hours))))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM observations
                WHERE location_id = ? AND observed_at >= ?
                ORDER BY observed_at ASC LIMIT ?
                """,
                (location_id, cutoff.isoformat(), max(1, min(5000, int(limit)))),
            ).fetchall()
        return [dict(row) for row in rows]

    def summary(self, location_id: str, hours: int = 24) -> dict[str, Any]:
        rows = self.recent(location_id, hours=hours)
        if not rows:
            return {"samples": 0, "hours": hours}
        temps = [r["temperature"] for r in rows if r["temperature"] is not None]
        gusts = [r["wind_gust"] for r in rows if r["wind_gust"] is not None]
        precip = [r["precipitation"] for r in rows if r["precipitation"] is not None]
        pressure = [r["pressure_hpa"] for r in rows if r["pressure_hpa"] is not None]
        trend = None
        if len(pressure) >= 2:
            delta = pressure[-1] - pressure[0]
            trend = "RISING" if delta > 1.2 else "FALLING" if delta < -1.2 else "STEADY"
        return {
            "samples": len(rows),
            "hours": hours,
            "high": max(temps) if temps else None,
            "low": min(temps) if temps else None,
            "max_gust": max(gusts) if gusts else None,
            "precipitation": sum(max(0.0, float(x)) for x in precip) if precip else 0.0,
            "pressure_trend": trend,
            "first": rows[0]["observed_at"],
            "last": rows[-1]["observed_at"],
        }

    def cleanup(self, retention_days: int = 90) -> int:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(1, int(retention_days)))
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM observations WHERE observed_at < ?", (cutoff.isoformat(),))
            conn.commit()
            return int(cur.rowcount or 0)

    def status(self, primary_location_id: str | None = None) -> dict[str, Any]:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
            with self._lock, self._connect() as conn:
                total = conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
            summary = self.summary(primary_location_id, 24) if primary_location_id else {"samples": 0}
            return {"database": str(self.path), "size_bytes": size, "rows": total, "last_24h": summary}
        except Exception as exc:
            return {"database": str(self.path), "error": str(exc), "rows": 0}
