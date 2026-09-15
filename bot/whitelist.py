"""Whitelist storage.

Whitelisted users are treated as Pro (unlimited, Pro-panel access) regardless
of their Discord role. Entries may expire after N days or last forever
(lifetime). Persisted to a small JSON file, guarded by a lock.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WhitelistStore:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

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
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def _expired(entry: dict) -> bool:
        exp = entry.get("expires_at")
        if not exp:
            return False  # lifetime
        try:
            return _now() >= datetime.fromisoformat(exp)
        except ValueError:
            return False

    def add(self, user_id: int, note: str, days: int | None,
            added_by: int) -> dict:
        with self._lock:
            expires_at = None
            if days and days > 0:
                expires_at = (_now() + timedelta(days=days)).isoformat()
            entry = {
                "note": note or "",
                "added_by": added_by,
                "added_at": _now().isoformat(),
                "expires_at": expires_at,
            }
            self._data[str(user_id)] = entry
            self._save()
            return entry

    def remove(self, user_id: int) -> bool:
        with self._lock:
            if str(user_id) in self._data:
                del self._data[str(user_id)]
                self._save()
                return True
            return False

    def get(self, user_id: int) -> dict | None:
        """Return the active entry, or None if missing/expired.

        Expired entries are NOT deleted here — ``pop_expired`` is the single
        place that removes them, so the expiry task can act (e.g. strip the
        Pro role) before they disappear.
        """
        with self._lock:
            entry = self._data.get(str(user_id))
            if entry is None or self._expired(entry):
                return None
            return dict(entry)

    def is_whitelisted(self, user_id: int) -> bool:
        return self.get(user_id) is not None

    def active_entries(self) -> list[tuple[int, dict]]:
        """All non-expired entries as (user_id, entry)."""
        with self._lock:
            return [(int(uid), dict(entry))
                    for uid, entry in self._data.items()
                    if not self._expired(entry)]

    def pop_expired(self) -> list[tuple[int, dict]]:
        """Remove and return every expired entry as (user_id, entry)."""
        with self._lock:
            expired = [(int(uid), dict(entry))
                       for uid, entry in list(self._data.items())
                       if self._expired(entry)]
            if expired:
                for uid, _ in expired:
                    self._data.pop(str(uid), None)
                self._save()
            return expired

    def mark_granted_role(self, user_id: int, granted: bool = True) -> None:
        """Record whether the bot assigned the Pro role for this entry, so the
        expiry task only removes roles it actually granted."""
        with self._lock:
            entry = self._data.get(str(user_id))
            if entry is not None:
                entry["granted_role"] = granted
                self._save()
