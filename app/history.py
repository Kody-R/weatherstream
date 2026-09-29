from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from zoneinfo import ZoneInfo
from contextlib import closing
from pathlib import Path
from typing import Any

from app.config import CONFIG_DIR

DB_PATH = CONFIG_DIR / "weatherstream.db"


class HistoryStore:
    """Small SQLite observation history used by the broadcast director and graphs."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._revision = 0
        self._recent_cache: dict[tuple[str, int, int, int], list[dict[str, Any]]] = {}
        self._summary_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._row_count = 0
        self._last_cleanup_date: dt.date | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init_db(self) -> None:
        with self._lock, closing(self._connect()) as conn:
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
            columns = {row[1] for row in conn.execute("PRAGMA table_info(observations)").fetchall()}
            for name, kind in (("dewpoint_f","REAL"),("visibility_miles","REAL"),("source","TEXT"),("station_id","TEXT")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE observations ADD COLUMN {name} {kind}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_obs_location_time ON observations(location_id, observed_at)")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._row_count = int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
            conn.commit()

    def _invalidate_locked(self) -> None:
        self._revision += 1
        self._recent_cache.clear()
        self._summary_cache.clear()

    def record(self, location_id: str, current: dict[str, Any], when: dt.datetime | None = None) -> None:
        self.record_many([(location_id, current, when)])

    def record_many(self, observations: list[tuple[str, dict[str, Any], dt.datetime | None]]) -> int:
        """Write one refresh cycle in a single SQLite transaction."""
        rows = []
        for location_id, current, when in observations:
            stamp = (when or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat()
            rows.append((
                location_id, stamp, current.get("temperature_2m"), current.get("apparent_temperature"),
                current.get("relative_humidity_2m"), current.get("surface_pressure"), current.get("wind_speed_10m"),
                current.get("wind_gusts_10m"), current.get("precipitation"), current.get("cloud_cover"), current.get("weather_code"),
                current.get("dewpoint_f"), current.get("visibility_miles"), current.get("observation_source") or "Open-Meteo", current.get("station_id"),
            ))
        if not rows:
            return 0
        with self._lock, closing(self._connect()) as conn:
            inserted = 0
            for row in rows:
                exists = conn.execute("SELECT 1 FROM observations WHERE location_id=? AND observed_at=? LIMIT 1", (row[0], row[1])).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO observations (
                        location_id, observed_at, temperature, apparent_temperature, humidity,
                        pressure_hpa, wind_speed, wind_gust, precipitation, cloud_cover, weather_code,
                        dewpoint_f, visibility_miles, source, station_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, row
                )
                inserted += 1
            conn.commit()
            if inserted:
                self._row_count += inserted
                self._invalidate_locked()
        return inserted

    def recent(self, location_id: str, hours: int = 24, limit: int = 500) -> list[dict[str, Any]]:
        hours = max(1, min(24 * 3650, int(hours)))
        limit = max(1, min(5000, int(limit)))
        with self._lock:
            key = (location_id, hours, limit, self._revision)
            cached = self._recent_cache.get(key)
            if cached is not None:
                return list(cached)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM observations
                WHERE location_id = ? AND observed_at >= ?
                ORDER BY observed_at ASC LIMIT ?
                """,
                (location_id, cutoff.isoformat(), limit),
            ).fetchall()
            result = [dict(row) for row in rows]
            self._recent_cache[key] = result
            if len(self._recent_cache) > 128:
                self._recent_cache.pop(next(iter(self._recent_cache)))
        return list(result)

    def summary(self, location_id: str, hours: int = 24) -> dict[str, Any]:
        hours = max(1, min(24 * 3650, int(hours)))
        with self._lock:
            key = (location_id, hours, self._revision)
            cached = self._summary_cache.get(key)
            if cached is not None:
                return dict(cached)
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
        result = {
            "samples": len(rows),
            "hours": hours,
            "high": max(temps) if temps else None,
            "low": min(temps) if temps else None,
            "max_gust": max(gusts) if gusts else None,
            "precipitation": max([float(x) for x in precip] or [0.0]),
            "pressure_trend": trend,
            "pressure_delta_hpa": (pressure[-1] - pressure[0]) if len(pressure) >= 2 else None,
            "temperature_change": (temps[-1] - temps[0]) if len(temps) >= 2 else None,
            "first": rows[0]["observed_at"],
            "last": rows[-1]["observed_at"],
            "source": rows[-1].get("source"),
            "station_id": rows[-1].get("station_id"),
        }
        with self._lock:
            self._summary_cache[key] = result
            if len(self._summary_cache) > 128:
                self._summary_cache.pop(next(iter(self._summary_cache)))
        return dict(result)

    def since(self, location_id: str, cutoff: dt.datetime, limit: int = 5000) -> list[dict[str, Any]]:
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=dt.timezone.utc)
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM observations WHERE location_id=? AND observed_at>=? ORDER BY observed_at ASC LIMIT ?",
                (location_id, cutoff.astimezone(dt.timezone.utc).isoformat(), max(1, min(10000, int(limit)))),
            ).fetchall()
        return [dict(row) for row in rows]

    def today_summary(self, location_id: str, timezone_name: str | None = None) -> dict[str, Any]:
        try:
            tz = ZoneInfo(str(timezone_name or "UTC"))
        except Exception:
            tz = dt.timezone.utc
        local_now = dt.datetime.now(tz)
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.since(location_id, local_midnight.astimezone(dt.timezone.utc))
        if not rows:
            return {"samples": 0, "date": local_now.date().isoformat()}
        temps = [(r.get("temperature"), r.get("observed_at")) for r in rows if r.get("temperature") is not None]
        gusts = [(r.get("wind_gust"), r.get("observed_at")) for r in rows if r.get("wind_gust") is not None]
        pressures = [float(r["pressure_hpa"]) for r in rows if r.get("pressure_hpa") is not None]
        high = max(temps, key=lambda x: float(x[0])) if temps else (None, None)
        low = min(temps, key=lambda x: float(x[0])) if temps else (None, None)
        gust = max(gusts, key=lambda x: float(x[0])) if gusts else (None, None)
        trend = None
        delta_p = None
        if len(pressures) >= 2:
            delta_p = pressures[-1] - pressures[0]
            trend = "RISING" if delta_p > 1.2 else "FALLING" if delta_p < -1.2 else "STEADY"
        temp_change = float(temps[-1][0]) - float(temps[0][0]) if len(temps) >= 2 else None
        return {
            "samples": len(rows), "date": local_now.date().isoformat(),
            "high": high[0], "high_time": high[1], "low": low[0], "low_time": low[1],
            "max_gust": gust[0], "max_gust_time": gust[1], "temperature_change": temp_change,
            "pressure_trend": trend, "pressure_delta_hpa": delta_p,
            "first": rows[0].get("observed_at"), "last": rows[-1].get("observed_at"),
            "source": rows[-1].get("source"), "station_id": rows[-1].get("station_id"),
        }

    def cleanup(self, retention_days: int = 90) -> int:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(1, int(retention_days)))
        with self._lock, closing(self._connect()) as conn:
            cur = conn.execute("DELETE FROM observations WHERE observed_at < ?", (cutoff.isoformat(),))
            conn.commit()
            removed = int(cur.rowcount or 0)
            if removed:
                self._row_count = max(0, self._row_count - removed)
                self._invalidate_locked()
            self._last_cleanup_date = dt.datetime.now(dt.timezone.utc).date()
            return removed

    def cleanup_if_due(self, retention_days: int = 90) -> int:
        today = dt.datetime.now(dt.timezone.utc).date()
        with self._lock:
            if self._last_cleanup_date == today:
                return 0
        return self.cleanup(retention_days)

    def vacuum(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as conn:
            before = self.path.stat().st_size if self.path.exists() else 0
            conn.execute("VACUUM")
        after = self.path.stat().st_size if self.path.exists() else 0
        return {"ok": True, "before_bytes": before, "after_bytes": after}

    def replace_database(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            tmp = self.path.with_suffix(".restore.tmp")
            tmp.write_bytes(data)
            # Validate that the uploaded DB is readable and has the expected table.
            conn = sqlite3.connect(tmp)
            try:
                row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='observations'").fetchone()
                if not row:
                    raise ValueError("Backup database does not contain observations table")
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                if not integrity or str(integrity[0]).lower() != "ok":
                    raise ValueError("Backup database failed SQLite integrity check")
            finally:
                conn.close()
            tmp.replace(self.path)
            self._init_db()
            self._invalidate_locked()

    def status(self, primary_location_id: str | None = None) -> dict[str, Any]:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
            with self._lock:
                total = self._row_count
            summary = self.summary(primary_location_id, 24) if primary_location_id else {"samples": 0}
            return {"database": str(self.path), "size_bytes": size, "rows": total, "last_24h": summary}
        except Exception as exc:
            return {"database": str(self.path), "error": str(exc), "rows": 0}
