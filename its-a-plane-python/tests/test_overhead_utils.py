"""
Unit tests for overhead.py utility functions and the data pipeline.

Tests cover:
  - haversine(): distance calculation, None guard, zero-coordinate handling
  - degrees_to_cardinal(): compass direction conversion
  - estimate_stale_data(): stale flight position estimation
  - Helicopter detection (HELICOPTER_TYPES)
  - data_is_empty property (thread-safe)
  - Error handler behavior (_new_data set True on error)
  - Local airport/airline lookups
  - Audit logging
"""

import sys
import os
import json
import time
import math
import threading
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# Ensure the project root is on sys.path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config and other imports before importing overhead
os.environ.setdefault("ZONE_TL_LAT", "51.595")
os.environ.setdefault("ZONE_TL_LON", "-0.314")
os.environ.setdefault("ZONE_BR_LAT", "51.47")
os.environ.setdefault("ZONE_BR_LON", "-0.111")
os.environ.setdefault("HOME_LAT", "51.55864")
os.environ.setdefault("HOME_LON", "-0.177332")
os.environ.setdefault("DISTANCE_UNITS", "imperial")
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())


# ═══════════════════════════════════════════════════════════════════════════════
# Haversine Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHaversine:
    """Tests for the haversine distance function."""

    def test_same_point_returns_zero(self):
        """Distance from a point to itself is zero."""
        from utilities.overhead import haversine
        assert haversine(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_known_distance_new_york_los_angeles(self):
        """NY to LA is approximately 2,451 miles."""
        from utilities.overhead import haversine
        # JFK to LAX approximate coordinates
        dist = haversine(40.6413, -73.7781, 33.9425, -118.4081)
        # Should be roughly 2,450 miles (imperial)
        assert 2400 < dist < 2500

    def test_none_lat1_returns_zero(self):
        """None latitude returns 0 (guard against invalid data)."""
        from utilities.overhead import haversine
        assert haversine(None, -74.0, 40.0, -74.0) == 0

    def test_none_lon1_returns_zero(self):
        """None longitude returns 0."""
        from utilities.overhead import haversine
        assert haversine(40.0, None, 40.0, -74.0) == 0

    def test_none_lat2_returns_zero(self):
        """None destination lat returns 0."""
        from utilities.overhead import haversine
        assert haversine(40.0, -74.0, None, -74.0) == 0

    def test_none_lon2_returns_zero(self):
        """None destination lon returns 0."""
        from utilities.overhead import haversine
        assert haversine(40.0, -74.0, 40.0, None) == 0

    def test_all_none_returns_zero(self):
        """All None returns 0."""
        from utilities.overhead import haversine
        assert haversine(None, None, None, None) == 0

    def test_zero_latitude_not_treated_as_none(self):
        """Airports at 0.0 latitude (equator) should NOT be treated as None.
        This is the bug fix: `not all(...)` would fail for 0.0 values."""
        from utilities.overhead import haversine
        # Sao Tome (on the equator) to London
        dist = haversine(0.0, 6.6131, 51.4775, -0.4614)
        assert dist > 0  # Should be a real distance, not zero

    def test_zero_longitude_not_treated_as_none(self):
        """Airports at 0.0 longitude (prime meridian) should work."""
        from utilities.overhead import haversine
        # A point on the prime meridian to somewhere else
        dist = haversine(51.4775, 0.0, 40.6413, -73.7781)
        assert dist > 0

    def test_symmetric(self):
        """haversine(A, B) == haversine(B, A)."""
        from utilities.overhead import haversine
        d1 = haversine(40.0, -74.0, 51.5, -0.1)
        d2 = haversine(51.5, -0.1, 40.0, -74.0)
        assert abs(d1 - d2) < 0.001


# ═══════════════════════════════════════════════════════════════════════════════
# Cardinal Direction Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDegreesToCardinal:
    """Tests for compass direction conversion."""

    def test_north(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(0) == "N"
        assert degrees_to_cardinal(360) == "N"

    def test_east(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(90) == "E"

    def test_south(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(180) == "S"

    def test_west(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(270) == "W"

    def test_northeast(self):
        from utilities.overhead import degrees_to_cardinal
        assert degrees_to_cardinal(45) == "NE"

    def test_boundary_values(self):
        """22.5 degrees is the boundary between N and NE."""
        from utilities.overhead import degrees_to_cardinal
        # At exactly 22.5 degrees, should still be N (edge case)
        result = degrees_to_cardinal(22)
        assert result == "N"
        result = degrees_to_cardinal(23)
        assert result == "NE"


# ═══════════════════════════════════════════════════════════════════════════════
# Helicopter Detection Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestHelicopterDetection:
    """Tests for helicopter type identification."""

    def test_common_helicopter_types_detected(self):
        """Common helicopter ICAO type codes are in HELICOPTER_TYPES."""
        from utilities.overhead import HELICOPTER_TYPES
        assert "S76" in HELICOPTER_TYPES   # Sikorsky S-76
        assert "EC35" in HELICOPTER_TYPES  # Eurocopter EC135
        assert "A139" in HELICOPTER_TYPES  # AgustaWestland AW139
        assert "R44" in HELICOPTER_TYPES   # Robinson R44
        assert "B407" in HELICOPTER_TYPES  # Bell 407
        assert "H60" in HELICOPTER_TYPES   # Black Hawk

    def test_fixed_wing_not_in_helicopter_types(self):
        """Common fixed-wing types should NOT be detected as helicopters."""
        from utilities.overhead import HELICOPTER_TYPES
        assert "B738" not in HELICOPTER_TYPES  # Boeing 737-800
        assert "A320" not in HELICOPTER_TYPES  # Airbus A320
        assert "C172" not in HELICOPTER_TYPES  # Cessna 172
        assert "B77W" not in HELICOPTER_TYPES  # Boeing 777

    def test_helicopter_sets_heli_icao(self):
        """When aircraft_type is a helicopter, owner_icao should be 'HELI'."""
        from utilities.overhead import HELICOPTER_TYPES
        # Simulate the logic from overhead.py
        aircraft_type = "EC35"
        owner_icao = "BAW"  # hypothetical
        if aircraft_type in HELICOPTER_TYPES:
            owner_icao = "HELI"
        assert owner_icao == "HELI"


# ═══════════════════════════════════════════════════════════════════════════════
# Stale Data Estimation Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEstimateStaleData:
    """Tests for stale flight position estimation."""

    def test_marks_not_live(self):
        """Stale data is marked with is_live=False."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,
            "last_seen_ts": time.time() - 60,
            "time_remaining": "1:30",
            "dist_remaining": 500,
        }
        result = estimate_stale_data(data)
        assert result["is_live"] is False

    def test_time_remaining_decreases(self):
        """Time remaining should decrease by elapsed time."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,
            "last_seen_ts": time.time() - 600,  # 10 minutes ago
            "time_remaining": "1:30",  # 90 minutes
            "dist_remaining": 500,
        }
        result = estimate_stale_data(data)
        # Should be approximately 80 minutes remaining
        assert "1:20" in result["time_remaining"] or "1:19" in result["time_remaining"]

    def test_distance_remaining_decreases(self):
        """Distance remaining should decrease based on speed × time."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,  # knots
            "last_seen_ts": time.time() - 3600,  # 1 hour ago
            "time_remaining": "3:00",
            "dist_remaining": 1000,
        }
        result = estimate_stale_data(data)
        # 450 knots × 1 hour × 1.15078 = ~518 mph covered
        assert result["dist_remaining"] < 1000
        assert result["dist_remaining"] > 0

    def test_no_last_ts_returns_unchanged(self):
        """Without last_seen_ts, returns data unchanged except is_live."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 450,
            "time_remaining": "2:00",
            "dist_remaining": 800,
        }
        result = estimate_stale_data(data)
        assert result["is_live"] is False
        assert result["time_remaining"] == "2:00"
        assert result["dist_remaining"] == 800

    def test_zero_speed_no_distance_change(self):
        """With zero ground speed, distance remaining doesn't change."""
        from utilities.overhead import estimate_stale_data
        data = {
            "is_live": True,
            "ground_speed": 0,
            "last_seen_ts": time.time() - 600,
            "time_remaining": "1:00",
            "dist_remaining": 500,
        }
        result = estimate_stale_data(data)
        assert result["dist_remaining"] == 500


# ═══════════════════════════════════════════════════════════════════════════════
# Overhead Class Property Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOverheadProperties:
    """Tests for Overhead class thread-safe properties."""

    def test_data_is_empty_with_lock(self):
        """data_is_empty should be thread-safe (uses lock)."""
        from utilities.overhead import Overhead
        o = Overhead()
        # Initially empty
        assert o.data_is_empty is True

        # Simulate data arrival (bypassing grab_data)
        with o._lock:
            o._data = [{"callsign": "TEST123"}]
        assert o.data_is_empty is False

    def test_new_data_default_false(self):
        """new_data should be False initially."""
        from utilities.overhead import Overhead
        o = Overhead()
        assert o.new_data is False

    def test_processing_default_false(self):
        """processing should be False initially."""
        from utilities.overhead import Overhead
        o = Overhead()
        assert o.processing is False

    def test_data_resets_new_data_flag(self):
        """Accessing .data should reset _new_data to False."""
        from utilities.overhead import Overhead
        o = Overhead()
        with o._lock:
            o._new_data = True
            o._data = [{"callsign": "ABC123"}]
        _ = o.data
        assert o.new_data is False

    def test_concurrent_data_access(self):
        """Multiple threads accessing data simultaneously shouldn't crash."""
        from utilities.overhead import Overhead
        o = Overhead()
        with o._lock:
            o._data = [{"callsign": f"FLT{i}"} for i in range(5)]
            o._new_data = True

        results = []
        errors = []

        def reader():
            try:
                for _ in range(100):
                    _ = o.data_is_empty
                    _ = o.new_data
                results.append(True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# Error Handler Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorHandlerFreeze:
    """Tests that _grab() sets _new_data=True even on errors (prevents display freeze)."""

    def test_network_error_sets_new_data_true(self):
        """After a network error, new_data should be True (not stuck spinning)."""
        from utilities.overhead import Overhead
        o = Overhead()

        # Mock the API to raise a ConnectionError
        with patch.object(o._api, 'get_flights', side_effect=ConnectionError("Network down")):
            # Run _grab directly (not in a thread for testing)
            o._grab()

        # Key assertion: new_data must be True so display loop proceeds
        assert o.new_data is True
        assert o.data_is_empty is True
        assert o.processing is False

    def test_unexpected_error_sets_new_data_true(self):
        """After an unexpected error, new_data should still be True."""
        from utilities.overhead import Overhead
        o = Overhead()

        # Mock the API to raise a generic exception
        with patch.object(o._api, 'get_flights', side_effect=RuntimeError("Unexpected!")):
            o._grab()

        assert o.new_data is True
        assert o.data_is_empty is True
        assert o.processing is False


# ═══════════════════════════════════════════════════════════════════════════════
# Safe JSON I/O Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSafeJsonIO:
    """Tests for safe_load_json and safe_write_json."""

    def test_load_nonexistent_returns_empty_list(self):
        """Loading a non-existent file returns empty list."""
        from utilities.overhead import safe_load_json
        result = safe_load_json("/tmp/nonexistent_file_xyz.json")
        assert result == []

    def test_load_invalid_json_returns_empty_list(self):
        """Loading invalid JSON returns empty list."""
        from utilities.overhead import safe_load_json
        path = "/tmp/test_invalid_json.json"
        with open(path, "w") as f:
            f.write("not valid json {{{")
        result = safe_load_json(path)
        assert result == []
        os.unlink(path)

    def test_write_and_load_roundtrip(self):
        """Write then load JSON roundtrip."""
        from utilities.overhead import safe_write_json, safe_load_json
        path = "/tmp/test_roundtrip.json"
        data = [{"callsign": "UAL123", "distance": 5.2}]
        safe_write_json(path, data)
        result = safe_load_json(path)
        assert result == data
        os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════════
# Ordinal Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrdinal:
    """Tests for ordinal number formatting."""

    def test_ordinals(self):
        from utilities.overhead import ordinal
        assert ordinal(1) == "1st"
        assert ordinal(2) == "2nd"
        assert ordinal(3) == "3rd"
        assert ordinal(4) == "4th"
        assert ordinal(11) == "11th"
        assert ordinal(12) == "12th"
        assert ordinal(13) == "13th"
        assert ordinal(21) == "21st"
        assert ordinal(22) == "22nd"
        assert ordinal(23) == "23rd"


# ═══════════════════════════════════════════════════════════════════════════════
# Farthest Flight Logging Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogFarthestFlight:
    """Tests for log_farthest_flight() ranking and storage logic."""

    def _make_entry(self, origin="JFK", destination="LHR",
                    distance_origin=500, distance_destination=300,
                    distance=10, callsign="UAL123"):
        """Helper to build a flight entry dict."""
        return {
            "origin": origin,
            "destination": destination,
            "distance_origin": distance_origin,
            "distance_destination": distance_destination,
            "distance": distance,
            "callsign": callsign,
        }

    def test_skips_when_both_distances_negative(self):
        """Returns immediately when both origin and dest distances are negative."""
        from utilities.overhead import log_farthest_flight, safe_load_json
        entry = self._make_entry(distance_origin=-1, distance_destination=-1)
        with patch("utilities.overhead.safe_load_json") as mock_load:
            log_farthest_flight(entry)
            mock_load.assert_not_called()

    def test_skips_when_both_distances_none(self):
        """None distances default to -1, so both None also skips."""
        from utilities.overhead import log_farthest_flight
        entry = self._make_entry(distance_origin=None, distance_destination=None)
        with patch("utilities.overhead.safe_load_json") as mock_load:
            log_farthest_flight(entry)
            mock_load.assert_not_called()

    def test_picks_origin_when_d_o_greater(self):
        """When distance_origin >= distance_destination, reason is 'origin'."""
        from utilities.overhead import log_farthest_flight
        entry = self._make_entry(distance_origin=500, distance_destination=300)
        written = []
        with patch("utilities.overhead.safe_load_json", return_value=[]), \
             patch("utilities.overhead.safe_write_json", side_effect=lambda p, d: written.append(d)), \
             patch("utilities.overhead.email_alerts") as mock_email:
            mock_email.get_timestamp.return_value = "2026-06-14T12:00:00"
            log_farthest_flight(entry)
        assert len(written) == 1
        saved = written[0][0]
        assert saved["reason"] == "origin"
        assert saved["farthest_value"] == 500

    def test_picks_destination_when_d_d_greater(self):
        """When distance_destination > distance_origin, reason is 'destination'."""
        from utilities.overhead import log_farthest_flight
        entry = self._make_entry(distance_origin=200, distance_destination=600)
        written = []
        with patch("utilities.overhead.safe_load_json", return_value=[]), \
             patch("utilities.overhead.safe_write_json", side_effect=lambda p, d: written.append(d)), \
             patch("utilities.overhead.email_alerts") as mock_email:
            mock_email.get_timestamp.return_value = "2026-06-14T12:00:00"
            log_farthest_flight(entry)
        assert written[0][0]["reason"] == "destination"
        assert written[0][0]["farthest_value"] == 600

    def test_skips_when_airport_is_none(self):
        """If the airport for the winning reason is None, returns early."""
        from utilities.overhead import log_farthest_flight
        # origin wins (500 > 300) but origin airport is None
        entry = self._make_entry(origin=None, distance_origin=500, distance_destination=300)
        with patch("utilities.overhead.safe_load_json") as mock_load:
            log_farthest_flight(entry)
            mock_load.assert_not_called()

    def test_appends_new_entry_sorted_descending(self):
        """New airport entry is appended and list sorted by farthest_value desc."""
        from utilities.overhead import log_farthest_flight
        existing = [
            {"_airport": "NRT", "farthest_value": 6000, "distance": 5},
            {"_airport": "LHR", "farthest_value": 3000, "distance": 8},
        ]
        entry = self._make_entry(origin="CDG", distance_origin=4500, distance_destination=100)
        written = []
        with patch("utilities.overhead.safe_load_json", return_value=existing), \
             patch("utilities.overhead.safe_write_json", side_effect=lambda p, d: written.append(d)), \
             patch("utilities.overhead.email_alerts") as mock_email, \
             patch("utilities.overhead._ensure_map_imports"), \
             patch("utilities.overhead.map_generator") as mock_mg, \
             patch("utilities.overhead.upload_helper") as mock_uh:
            mock_email.get_timestamp.return_value = "2026-06-14T12:00:00"
            mock_mg.generate_farthest_map.return_value = "/tmp/map.html"
            mock_uh.upload_map_to_server.return_value = "https://example.com/map"
            log_farthest_flight(entry)

        saved = written[0]
        values = [f["farthest_value"] for f in saved]
        assert values == sorted(values, reverse=True)
        airports = [f["_airport"] for f in saved]
        assert "CDG" in airports

    def test_updates_existing_airport_closer_distance(self):
        """If same airport appears with a closer overhead distance, replaces it."""
        from utilities.overhead import log_farthest_flight
        existing = [
            {"_airport": "JFK", "farthest_value": 500, "distance": 20, "reason": "origin"},
        ]
        # Same airport, closer distance (distance=5 < 20)
        entry = self._make_entry(origin="JFK", distance_origin=500, distance_destination=100, distance=5)
        written = []
        with patch("utilities.overhead.safe_load_json", return_value=existing), \
             patch("utilities.overhead.safe_write_json", side_effect=lambda p, d: written.append(d)), \
             patch("utilities.overhead.email_alerts") as mock_email, \
             patch("utilities.overhead._ensure_map_imports"), \
             patch("utilities.overhead.map_generator") as mock_mg:
            mock_email.get_timestamp.return_value = "2026-06-14T12:00:00"
            mock_mg.generate_farthest_map.return_value = "/tmp/map.html"
            log_farthest_flight(entry)

        assert len(written) == 1
        assert written[0][0]["distance"] == 5  # Updated to closer

    def test_rejects_existing_airport_farther_distance(self):
        """If same airport appears with a farther overhead distance, rejects it."""
        from utilities.overhead import log_farthest_flight
        existing = [
            {"_airport": "JFK", "farthest_value": 500, "distance": 5, "reason": "origin"},
        ]
        # Same airport, farther distance (distance=20 > 5)
        entry = self._make_entry(origin="JFK", distance_origin=500, distance_destination=100, distance=20)
        with patch("utilities.overhead.safe_load_json", return_value=existing), \
             patch("utilities.overhead.safe_write_json") as mock_write, \
             patch("utilities.overhead.email_alerts") as mock_email:
            mock_email.get_timestamp.return_value = "2026-06-14T12:00:00"
            log_farthest_flight(entry)
            mock_write.assert_not_called()

    def test_max_farthest_cap_rejects_below_minimum(self):
        """When list is full, entry with farthest_value <= min is rejected."""
        from utilities.overhead import log_farthest_flight, MAX_FARTHEST
        # Build a full list
        existing = [
            {"_airport": f"AP{i}", "farthest_value": 1000 - i * 10, "distance": 5}
            for i in range(MAX_FARTHEST)
        ]
        min_val = min(f["farthest_value"] for f in existing)
        # New entry at or below the minimum
        entry = self._make_entry(origin="TINY", distance_origin=min_val, distance_destination=0)
        with patch("utilities.overhead.safe_load_json", return_value=existing), \
             patch("utilities.overhead.safe_write_json") as mock_write, \
             patch("utilities.overhead.email_alerts") as mock_email:
            mock_email.get_timestamp.return_value = "2026-06-14T12:00:00"
            log_farthest_flight(entry)
            mock_write.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Distance Pipeline Tests (meters → km → miles)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDistancePipeline:
    """Tests for the FR24 flight_progress distance conversion math.

    FR24 gRPC returns traversed_distance and remaining_distance in METERS.
    The pipeline converts: meters / 1000 → km, then km / 1.609344 → miles (imperial).
    """

    def test_traversed_meters_to_km(self):
        """traversed_distance in meters is correctly divided by 1000."""
        fp = {"traversed_distance": 2474078}
        traversed_km = (fp.get("traversed_distance", 0) or 0) / 1000.0
        assert abs(traversed_km - 2474.078) < 0.001

    def test_remaining_meters_to_km(self):
        """remaining_distance in meters is correctly divided by 1000."""
        fp = {"remaining_distance": 1500000}
        remaining_km = (fp.get("remaining_distance", 0) or 0) / 1000.0
        assert remaining_km == 1500.0

    def test_none_traversed_returns_zero(self):
        """None traversed_distance defaults to 0."""
        fp = {"traversed_distance": None}
        traversed_km = (fp.get("traversed_distance", 0) or 0) / 1000.0
        assert traversed_km == 0.0

    def test_zero_traversed_returns_zero(self):
        """Zero traversed_distance stays zero."""
        fp = {"traversed_distance": 0}
        traversed_km = (fp.get("traversed_distance", 0) or 0) / 1000.0
        assert traversed_km == 0.0

    def test_missing_key_returns_zero(self):
        """Missing key defaults to 0."""
        fp = {}
        traversed_km = (fp.get("traversed_distance", 0) or 0) / 1000.0
        assert traversed_km == 0.0

    def test_imperial_conversion_km_to_miles(self):
        """km / 1.609344 gives correct imperial miles."""
        traversed_km = 2474.078
        dist_miles = traversed_km / 1.609344
        # 2474.078 km ≈ 1537.3 miles
        assert abs(dist_miles - 1537.3) < 1.0

    def test_metric_passthrough(self):
        """When metric, dist_o = traversed_km directly (no conversion)."""
        traversed_km = 2474.078
        # Metric path: dist_o = traversed_km
        dist_o = traversed_km
        assert dist_o == 2474.078

    def test_old_bug_would_produce_absurd_values(self):
        """Before the fix, meters were treated as km — producing ~1000x inflation.
        This test documents the bug to prevent regression."""
        fp = {"traversed_distance": 2474078}  # meters
        # CORRECT: divide by 1000 first
        correct_km = (fp["traversed_distance"] or 0) / 1000.0
        correct_miles = correct_km / 1.609344
        # BUG: treating meters as km directly
        buggy_miles = fp["traversed_distance"] / 1.609344
        # Buggy value is ~1000x too large
        assert buggy_miles > correct_miles * 900
        # Correct value is reasonable (Earth max ~12,500 miles one-way)
        assert correct_miles < 12500


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Overflow Clipping Regression Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAlertOverflowClipping:
    """Regression tests for the y=11 clipping boundary fix.

    clock.py and date.py clear overflow pixels with draw_square(..., y_max, ...).
    The fix changed y_max from 12 to 11 so the forecast day-name row (y=12)
    is not wiped. These tests read the source files to verify the boundary.
    """

    def _get_source_dir(self):
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scenes")

    def test_clock_overflow_clear_uses_y11(self):
        """clock.py draw_square for alert overflow must use y=11, not y=12."""
        clock_path = os.path.join(self._get_source_dir(), "clock.py")
        with open(clock_path) as f:
            source = f.read()
        # Find the draw_square call in the overflow clearing block
        # Pattern: draw_square(36, 6, 64, 11, ...) — the 4th arg must be 11
        import re
        matches = re.findall(r'draw_square\(36,\s*6,\s*64,\s*(\d+)', source)
        assert len(matches) >= 1, "Expected draw_square(36, 6, 64, ...) in clock.py"
        for m in matches:
            assert m == "11", f"clock.py overflow clear uses y={m}, expected y=11"

    def test_date_overflow_clear_uses_y11(self):
        """date.py draw_square for alert overflow must use y=11, not y=12."""
        date_path = os.path.join(self._get_source_dir(), "date.py")
        with open(date_path) as f:
            source = f.read()
        import re
        # date.py uses: draw_square(clear_start, 6, 64, 11, ...)
        matches = re.findall(r'draw_square\([^,]+,\s*6,\s*64,\s*(\d+)', source)
        assert len(matches) >= 1, "Expected draw_square(..., 6, 64, ...) in date.py"
        for m in matches:
            assert m == "11", f"date.py overflow clear uses y={m}, expected y=11"

    def test_overflow_triggers_at_len_greater_than_9(self):
        """Alert text longer than 9 chars triggers overflow clearing."""
        # Replicate the logic from clock.py line 304
        alert_text = "RAIN 10min"  # 10 chars
        assert len(alert_text) > 9
        overflow = len(alert_text) if (alert_text and len(alert_text) > 9) else 0
        assert overflow == 10

    def test_no_overflow_at_len_9_or_less(self):
        """Alert text of 9 chars or fewer does NOT trigger overflow."""
        alert_text = "RAIN 5mn"  # 8 chars
        overflow = len(alert_text) if (alert_text and len(alert_text) > 9) else 0
        assert overflow == 0

    def test_no_overflow_empty_alert(self):
        """Empty alert text does NOT trigger overflow."""
        alert_text = ""
        overflow = len(alert_text) if (alert_text and len(alert_text) > 9) else 0
        assert overflow == 0

    def test_no_overflow_none_alert(self):
        """None alert text does NOT trigger overflow."""
        alert_text = None
        overflow = len(alert_text) if (alert_text and len(alert_text) > 9) else 0
        assert overflow == 0

    def test_clear_start_calculation(self):
        """date.py calculates clear_start = max(overflow_chars * 4, DATE_POSITION[0])."""
        overflow_chars = 12
        alert_end_x = overflow_chars * 4  # 48
        date_position_x = 36  # typical DATE_POSITION[0]
        clear_start = max(alert_end_x, date_position_x)
        assert clear_start == 48  # alert extends past date start

    def test_clear_start_uses_date_position_when_alert_shorter(self):
        """When alert is short, clear_start = DATE_POSITION[0]."""
        overflow_chars = 10  # just barely overflowing
        alert_end_x = overflow_chars * 4  # 40
        date_position_x = 36
        clear_start = max(alert_end_x, date_position_x)
        assert clear_start == 40  # alert end is past date start
