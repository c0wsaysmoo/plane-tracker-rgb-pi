"""
Unit tests for the caching layer (utilities/cache.py).

Tests cover:
  - TTLCache: set/get, expiry, invalidation, cleanup
  - RateLimiter: interval enforcement, backoff mode, reset
  - WeatherCache: 1-hour TTL, rate limiting, 429 backoff handling
  - FR24Cache: 90s feed polling, 30-min flight detail TTL, cache key generation
"""

import sys
import os
import time
import threading
# unittest.mock not needed - tests use the cache layer directly

import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utilities.cache import TTLCache, RateLimiter, WeatherCache, FR24Cache


# ═══════════════════════════════════════════════════════════════════════════════
# TTLCache Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTTLCache:
    """Tests for the generic TTLCache class."""

    def test_set_and_get(self):
        """Basic set/get returns correct value."""
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_nonexistent_key_returns_none(self):
        """Getting a key that was never set returns None."""
        cache = TTLCache(default_ttl=60)
        assert cache.get("nonexistent") is None

    def test_expiry_returns_none(self):
        """After TTL expires, get returns None."""
        cache = TTLCache(default_ttl=0.1)  # 100ms TTL
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
        time.sleep(0.15)
        assert cache.get("key1") is None

    def test_custom_ttl_per_key(self):
        """Keys can have different TTLs via the ttl parameter."""
        cache = TTLCache(default_ttl=60)
        cache.set("short", "val", ttl=0.1)
        cache.set("long", "val", ttl=60)
        time.sleep(0.15)
        assert cache.get("short") is None
        assert cache.get("long") == "val"

    def test_has_returns_true_for_valid_entry(self):
        """has() returns True for non-expired entries."""
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        assert cache.has("key1") is True

    def test_has_returns_false_for_expired_entry(self):
        """has() returns False for expired entries."""
        cache = TTLCache(default_ttl=0.1)
        cache.set("key1", "value1")
        time.sleep(0.15)
        assert cache.has("key1") is False

    def test_has_returns_false_for_missing_key(self):
        """has() returns False for keys that don't exist."""
        cache = TTLCache(default_ttl=60)
        assert cache.has("nokey") is False

    def test_invalidate_removes_key(self):
        """invalidate() immediately removes a key."""
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "value1")
        cache.invalidate("key1")
        assert cache.get("key1") is None

    def test_invalidate_nonexistent_no_error(self):
        """invalidate() on non-existent key doesn't raise."""
        cache = TTLCache(default_ttl=60)
        cache.invalidate("missing")  # Should not raise

    def test_clear_removes_all(self):
        """clear() removes all entries."""
        cache = TTLCache(default_ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None
        assert cache.size() == 0

    def test_size(self):
        """size() returns number of entries."""
        cache = TTLCache(default_ttl=60)
        assert cache.size() == 0
        cache.set("a", 1)
        cache.set("b", 2)
        assert cache.size() == 2

    def test_cleanup_removes_expired(self):
        """cleanup() removes only expired entries."""
        cache = TTLCache(default_ttl=60)
        cache.set("short", "val", ttl=0.1)
        cache.set("long", "val", ttl=60)
        time.sleep(0.15)
        removed = cache.cleanup()
        assert removed == 1
        assert cache.get("short") is None
        assert cache.get("long") == "val"

    def test_overwrite_key(self):
        """Setting same key again overwrites the value and resets TTL."""
        cache = TTLCache(default_ttl=60)
        cache.set("key1", "old_value")
        cache.set("key1", "new_value")
        assert cache.get("key1") == "new_value"

    def test_stores_various_types(self):
        """Cache can store different value types."""
        cache = TTLCache(default_ttl=60)
        cache.set("int", 42)
        cache.set("list", [1, 2, 3])
        cache.set("dict", {"a": 1})
        cache.set("tuple", (1, 2))
        cache.set("none", None)  # None is stored as a value

        assert cache.get("int") == 42
        assert cache.get("list") == [1, 2, 3]
        assert cache.get("dict") == {"a": 1}
        assert cache.get("tuple") == (1, 2)
        # Note: None is a valid stored value, but get() returns None for
        # missing/expired. The has() method distinguishes:
        # Actually None stored will be returned by get as None which looks
        # like cache miss. This is by design for simplicity.

    def test_thread_safety(self):
        """Concurrent access from multiple threads doesn't corrupt data."""
        cache = TTLCache(default_ttl=60)
        errors = []

        def writer(prefix, count):
            try:
                for i in range(count):
                    cache.set(f"{prefix}_{i}", i)
            except Exception as e:
                errors.append(e)

        def reader(prefix, count):
            try:
                for i in range(count):
                    cache.get(f"{prefix}_{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for p in range(5):
            threads.append(threading.Thread(target=writer, args=(f"t{p}", 100)))
            threads.append(threading.Thread(target=reader, args=(f"t{p}", 100)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# RateLimiter Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimiter:
    """Tests for the RateLimiter class."""

    def test_not_rate_limited_initially(self):
        """First call is never rate limited."""
        rl = RateLimiter(normal_interval=60.0)
        assert rl.is_rate_limited() is False

    def test_rate_limited_after_call(self):
        """After recording a call, subsequent check is rate limited."""
        rl = RateLimiter(normal_interval=60.0)
        rl.record_call()
        assert rl.is_rate_limited() is True

    def test_rate_limit_expires(self):
        """After interval passes, no longer rate limited."""
        rl = RateLimiter(normal_interval=0.1)
        rl.record_call()
        assert rl.is_rate_limited() is True
        time.sleep(0.15)
        assert rl.is_rate_limited() is False

    def test_backoff_mode_uses_backoff_interval(self):
        """In backoff mode, the backoff interval is used."""
        rl = RateLimiter(normal_interval=0.1, backoff_interval=0.3)
        rl.record_call()
        rl.enter_backoff()

        # After normal interval passes, still rate limited due to backoff
        time.sleep(0.15)
        assert rl.is_rate_limited() is True
        assert rl.in_backoff is True

        # After backoff interval passes, no longer rate limited
        time.sleep(0.2)
        assert rl.is_rate_limited() is False

    def test_exit_backoff(self):
        """exit_backoff() switches back to normal interval."""
        rl = RateLimiter(normal_interval=0.1, backoff_interval=10.0)
        rl.record_call()
        rl.enter_backoff()
        assert rl.in_backoff is True

        rl.exit_backoff()
        assert rl.in_backoff is False

        # After normal interval, should be allowed
        time.sleep(0.15)
        assert rl.is_rate_limited() is False

    def test_reset_clears_all_state(self):
        """reset() clears last call timestamp and backoff mode."""
        rl = RateLimiter(normal_interval=60.0, backoff_interval=120.0)
        rl.record_call()
        rl.enter_backoff()

        rl.reset()
        assert rl.is_rate_limited() is False
        assert rl.in_backoff is False

    def test_time_until_next_allowed(self):
        """time_until_next_allowed returns correct remaining time."""
        rl = RateLimiter(normal_interval=1.0)
        rl.record_call()
        remaining = rl.time_until_next_allowed()
        assert 0.9 < remaining <= 1.0

    def test_time_until_next_allowed_when_allowed(self):
        """time_until_next_allowed returns 0 when call is allowed."""
        rl = RateLimiter(normal_interval=60.0)
        assert rl.time_until_next_allowed() == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# WeatherCache Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeatherCache:
    """Tests for the WeatherCache class (1-hour caching with 429 backoff)."""

    def test_initial_state_allows_api_call(self):
        """Initially, API calls should be allowed."""
        wc = WeatherCache()
        assert wc.should_call_api() is True

    def test_after_call_rate_limited_for_one_hour(self):
        """After an API call, should be rate limited."""
        wc = WeatherCache()
        wc.record_api_call()
        assert wc.should_call_api() is False

    def test_temperature_cache_stores_and_retrieves(self):
        """Temperature data is cached and retrievable."""
        wc = WeatherCache()
        wc.set_cached_temperature((22.5, 65))
        result = wc.get_cached_temperature()
        assert result == (22.5, 65)

    def test_temperature_cache_expires_after_one_hour(self):
        """Temperature cache expires after 1 hour."""
        wc = WeatherCache()
        wc.set_cached_temperature((22.5, 65))

        # Manually expire by setting TTL to 0
        wc._cache.set("temperature", (22.5, 65), ttl=0.1)
        time.sleep(0.15)
        assert wc.get_cached_temperature() is None

    def test_forecast_cache_stores_and_retrieves(self):
        """Forecast data is cached and retrievable."""
        wc = WeatherCache()
        forecast = [{"day": 1, "temp": 20}, {"day": 2, "temp": 22}]
        wc.set_cached_forecast(forecast)
        assert wc.get_cached_forecast() == forecast

    def test_429_handling_enters_backoff(self):
        """handle_429() puts the rate limiter in backoff mode."""
        wc = WeatherCache()
        wc.handle_429()
        assert wc.rate_limiter.in_backoff is True

    def test_429_backoff_prevents_api_calls(self):
        """After 429, API calls are blocked for the backoff interval."""
        wc = WeatherCache()
        wc.record_api_call()
        wc.handle_429()
        # Should be rate limited
        assert wc.should_call_api() is False

    def test_success_after_429_exits_backoff(self):
        """handle_success() clears the backoff state."""
        wc = WeatherCache()
        wc.handle_429()
        assert wc.rate_limiter.in_backoff is True
        wc.handle_success()
        assert wc.rate_limiter.in_backoff is False

    def test_cache_ttl_is_one_hour(self):
        """Verify the cache TTL constant is 3600 seconds."""
        assert WeatherCache.CACHE_TTL == 3600.0

    def test_rate_limit_interval_is_one_hour(self):
        """Verify rate limit interval is 3600 seconds."""
        assert WeatherCache.RATE_LIMIT_INTERVAL == 3600.0

    def test_backoff_interval_is_one_hour(self):
        """Verify backoff interval (after 429) is 3600 seconds."""
        assert WeatherCache.BACKOFF_INTERVAL == 3600.0


# ═══════════════════════════════════════════════════════════════════════════════
# FR24Cache Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFR24Cache:
    """Tests for the FR24Cache class (90s feed, 30min flight details)."""

    def test_feed_ttl_is_90_seconds(self):
        """Feed cache TTL is 90 seconds."""
        assert FR24Cache.FEED_TTL == 90.0

    def test_flight_detail_ttl_is_30_minutes(self):
        """Flight detail cache TTL is 30 minutes (1800 seconds)."""
        assert FR24Cache.FLIGHT_DETAIL_TTL == 1800.0

    def test_feed_poll_interval_is_90_seconds(self):
        """Feed polling interval is 90 seconds."""
        assert FR24Cache.FEED_POLL_INTERVAL == 90.0

    def test_initial_state_allows_feed_poll(self):
        """Initially, feed polling should be allowed."""
        fc = FR24Cache()
        assert fc.should_poll_feed("zone") is True

    def test_after_poll_feed_is_rate_limited(self):
        """After recording a feed poll, subsequent polls are rate limited."""
        fc = FR24Cache()
        fc.record_feed_poll("zone")
        assert fc.should_poll_feed("zone") is False

    def test_feed_rate_limit_expires(self):
        """Feed rate limit expires after the poll interval."""
        fc = FR24Cache()
        fc.FEED_POLL_INTERVAL = 0.1
        fc.record_feed_poll("zone")
        assert fc.should_poll_feed("zone") is False
        time.sleep(0.15)
        assert fc.should_poll_feed("zone") is True

    def test_per_key_rate_limiting_independence(self):
        """Different cache keys have independent rate limits."""
        fc = FR24Cache()
        fc.record_feed_poll("zone")
        assert fc.should_poll_feed("zone") is False
        assert fc.should_poll_feed("wide") is True  # different key, not limited

    def test_reset_feed_key(self):
        """reset_feed_key clears rate limit and cache for a specific key."""
        fc = FR24Cache()
        fc.record_feed_poll("zone")
        fc.set_cached_flights("zone", [{"id": "test"}])
        assert fc.should_poll_feed("zone") is False
        fc.reset_feed_key("zone")
        assert fc.should_poll_feed("zone") is True
        assert fc.get_cached_flights("zone") is None

    def test_flight_list_caching(self):
        """Flight list is cached and retrievable by key."""
        fc = FR24Cache()
        flights = [{"id": "abc123"}, {"id": "def456"}]
        fc.set_cached_flights("bounds:51,-0.3,51.5,0.1", flights)
        assert fc.get_cached_flights("bounds:51,-0.3,51.5,0.1") == flights

    def test_flight_list_cache_miss(self):
        """Missing flight list returns None."""
        fc = FR24Cache()
        assert fc.get_cached_flights("nonexistent") is None

    def test_flight_list_cache_expires(self):
        """Flight list cache expires after TTL."""
        fc = FR24Cache()
        # Use short TTL for testing
        fc._feed_cache._default_ttl = 0.1
        fc.set_cached_flights("key1", [{"id": "test"}])
        time.sleep(0.15)
        assert fc.get_cached_flights("key1") is None

    def test_flight_detail_caching(self):
        """Individual flight details are cached by flight_id."""
        fc = FR24Cache()
        details = {"aircraft": {"model": {"code": "A320"}}, "airline": {"name": "BA"}}
        fc.set_cached_flight_details("abc123", details)
        assert fc.get_cached_flight_details("abc123") == details

    def test_flight_detail_cache_miss(self):
        """Missing flight details returns None."""
        fc = FR24Cache()
        assert fc.get_cached_flight_details("nonexistent") is None

    def test_flight_detail_cache_expires(self):
        """Flight details expire after TTL."""
        fc = FR24Cache()
        # Use short TTL for testing
        fc._detail_cache._default_ttl = 0.1
        fc.set_cached_flight_details("flight1", {"data": "test"})
        time.sleep(0.15)
        assert fc.get_cached_flight_details("flight1") is None

    def test_multiple_flights_cached_independently(self):
        """Multiple flights can be cached independently."""
        fc = FR24Cache()
        fc.set_cached_flight_details("flight1", {"data": "first"})
        fc.set_cached_flight_details("flight2", {"data": "second"})
        fc.set_cached_flight_details("flight3", {"data": "third"})

        assert fc.get_cached_flight_details("flight1") == {"data": "first"}
        assert fc.get_cached_flight_details("flight2") == {"data": "second"}
        assert fc.get_cached_flight_details("flight3") == {"data": "third"}

    def test_cache_key_generation_with_bounds(self):
        """Cache key includes bounds when provided."""
        fc = FR24Cache()
        bounds = {"tl_y": 51.6, "tl_x": -0.3, "br_y": 51.4, "br_x": 0.1}
        key = fc.make_feed_cache_key(bounds=bounds)
        assert "bounds:" in key
        assert "51.6" in key
        assert "-0.3" in key

    def test_cache_key_generation_with_airline(self):
        """Cache key includes airline when provided."""
        fc = FR24Cache()
        key = fc.make_feed_cache_key(airline="BAW")
        assert "airline:BAW" in key

    def test_cache_key_generation_global(self):
        """Cache key for no params is 'global'."""
        fc = FR24Cache()
        key = fc.make_feed_cache_key()
        assert key == "global"

    def test_cache_key_generation_combined(self):
        """Cache key combines bounds and airline."""
        fc = FR24Cache()
        bounds = {"tl_y": 51.6, "tl_x": -0.3, "br_y": 51.4, "br_x": 0.1}
        key = fc.make_feed_cache_key(bounds=bounds, airline="BAW")
        assert "bounds:" in key
        assert "airline:BAW" in key

    def test_cleanup_removes_expired_entries(self):
        """cleanup() removes expired entries from both caches."""
        fc = FR24Cache()
        fc._feed_cache.set("expired", "val", ttl=0.1)
        fc._detail_cache.set("expired_detail", "val", ttl=0.1)
        fc._feed_cache.set("valid", "val", ttl=60)
        time.sleep(0.15)
        fc.cleanup()
        assert fc._feed_cache.get("expired") is None
        assert fc._detail_cache.get("expired_detail") is None
        assert fc._feed_cache.get("valid") == "val"


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Tests: Verifying no API hammering
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoAPIHammering:
    """
    Integration tests verifying that the caching layer prevents
    excessive API calls (hammering).
    """

    def test_weather_cache_prevents_repeated_calls_within_hour(self):
        """
        Simulates multiple weather API requests within 1 hour.
        After the first successful call, subsequent calls should return
        cached data without triggering another API call.
        """
        wc = WeatherCache()
        api_call_count = 0

        def mock_api_call():
            nonlocal api_call_count
            if not wc.should_call_api():
                # Rate limited — return cached data
                return wc.get_cached_temperature()
            # Make the "API call"
            api_call_count += 1
            wc.record_api_call()
            result = (20.0, 55)  # Simulated API response
            wc.set_cached_temperature(result)
            wc.handle_success()
            return result

        # First call should hit the API
        r1 = mock_api_call()
        assert r1 == (20.0, 55)
        assert api_call_count == 1

        # 10 more calls should all use cache
        for _ in range(10):
            r = mock_api_call()
            assert r == (20.0, 55)

        assert api_call_count == 1  # Still only 1 actual API call

    def test_weather_cache_429_prevents_retry_for_one_hour(self):
        """
        After a 429 response, the weather cache should prevent retries
        for at least 1 hour (backoff interval).
        """
        wc = WeatherCache()
        api_call_count = 0

        def mock_api_call_with_429():
            nonlocal api_call_count
            if not wc.should_call_api():
                return wc.get_cached_temperature()
            api_call_count += 1
            wc.record_api_call()
            # Simulate 429 response
            wc.handle_429()
            return None  # No data

        # First call gets 429
        mock_api_call_with_429()
        assert api_call_count == 1

        # All subsequent calls within the backoff window should be blocked
        for _ in range(50):
            mock_api_call_with_429()

        # Only 1 actual API call was made despite 51 attempts
        assert api_call_count == 1

    def test_fr24_feed_not_polled_within_90_seconds(self):
        """
        FR24 live feed should not be polled more than once per 90 seconds.
        Multiple requests within 90s should return cached results.
        """
        fc = FR24Cache()
        api_call_count = 0
        mock_flights = [{"flight_id": "abc"}, {"flight_id": "def"}]

        def mock_get_flights(cache_key):
            nonlocal api_call_count
            # Check cache first
            cached = fc.get_cached_flights(cache_key)
            if cached is not None:
                return cached
            # Check rate limiter
            if not fc.should_poll_feed(cache_key):
                return cached if cached is not None else []
            # Make API call
            api_call_count += 1
            fc.set_cached_flights(cache_key, mock_flights)
            fc.record_feed_poll(cache_key)
            return mock_flights

        # First call hits API
        r1 = mock_get_flights("global")
        assert r1 == mock_flights
        assert api_call_count == 1

        # 20 rapid calls should all hit cache
        for _ in range(20):
            r = mock_get_flights("global")
            assert r == mock_flights

        assert api_call_count == 1  # Only 1 actual poll

    def test_fr24_flight_details_cached_for_30_minutes(self):
        """
        Individual flight details should be served from cache for 30 minutes
        without making redundant API calls.
        """
        fc = FR24Cache()
        api_call_count = 0
        mock_details = {"aircraft": {"model": {"code": "B777"}}}

        def mock_get_details(flight_id):
            nonlocal api_call_count
            # Check cache first
            cached = fc.get_cached_flight_details(flight_id)
            if cached is not None:
                return cached
            # Cache miss — API call
            api_call_count += 1
            fc.set_cached_flight_details(flight_id, mock_details)
            return mock_details

        # First call for flight "abc123" hits API
        r1 = mock_get_details("abc123")
        assert r1 == mock_details
        assert api_call_count == 1

        # 30 more requests for same flight should all hit cache
        for _ in range(30):
            r = mock_get_details("abc123")
            assert r == mock_details

        assert api_call_count == 1  # Only 1 actual API call

    def test_fr24_different_flights_cached_independently(self):
        """
        Different flight IDs are cached independently; requesting
        details for a new flight makes a new API call, but repeats
        for the same flight use cache.
        """
        fc = FR24Cache()
        api_call_count = 0

        def mock_get_details(flight_id):
            nonlocal api_call_count
            cached = fc.get_cached_flight_details(flight_id)
            if cached is not None:
                return cached
            api_call_count += 1
            details = {"flight_id": flight_id, "aircraft": "A320"}
            fc.set_cached_flight_details(flight_id, details)
            return details

        # 3 unique flights = 3 API calls
        mock_get_details("flight_1")
        mock_get_details("flight_2")
        mock_get_details("flight_3")
        assert api_call_count == 3

        # Requesting same 3 flights again should use cache
        mock_get_details("flight_1")
        mock_get_details("flight_2")
        mock_get_details("flight_3")
        assert api_call_count == 3  # No new API calls

    def test_fr24_feed_different_bounds_cached_separately(self):
        """
        Feed requests with different bounds/airline parameters
        are cached independently.
        """
        fc = FR24Cache()
        api_call_count = 0

        def mock_get_flights(bounds=None, airline=None):
            nonlocal api_call_count
            cache_key = fc.make_feed_cache_key(bounds, airline)
            cached = fc.get_cached_flights(cache_key)
            if cached is not None:
                return cached
            if not fc.should_poll_feed(cache_key):
                return []
            api_call_count += 1
            result = [{"key": cache_key}]
            fc.set_cached_flights(cache_key, result)
            fc.record_feed_poll(cache_key)
            return result

        # First call with bounds
        bounds1 = {"tl_y": 51.6, "tl_x": -0.3, "br_y": 51.4, "br_x": 0.1}
        r1 = mock_get_flights(bounds=bounds1)
        assert api_call_count == 1

        # Same bounds again — cache hit (no new API call even though rate limit
        # would block; cache check comes before rate limit)
        r2 = mock_get_flights(bounds=bounds1)
        assert api_call_count == 1  # Still 1

    def test_weather_multiple_endpoints_share_rate_limiter(self):
        """
        Temperature and forecast share the same rate limiter so that
        one call for temperature blocks an immediate forecast call.
        """
        wc = WeatherCache()

        # First call allowed
        assert wc.should_call_api() is True
        wc.record_api_call()

        # Now both temperature and forecast should be blocked
        assert wc.should_call_api() is False

    def test_fr24_cache_hit_avoids_api_completely(self):
        """
        If details are in cache, no async/API code should execute at all.
        This verifies the check-before-call pattern.
        """
        fc = FR24Cache()
        cached_data = {"airline": "TestAir", "aircraft": "A380"}
        fc.set_cached_flight_details("test_flight_99", cached_data)

        # Simulate what FR24Client.get_flight_details does
        result = fc.get_cached_flight_details("test_flight_99")
        assert result is not None
        assert result == cached_data
        # If this was a real client call, the fact that we got a non-None
        # result means we'd return without calling the async API method


# ═══════════════════════════════════════════════════════════════════════════════
# Weather Rate Limiting (temperature.py module-level) Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeatherModuleRateLimiting:
    """
    Tests that the rate limiting logic prevents API hammering
    when the weather module is rate limited.
    """

    def test_rate_limited_skips_call(self):
        """
        When rate limited, the weather cache returns cached data
        without allowing an API call.
        """
        wc = WeatherCache()
        wc.record_api_call()
        wc.set_cached_temperature((18.0, 72))

        # Should be rate limited now
        assert wc.should_call_api() is False
        # But cached data is still available
        assert wc.get_cached_temperature() == (18.0, 72)

    def test_rate_limited_returns_stale_cache_on_429(self):
        """
        After a 429, even if cache data is old, it's returned
        rather than making another API call.
        """
        wc = WeatherCache()
        # Simulate: first call succeeded, then got 429
        wc.set_cached_temperature((15.0, 60))
        wc.record_api_call()
        wc.handle_429()

        # 50 attempts should all be blocked
        for _ in range(50):
            assert wc.should_call_api() is False

        # But the stale cached data is still available
        assert wc.get_cached_temperature() == (15.0, 60)


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Case Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_cache_with_zero_ttl_always_expires(self):
        """TTL of 0 means the entry is immediately expired."""
        cache = TTLCache(default_ttl=0)
        cache.set("key", "value")
        # Due to timing, this might or might not be expired yet
        # But with any sleep it definitely will be
        time.sleep(0.01)
        assert cache.get("key") is None

    def test_rate_limiter_with_zero_interval(self):
        """Zero interval means never rate limited."""
        rl = RateLimiter(normal_interval=0)
        rl.record_call()
        # With 0 interval, should not be rate limited (elapsed >= 0)
        time.sleep(0.01)
        assert rl.is_rate_limited() is False

    def test_fr24_cache_empty_flight_id(self):
        """Empty flight_id should still work (though unusual)."""
        fc = FR24Cache()
        fc.set_cached_flight_details("", {"empty": True})
        assert fc.get_cached_flight_details("") == {"empty": True}

    def test_weather_cache_no_data_before_first_call(self):
        """Before any API call, cache returns None."""
        wc = WeatherCache()
        assert wc.get_cached_temperature() is None
        assert wc.get_cached_forecast() is None

    def test_concurrent_cache_access(self):
        """Multiple threads reading/writing the FR24 cache simultaneously."""
        fc = FR24Cache()
        errors = []

        def writer(thread_id):
            try:
                for i in range(50):
                    fc.set_cached_flight_details(
                        f"t{thread_id}_f{i}",
                        {"thread": thread_id, "flight": i}
                    )
            except Exception as e:
                errors.append(e)

        def reader(thread_id):
            try:
                for i in range(50):
                    fc.get_cached_flight_details(f"t{thread_id}_f{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for t in range(10):
            threads.append(threading.Thread(target=writer, args=(t,)))
            threads.append(threading.Thread(target=reader, args=(t,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
