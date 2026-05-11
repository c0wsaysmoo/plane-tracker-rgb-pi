"""Tests for FL altitude formatting and nearest city lookup."""

import json
import math
import os
import sys
import tempfile
import pytest
from unittest.mock import patch

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# --- FL altitude tests ---

# Copy of _format_altitude to avoid rgbmatrix import chain
def _format_altitude(altitude):
    """Format altitude as flight level (FL350) or raw feet below 1000ft."""
    if not altitude:
        return None
    altitude = int(altitude)
    if altitude >= 1000:
        fl = altitude // 100
        return f"FL{fl:03d}"
    else:
        return f"{altitude}ft"


class TestFormatAltitude:
    """Test _format_altitude (copy of scenes/trackedstats.py version)."""

    def _format(self, altitude):
        return _format_altitude(altitude)

    def test_cruise_altitude(self):
        assert self._format(35000) == "FL350"

    def test_fl100(self):
        assert self._format(10000) == "FL100"

    def test_fl010(self):
        assert self._format(1000) == "FL010"

    def test_fl001_boundary(self):
        """1100 feet should be FL011."""
        assert self._format(1100) == "FL011"

    def test_below_1000_raw_feet(self):
        """Below 1000ft, show raw feet."""
        assert self._format(500) == "500ft"

    def test_zero(self):
        assert self._format(0) is None

    def test_none(self):
        assert self._format(None) is None

    def test_helicopter_low(self):
        assert self._format(200) == "200ft"

    def test_typical_descent(self):
        assert self._format(28500) == "FL285"


# --- Nearest city tests ---

class TestNearestCity:
    """Test get_nearest_city from cities.py."""

    def _make_test_cache(self, tmpdir):
        """Create a small test cities.json."""
        from utilities.cities import CACHE_VERSION
        cities = [
            ["New York City", 40.7128, -74.0060],
            ["London", 51.5074, -0.1278],
            ["Tokyo", 35.6762, 139.6503],
            ["Paris", 48.8566, 2.3522],
            ["Sydney", -33.8688, 151.2093],
        ]
        cache_data = {"_version": CACHE_VERSION, "cities": cities}
        cache_path = os.path.join(tmpdir, "cities.json")
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        return cache_path, cities

    def test_nearest_to_jfk(self):
        """JFK airport should find New York City as nearest."""
        import utilities.cities as mod
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path, _ = self._make_test_cache(tmpdir)
            old_file = mod.CACHE_FILE
            old_loaded = mod._loaded
            old_db = mod._db
            try:
                mod.CACHE_FILE = cache_path
                mod._loaded = False
                mod._db = []
                result = mod.get_nearest_city(40.6413, -73.7781)
                assert result is not None
                assert result["name"] == "New York City"
                assert result["distance_km"] < 30  # JFK is ~20km from NYC center
            finally:
                mod.CACHE_FILE = old_file
                mod._loaded = old_loaded
                mod._db = old_db

    def test_nearest_to_heathrow(self):
        """Heathrow should find London as nearest."""
        import utilities.cities as mod
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path, _ = self._make_test_cache(tmpdir)
            old_file = mod.CACHE_FILE
            old_loaded = mod._loaded
            old_db = mod._db
            try:
                mod.CACHE_FILE = cache_path
                mod._loaded = False
                mod._db = []
                result = mod.get_nearest_city(51.4700, -0.4543)
                assert result is not None
                assert result["name"] == "London"
                assert result["distance_km"] < 30
            finally:
                mod.CACHE_FILE = old_file
                mod._loaded = old_loaded
                mod._db = old_db

    def test_empty_db(self):
        """Empty database returns None."""
        import utilities.cities as mod
        old_loaded = mod._loaded
        old_db = mod._db
        try:
            mod._loaded = True
            mod._db = []
            assert mod.get_nearest_city(0, 0) is None
        finally:
            mod._loaded = old_loaded
            mod._db = old_db

    def test_haversine_km(self):
        """Verify internal haversine gives reasonable distances."""
        from utilities.cities import _haversine_km
        # NYC to London ~5570 km
        dist = _haversine_km(40.7128, -74.0060, 51.5074, -0.1278)
        assert 5500 < dist < 5700

    def test_haversine_same_point(self):
        from utilities.cities import _haversine_km
        assert _haversine_km(0, 0, 0, 0) == 0.0

    def test_cache_version_mismatch_triggers_rebuild(self):
        """Wrong version in cache should trigger rebuild (but we mock the download)."""
        import utilities.cities as mod
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "cities.json")
            with open(cache_path, "w") as f:
                json.dump({"_version": 999, "cities": []}, f)
            old_file = mod.CACHE_FILE
            old_loaded = mod._loaded
            old_db = mod._db
            try:
                mod.CACHE_FILE = cache_path
                mod._loaded = False
                mod._db = []
                # Mock download to avoid network call
                with patch.object(mod, '_download_and_build', return_value=[["TestCity", 0, 0]]):
                    mod._load()
                assert mod._db == [["TestCity", 0, 0]]
                assert mod._loaded is True
            finally:
                mod.CACHE_FILE = old_file
                mod._loaded = old_loaded
                mod._db = old_db
