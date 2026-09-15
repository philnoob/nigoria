"""Per-user daily usage tracking for the Free tier (3 obfuscations / day).

Counts reset at UTC midnight. Persisted to a small JSON file so the limit
survives bot restarts. Access is guarded by a lock for concurrent interactions.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone


class UsageTracker:
    def __init__(self, path: str, daily_limit: int):
        self.path = path
        self.daily_limit = daily_limit
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh)
        os.replace(tmp, self.path)

    def _entry(self, user_id: int) -> dict:
        rec = self._data.get(str(user_id))
        today = self._today()
        if not rec or rec.get("date") != today:
            rec = {"date": today, "count": 0}
            self._data[str(user_id)] = rec
        return rec

    def remaining(self, user_id: int) -> int:
        with self._lock:
            rec = self._entry(user_id)
            return max(0, self.daily_limit - rec["count"])

    def try_consume(self, user_id: int) -> tuple[bool, int]:
        """Consume one use if available. Returns (allowed, remaining_after)."""
        with self._lock:
            rec = self._entry(user_id)
            if rec["count"] >= self.daily_limit:
                return False, 0
            rec["count"] += 1
            self._save()
            return True, max(0, self.daily_limit - rec["count"])
