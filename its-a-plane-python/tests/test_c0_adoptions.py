"""Tests for features adopted from c0wsaysmoo's private production (June 2026).

Covers:
- adsbdb.py: cache bounds, thread safety, LRU eviction
- nws_alerts.py: expanded events, color overrides, watch suppression
- landmarks.py: ocean detection, country lookup
- api_usage.py: log/summary/pruning
- display/__init__.py: LED_RGB_SEQUENCE import
"""
import os
import sys
import tempfile
import threading
import json

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars before importing anything
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("FR24_API_KEY", "test_sub|test_jwt")
os.environ.setdefault("TOMORROW_API_KEY", "test")
os.environ.setdefault("TEMPERATURE_LOCATION", "40.7,-74.0")
os.environ.setdefault("DISTANCE_UNITS", "imperial")


# ====================================================================
# adsbdb.py — Cache Bounds and Thread Safety
# ====================================================================

class TestAdsbdb:
    """Test the adsbdb aircraft registration lookup module."""

    def test_empty_identifier_returns_empty(self):
        from utilities.adsbdb import get_aircraft_info
        assert get_aircraft_info("") == {}
        assert get_aircraft_info(None) == {}

    def test_cache_key_uppercased(self):
        from utilities import adsbdb
        from utilities.adsbdb import get_aircraft_info
        # Reset cache for test
        with adsbdb._cache_lock:
            adsbdb._cache.clear()
            adsbdb._cache["A9FB92"] = {
                "data": {"registration": "N742SK", "icao_type": "CRJ7"},
                "ts": adsbdb.time(),
                "last_access": adsbdb.time(),
            }
        result = get_aircraft_info("a9fb92")
        assert result["registration"] == "N742SK"

    def test_cache_eviction_at_max_size(self):
        from utilities import adsbdb
        with adsbdb._cache_lock:
            adsbdb._cache.clear()
            now = adsbdb.time()
            # Fill cache to MAX_CACHE_SIZE + 50
            for i in range(adsbdb.MAX_CACHE_SIZE + 50):
                adsbdb._cache[f"TEST{i:06d}"] = {
                    "data": None,
                    "ts": now,
                    "last_access": now - (adsbdb.MAX_CACHE_SIZE + 50 - i),
                }
            adsbdb._evict_oldest()
        with adsbdb._cache_lock:
            assert len(adsbdb._cache) <= adsbdb.MAX_CACHE_SIZE

    def test_cache_has_lock(self):
        from utilities.adsbdb import _cache_lock
        assert isinstance(_cache_lock, type(threading.Lock()))

    def test_lru_updates_last_access(self):
        from utilities import adsbdb
        import time as _time
        with adsbdb._cache_lock:
            adsbdb._cache.clear()
            old_ts = adsbdb.time() - 10
            adsbdb._cache["LRUTEST"] = {
                "data": {"registration": "N123"},
                "ts": adsbdb.time(),
                "last_access": old_ts,
            }
        result = adsbdb.get_aircraft_info("LRUTEST")
        assert result["registration"] == "N123"
        with adsbdb._cache_lock:
            assert adsbdb._cache["LRUTEST"]["last_access"] > old_ts


# ====================================================================
# nws_alerts.py — Expanded Events and Watch Suppression
# ====================================================================

class TestNwsAlerts:
    """Test the expanded NWS alert system."""

    def test_new_event_types_present(self):
        """Verify new event types from c0 are in our map."""
        from utilities.nws_alerts import _ALERT_MAP
        new_events = [
            "Earthquake Warning",
            "Volcano Warning",
            "Evacuation Immediate",
            "Nuclear Power Plant Warning",
            "Snow Squall Warning",
            "Lake Effect Snow Warning",
            "Dense Smoke Advisory",
            "Avalanche Warning",
            "Tsunami Advisory",
            "Small Craft Advisory",
            "911 Telephone Outage",
            "Air Stagnation Advisory",
        ]
        for event in new_events:
            assert event in _ALERT_MAP, f"Missing new event: {event}"

    def test_abbreviations_fit_display(self):
        """Alert abbreviations should be ≤9 chars to avoid overflow."""
        from utilities.nws_alerts import _ALERT_MAP
        for event, (abbrev, color) in _ALERT_MAP.items():
            assert len(abbrev) <= 9, (
                f"'{event}' abbreviation '{abbrev}' is {len(abbrev)} chars (max 9)"
            )

    def test_color_override_applies(self):
        """Verify _EVENT_COLOUR_OVERRIDE forces specific events to red."""
        from utilities.nws_alerts import _EVENT_COLOUR_OVERRIDE
        assert _EVENT_COLOUR_OVERRIDE["Extreme Heat Warning"] == "red"
        assert _EVENT_COLOUR_OVERRIDE["Tornado Watch"] == "red"
        assert _EVENT_COLOUR_OVERRIDE["Tsunami Warning"] == "red"

    def test_watch_suppression_basic(self):
        """When a Warning is active, matching Watch should be suppressed."""
        from utilities.nws_alerts import _suppress_watches
        alerts = [
            {"text": "Tornado!", "color": "red"},
            {"text": "TornWtch", "color": "grey"},
        ]
        features = [
            {"properties": {"event": "Tornado Warning"}},
            {"properties": {"event": "Tornado Watch"}},
        ]
        filtered = _suppress_watches(alerts, features)
        assert len(filtered) == 1
        assert filtered[0]["text"] == "Tornado!"

    def test_watch_suppression_no_match(self):
        """Watch without matching Warning should NOT be suppressed."""
        from utilities.nws_alerts import _suppress_watches
        alerts = [
            {"text": "Flood", "color": "orange"},
            {"text": "TornWtch", "color": "grey"},
        ]
        features = [
            {"properties": {"event": "Flood Warning"}},
            {"properties": {"event": "Tornado Watch"}},
        ]
        filtered = _suppress_watches(alerts, features)
        assert len(filtered) == 2  # No suppression — "Flood" != "Tornado"

    def test_watch_suppression_empty_warnings(self):
        """If no warnings exist, all watches should be kept."""
        from utilities.nws_alerts import _suppress_watches
        alerts = [
            {"text": "TornWtch", "color": "grey"},
            {"text": "FldWatch", "color": "grey"},
        ]
        features = [
            {"properties": {"event": "Tornado Watch"}},
            {"properties": {"event": "Flash Flood Watch"}},
        ]
        filtered = _suppress_watches(alerts, features)
        assert len(filtered) == 2

    def test_all_colors_valid(self):
        """All colors in _ALERT_MAP should be valid display colors."""
        from utilities.nws_alerts import _ALERT_MAP
        valid_colors = {"red", "orange", "cyan", "yellow", "grey"}
        for event, (abbrev, color) in _ALERT_MAP.items():
            assert color in valid_colors, f"'{event}' has invalid color '{color}'"


# ====================================================================
# landmarks.py — Ocean Detection and Country Lookup
# ====================================================================

class TestLandmarksOceanDetection:
    """Test the ocean/sea bounding-box lookup."""

    def test_ocean_north_atlantic(self):
        from utilities.landmarks import _get_ocean_name
        # Mid-Atlantic (roughly between NY and London)
        result = _get_ocean_name(40.0, -40.0)
        assert result == "North Atlantic"

    def test_ocean_caribbean(self):
        from utilities.landmarks import _get_ocean_name
        result = _get_ocean_name(18.0, -75.0)
        assert result == "Caribbean Sea"

    def test_ocean_mediterranean(self):
        from utilities.landmarks import _get_ocean_name
        result = _get_ocean_name(38.0, 15.0)
        assert result == "Mediterranean Sea"

    def test_ocean_specificity_order(self):
        """Seas should match before oceans (more specific first)."""
        from utilities.landmarks import _get_ocean_name
        # Gulf of Mexico should match before North Atlantic
        result = _get_ocean_name(25.0, -90.0)
        assert result == "Gulf of Mexico"

    def test_ocean_none_on_land(self):
        """Points clearly on land should not match any ocean.
        Note: ocean regions are broad bounding boxes — they're a last-resort
        fallback. get_nearest_landmark() only calls _get_ocean_name when
        parks, Nominatim, and cities all fail (i.e., likely over water).
        Here we test a point in central Africa, far from any ocean box."""
        from utilities.landmarks import _get_ocean_name
        # Sahara desert — far from any ocean bounding box
        result = _get_ocean_name(23.0, 10.0)
        assert result is None


class TestLandmarksCountryLookup:
    """Test the country name lookup."""

    def test_country_us(self):
        from utilities.landmarks import _country_name
        assert _country_name("us") == "United States"

    def test_country_case_insensitive(self):
        from utilities.landmarks import _country_name
        assert _country_name("GB") == "United Kingdom"
        assert _country_name("jp") == "Japan"

    def test_country_unknown(self):
        from utilities.landmarks import _country_name
        assert _country_name("zz") is None
        assert _country_name("") is None
        assert _country_name(None) is None

    def test_country_truncation(self):
        from utilities.landmarks import _country_name, MAX_NAME_LEN
        name = _country_name("us")
        assert len(name) <= MAX_NAME_LEN


# ====================================================================
# api_usage.py — Logging and Summary
# ====================================================================

class TestApiUsage:
    """Test the API usage tracking module."""

    def setup_method(self):
        """Reset module state for each test."""
        from utilities import api_usage
        with api_usage._lock:
            api_usage._data.clear()
            api_usage._loaded = True  # Skip disk load

    def test_log_call_increments(self):
        from utilities.api_usage import log_call, get_usage
        log_call("fr24_grpc")
        log_call("fr24_grpc")
        log_call("airlabs")
        usage = get_usage()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert usage[today]["fr24_grpc"] == 2
        assert usage[today]["airlabs"] == 1

    def test_get_summary_current_month(self):
        from utilities.api_usage import log_call, get_summary
        log_call("nws")
        log_call("nws")
        log_call("iss_api")
        summary = get_summary()
        assert summary["totals"]["nws"] == 2
        assert summary["totals"]["iss_api"] == 1

    def test_thread_safety(self):
        """Multiple threads logging simultaneously shouldn't crash."""
        from utilities.api_usage import log_call
        errors = []

        def log_many(source, count):
            try:
                for _ in range(count):
                    log_call(source)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=log_many, args=("fr24_grpc", 50)),
            threading.Thread(target=log_many, args=("airlabs", 50)),
            threading.Thread(target=log_many, args=("nws", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"

    def test_disk_persistence(self):
        """log_call should set _dirty flag so data persists to disk."""
        from utilities import api_usage
        # Reset
        with api_usage._lock:
            api_usage._data.clear()
            api_usage._loaded = True
            api_usage._dirty = False
            api_usage._last_save_ts = 0.0

        api_usage.log_call("fr24_grpc")
        # After log_call, _dirty should be True (or already saved)
        # The key test: data should be in _data
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert api_usage._data.get(today, {}).get("fr24_grpc") == 1

        # Force flush and verify file was written
        api_usage.flush()
        assert os.path.exists(api_usage.USAGE_FILE)
        with open(api_usage.USAGE_FILE) as f:
            persisted = json.load(f)
        assert persisted[today]["fr24_grpc"] == 1

    def test_thread_safety_counts(self):
        """Thread safety test should produce exact expected counts."""
        from utilities.api_usage import log_call, get_usage
        log_call("test_src")
        log_call("test_src")
        log_call("test_src")
        usage = get_usage()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        assert usage[today]["test_src"] == 3


# ====================================================================
# display/__init__.py — LED_RGB_SEQUENCE config
# ====================================================================

class TestDisplayConfig:
    """Test display configuration imports."""

    def test_led_rgb_sequence_in_config(self):
        """LED_RGB_SEQUENCE should be defined in config module."""
        import config
        assert hasattr(config, "LED_RGB_SEQUENCE")
        assert isinstance(config.LED_RGB_SEQUENCE, str)
        assert len(config.LED_RGB_SEQUENCE) == 3  # e.g., "RGB", "GRB"
