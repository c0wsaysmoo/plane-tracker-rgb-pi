"""
Unified caching layer for API calls.

Provides:
  - TTLCache: A generic thread-safe time-to-live cache.
  - WeatherCache: Weather API wrapper with 1-hour TTL and 429 backoff.
  - FR24Cache: FlightRadar24 cache with 90s feed TTL and 30-min flight detail TTL.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    """
    Thread-safe dictionary cache with per-key TTL expiry.

    Usage:
        cache = TTLCache(default_ttl=3600)
        cache.set("key", value)
        hit = cache.get("key")  # returns value or None if expired
    """

    def __init__(self, default_ttl: float = 3600.0):
        """
        :param default_ttl: Default time-to-live in seconds for cached entries.
        """
        self._store: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_ts)
        self._lock = threading.Lock()
        self._default_ttl = default_ttl

    @property
    def default_ttl(self) -> float:
        return self._default_ttl

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve a cached value by key.
        Returns None if key doesn't exist or has expired.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry_ts = entry
            if time.time() > expiry_ts:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        Store a value with a TTL.
        :param key: Cache key.
        :param value: Value to store.
        :param ttl: Optional TTL override (seconds). Uses default_ttl if None.
        """
        ttl = ttl if ttl is not None else self._default_ttl
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def has(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        return self.get(key) is not None

    def invalidate(self, key: str) -> None:
        """Remove a specific key from cache."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from cache."""
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        """Return number of entries (including possibly expired ones)."""
        with self._lock:
            return len(self._store)

    def cleanup(self) -> int:
        """Remove expired entries. Returns number of entries removed."""
        now = time.time()
        removed = 0
        with self._lock:
            expired_keys = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired_keys:
                del self._store[k]
                removed += 1
        return removed


class RateLimiter:
    """
    Thread-safe rate limiter that enforces a minimum interval between operations.
    Supports a backoff mode for handling 429 (rate limit) responses.
    """

    def __init__(
        self,
        normal_interval: float = 3600.0,
        backoff_interval: float = 3600.0,
    ):
        """
        :param normal_interval: Minimum seconds between calls in normal mode.
        :param backoff_interval: Minimum seconds between calls in backoff mode.
        """
        self._normal_interval = normal_interval
        self._backoff_interval = backoff_interval
        self._last_call_ts: float = 0.0
        self._in_backoff: bool = False
        self._lock = threading.Lock()

    @property
    def in_backoff(self) -> bool:
        with self._lock:
            return self._in_backoff

    @property
    def last_call_ts(self) -> float:
        with self._lock:
            return self._last_call_ts

    def is_rate_limited(self) -> bool:
        """Return True if a call should be skipped due to rate limiting."""
        with self._lock:
            elapsed = time.time() - self._last_call_ts
            interval = self._backoff_interval if self._in_backoff else self._normal_interval
            return elapsed < interval

    def time_until_next_allowed(self) -> float:
        """Return seconds until the next call is allowed (0 if allowed now)."""
        with self._lock:
            elapsed = time.time() - self._last_call_ts
            interval = self._backoff_interval if self._in_backoff else self._normal_interval
            remaining = interval - elapsed
            return max(0.0, remaining)

    def record_call(self) -> None:
        """Record that an API call was just made."""
        with self._lock:
            self._last_call_ts = time.time()

    def enter_backoff(self) -> None:
        """Enter backoff mode (e.g., after receiving HTTP 429)."""
        with self._lock:
            self._in_backoff = True
        logger.warning("Rate limiter: entering backoff mode")

    def exit_backoff(self) -> None:
        """Exit backoff mode after a successful response."""
        with self._lock:
            if self._in_backoff:
                self._in_backoff = False
                logger.info("Rate limiter: backoff cleared, resuming normal interval")

    def reset(self) -> None:
        """Reset all state."""
        with self._lock:
            self._last_call_ts = 0.0
            self._in_backoff = False


class WeatherCache:
    """
    Cache layer for Tomorrow.io weather API.

    - Caches temperature/humidity data for 1 hour.
    - Caches forecast data for 1 hour.
    - Rate limits API calls to 1 per hour.
    - On 429 response, enters backoff mode (waits 1 hour before retrying).
    """

    CACHE_TTL = 3600.0  # 1 hour
    RATE_LIMIT_INTERVAL = 3600.0  # 1 hour between calls
    BACKOFF_INTERVAL = 3600.0  # 1 hour backoff on 429

    def __init__(self):
        self._cache = TTLCache(default_ttl=self.CACHE_TTL)
        self._rate_limiter = RateLimiter(
            normal_interval=self.RATE_LIMIT_INTERVAL,
            backoff_interval=self.BACKOFF_INTERVAL,
        )

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    @property
    def cache(self) -> TTLCache:
        return self._cache

    def get_cached_temperature(self) -> Optional[tuple]:
        """Get cached temperature/humidity tuple or None."""
        return self._cache.get("temperature")

    def set_cached_temperature(self, value: tuple) -> None:
        """Cache temperature/humidity tuple."""
        self._cache.set("temperature", value)

    def get_cached_forecast(self) -> Optional[list]:
        """Get cached forecast data or None."""
        return self._cache.get("forecast")

    def set_cached_forecast(self, value: list) -> None:
        """Cache forecast data."""
        self._cache.set("forecast", value)

    def should_call_api(self) -> bool:
        """
        Returns True if an API call is allowed (not rate limited).
        """
        return not self._rate_limiter.is_rate_limited()

    def record_api_call(self) -> None:
        """Record that an API call was made."""
        self._rate_limiter.record_call()

    def handle_429(self) -> None:
        """Handle a 429 response — enter backoff mode."""
        self._rate_limiter.enter_backoff()

    def handle_success(self) -> None:
        """Handle a successful response — exit backoff if needed."""
        self._rate_limiter.exit_backoff()


class FR24Cache:
    """
    Cache layer for FlightRadar24 API.

    - Live feed (get_flights): cached for 90 seconds.
    - Flight details (per flight_id): cached for 30 minutes.
    - Prevents redundant API calls by checking cache first.
    """

    FEED_TTL = 90.0  # 90 seconds for live feed polling
    FLIGHT_DETAIL_TTL = 1800.0  # 30 minutes for individual flight details
    FEED_POLL_INTERVAL = 90.0  # Minimum 90 seconds between feed polls

    def __init__(self):
        self._feed_cache = TTLCache(default_ttl=self.FEED_TTL)
        self._detail_cache = TTLCache(default_ttl=self.FLIGHT_DETAIL_TTL)
        self._feed_rate_limiter = RateLimiter(
            normal_interval=self.FEED_POLL_INTERVAL,
            backoff_interval=self.FEED_POLL_INTERVAL * 2,  # Double interval on backoff
        )
        # Per-key rate limiting: tracks last poll time per cache key
        self._per_key_last_poll: dict[str, float] = {}
        self._per_key_lock = threading.Lock()

    @property
    def feed_cache(self) -> TTLCache:
        return self._feed_cache

    @property
    def detail_cache(self) -> TTLCache:
        return self._detail_cache

    @property
    def feed_rate_limiter(self) -> RateLimiter:
        return self._feed_rate_limiter

    def get_cached_flights(self, cache_key: str) -> Optional[list]:
        """
        Get cached flight list for a given bounds/airline key.
        Returns None if no valid cache entry exists.
        """
        return self._feed_cache.get(cache_key)

    def set_cached_flights(self, cache_key: str, flights: list) -> None:
        """Cache a flight list result."""
        self._feed_cache.set(cache_key, flights)

    def get_cached_flight_details(self, flight_id: str) -> Optional[dict]:
        """
        Get cached flight details for a specific flight.
        Returns None if no valid cache entry exists (or expired).
        """
        return self._detail_cache.get(flight_id)

    def set_cached_flight_details(self, flight_id: str, details: dict) -> None:
        """Cache flight details for a specific flight."""
        self._detail_cache.set(flight_id, details)

    def should_poll_feed(self, cache_key: str = "") -> bool:
        """Returns True if enough time has elapsed to poll this specific feed key."""
        if not cache_key:
            return not self._feed_rate_limiter.is_rate_limited()
        with self._per_key_lock:
            last = self._per_key_last_poll.get(cache_key, 0.0)
            return (time.time() - last) >= self.FEED_POLL_INTERVAL

    def record_feed_poll(self, cache_key: str = "") -> None:
        """Record that a feed poll was made for this specific key."""
        if not cache_key:
            self._feed_rate_limiter.record_call()
            return
        with self._per_key_lock:
            self._per_key_last_poll[cache_key] = time.time()

    def make_feed_cache_key(
        self, bounds: Optional[dict] = None, airline: Optional[str] = None
    ) -> str:
        """
        Generate a cache key from the feed query parameters.
        """
        parts = []
        if bounds:
            parts.append(
                f"bounds:{bounds.get('tl_y','')},{bounds.get('tl_x','')},"
                f"{bounds.get('br_y','')},{bounds.get('br_x','')}"
            )
        if airline:
            parts.append(f"airline:{airline.upper()}")
        return "|".join(parts) if parts else "global"

    def cleanup(self) -> None:
        """Remove expired entries from both caches."""
        self._feed_cache.cleanup()
        self._detail_cache.cleanup()
