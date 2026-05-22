"""
Unified caching layer for API calls.
Provides TTLCache and FR24Cache.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    def __init__(self, default_ttl: float = 3600.0):
        self._store: dict = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl

    @property
    def default_ttl(self): return self._default_ttl

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None: return None
            value, expiry_ts = entry
            if time.time() > expiry_ts:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def has(self, key: str) -> bool: return self.get(key) is not None
    def invalidate(self, key: str) -> None:
        with self._lock: self._store.pop(key, None)
    def clear(self) -> None:
        with self._lock: self._store.clear()
    def size(self) -> int:
        with self._lock: return len(self._store)
    def cleanup(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
                removed += 1
        return removed


class FR24Cache:
    FEED_TTL            = 90.0
    FLIGHT_DETAIL_TTL   = 1800.0
    FEED_POLL_INTERVAL  = 90.0

    def __init__(self):
        self._feed_cache   = TTLCache(default_ttl=self.FEED_TTL)
        self._detail_cache = TTLCache(default_ttl=self.FLIGHT_DETAIL_TTL)
        self._per_key_last_poll: dict = {}
        self._per_key_lock = threading.Lock()

    @property
    def feed_cache(self): return self._feed_cache
    @property
    def detail_cache(self): return self._detail_cache

    def get_cached_flights(self, key): return self._feed_cache.get(key)
    def set_cached_flights(self, key, flights): self._feed_cache.set(key, flights)
    def get_cached_flight_details(self, fid): return self._detail_cache.get(fid)
    def set_cached_flight_details(self, fid, d): self._detail_cache.set(fid, d)

    def should_poll_feed(self, key):
        with self._per_key_lock:
            return (time.time() - self._per_key_last_poll.get(key, 0.0)) >= self.FEED_POLL_INTERVAL

    def record_feed_poll(self, key):
        with self._per_key_lock: self._per_key_last_poll[key] = time.time()

    def reset_feed_key(self, key):
        with self._per_key_lock: self._per_key_last_poll.pop(key, None)
        self._feed_cache.invalidate(key)

    def make_feed_cache_key(self, bounds=None, airline=None):
        parts = []
        if bounds:
            parts.append(f"bounds:{bounds.get('tl_y','')},{bounds.get('tl_x','')},{bounds.get('br_y','')},{bounds.get('br_x','')}")
        if airline:
            parts.append(f"airline:{airline.upper()}")
        return "|".join(parts) if parts else "global"

    def cleanup(self):
        self._feed_cache.cleanup()
        self._detail_cache.cleanup()
