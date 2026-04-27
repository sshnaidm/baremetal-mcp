#!/usr/bin/env python3
"""
TTL-based in-memory cache for Redfish API responses.
"""

import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    """Simple dict-backed TTL cache. Not thread-safe; designed for asyncio single-loop use."""

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[Any, float]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = (value, time.monotonic() + ttl)

    def invalidate_prefix(self, prefix: str) -> int:
        """Delete all entries whose key starts with prefix. Returns count removed."""
        to_delete = [k for k in self._store if k.startswith(prefix)]
        for k in to_delete:
            del self._store[k]
        return len(to_delete)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        now = time.monotonic()
        return sum(1 for _, (_, exp) in self._store.items() if now <= exp)


# Module-level singleton shared by all tools
RESPONSE_CACHE = TTLCache()
