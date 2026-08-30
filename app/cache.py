from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from app.config import CONFIG_DIR


class CacheManager:
    def __init__(self, config_store) -> None:
        self.config_store = config_store
        self.root = CONFIG_DIR / "cache"
        self._stop = threading.Event(); self._wake = threading.Event(); self._thread: threading.Thread | None = None
        self._status = {"last_cleanup": None, "last_error": None, "removed_files": 0, "freed_bytes": 0}

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._thread = threading.Thread(target=self._run, name="cache-cleanup", daemon=True); self._thread.start()

    def stop(self) -> None:
        self._stop.set(); self._wake.set()

    def request_cleanup(self) -> None: self._wake.set()

    def size_bytes(self) -> int:
        total=0
        if not self.root.exists(): return 0
        for f in self.root.rglob('*'):
            try:
                if f.is_file(): total += f.stat().st_size
            except Exception: pass
        return total

    def clean(self) -> dict[str, Any]:
        cfg=(self.config_store.get().get('cache') or {}); cutoff=time.time()-max(1,int(cfg.get('retention_hours',48)))*3600
        removed=0; freed=0
        if self.root.exists():
            for f in self.root.rglob('*'):
                try:
                    if f.is_file() and f.stat().st_mtime < cutoff:
                        size=f.stat().st_size; f.unlink(); removed+=1; freed+=size
                except Exception: pass
        self._status.update({"last_cleanup":time.time(),"last_error":None,"removed_files":removed,"freed_bytes":freed})
        return {**self._status,"size_bytes":self.size_bytes()}

    def status(self) -> dict[str, Any]: return {**self._status,"size_bytes":self.size_bytes()}

    def _run(self) -> None:
        # One cleanup pass at startup, then every six hours when enabled.
        next_run=0.0
        while not self._stop.is_set():
            cfg=(self.config_store.get().get('cache') or {}); now=time.monotonic()
            if cfg.get('auto_cleanup',True) and now>=next_run:
                try: self.clean()
                except Exception as exc: self._status['last_error']=str(exc)
                next_run=now+21600
            self._wake.wait(timeout=60)
            if self._wake.is_set(): self._wake.clear(); next_run=0.0
