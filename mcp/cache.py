"""Simple in-memory TTL cache for VoyagerClient responses."""
from __future__ import annotations

import time
from typing import Any


class SimpleCache:
    """Thread-unsafe in-memory cache with per-key TTL.

    Suitable for single-threaded use within one VoyagerClient instance.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, ttl: float) -> tuple[bool, Any]:
        """Return (hit, value). hit=False when key is missing or expired."""
        entry = self._store.get(key)
        if entry is None:
            return False, None
        value, ts = entry
        if time.time() - ts > ttl:
            del self._store[key]
            return False, None
        return True, value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.time())

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()
