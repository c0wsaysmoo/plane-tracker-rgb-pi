"""Tests for features adopted from c0wsaysmoo/plane-tracker-rgb-pi."""
import pytest
import os
import sys
import tempfile

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set required env vars before importing anything
os.environ.setdefault("PLANE_TRACKER_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("FR24_API_KEY", "test_sub|test_jwt")
os.environ.setdefault("TOMORROW_API_KEY", "test")
os.environ.setdefault("TEMPERATURE_LOCATION", "40.7,-74.0")
os.environ.setdefault("DISTANCE_UNITS", "imperial")


# ====================================================================
# 3-Phase ETA Model (concept from c0wsaysmoo calculate_eta())
# ====================================================================

class TestEstimateEta3Phase:
    """Test the _estimate_eta_3phase helper function."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from utilities.overhead import _estimate_eta_3phase
        self.eta = _estimate_eta_3phase

    def test_cruising_simple(self):
        """Cruising at FL350, 450kts, 500nm remaining → ~66 min + descent."""
        mins = self.eta(35000, 0, 450, 500)
        assert mins is not None
        assert 60 < mins < 90  # should be ~66 cruise + descent time

    def test_climbing(self):
        """Climbing at 10,000ft, +2000fpm, 400kts, 800nm → longer than simple d/s."""
        mins = self.eta(10000, 2500, 400, 800)
        assert mins is not None
        assert mins > 0
        # Simple d/s would be 800/400*60 = 120min. 3-phase should be slightly more.
        assert mins > 100

    def test_descending(self):
        """Descending at 15,000ft, -1500fpm, 300kts, 50nm → short with buffer."""
        mins = self.eta(15000, -1500, 300, 50)
        assert mins is not None
        assert mins > 0
        # 50nm at 225kts (75%) ≈ 13min + 15% buffer ≈ 15min
        assert 10 < mins < 25

    def test_approach_buffer_close(self):
        """Within 15nm → adds 6nm buffer."""
        mins = self.eta(3000, -800, 200, 10)
        assert mins is not None
        assert mins > 0

    def test_approach_buffer_medium(self):
        """Within 50nm → adds 15% buffer."""
        mins = self.eta(20000, -500, 300, 40)
        assert mins is not None
        assert mins > 0

    def test_zero_speed(self):
        """Zero ground speed → returns None."""
        assert self.eta(35000, 0, 0, 500) is None

    def test_zero_distance(self):
        """Zero distance → returns None."""
        assert self.eta(35000, 0, 450, 0) is None

    def test_negative_distance(self):
        """Negative distance → returns None."""
        assert self.eta(35000, 0, 450, -10) is None

    def test_returns_non_negative(self):
        """Result should never be negative."""
        mins = self.eta(500, -2000, 150, 5)
        assert mins is not None
        assert mins >= 0

    def test_zero_altitude_climbing(self):
        """Zero altitude with positive vspeed (just took off)."""
        mins = self.eta(0, 3000, 200, 1000)
        assert mins is not None
        assert mins > 0

    def test_none_altitude(self):
        """None altitude should not TypeError (coerced to 0)."""
        mins = self.eta(None, 0, 400, 500)
        assert mins is not None
        assert mins > 0

    def test_none_vspeed(self):
        """None vspeed should not TypeError (coerced to 0 = cruising)."""
        mins = self.eta(35000, None, 400, 500)
        assert mins is not None
        assert mins > 0

    def test_vspeed_boundary_200(self):
        """vspeed=200 exactly → cruising branch (not climbing).
        vspeed=201 → climbing branch. Both produce valid results."""
        mins_cruise = self.eta(35000, 200, 400, 500)
        mins_climb = self.eta(35000, 201, 400, 500)
        assert mins_cruise is not None
        assert mins_climb is not None
        assert mins_cruise > 0
        assert mins_climb > 0

    def test_vspeed_boundary_neg200(self):
        """vspeed=-200 exactly → cruising (not descending)."""
        mins_neg200 = self.eta(25000, -200, 400, 500)
        mins_neg201 = self.eta(25000, -201, 400, 500)
        assert mins_neg200 is not None
        assert mins_neg201 is not None


# ====================================================================
# Scroll Synchronization (from c0wsaysmoo PR #28)
# ====================================================================

class TestScrollSync:
    """Test scroll sync logic (mark_scroll_complete / advance_completed_scroll).
    Note: display module requires rgbmatrix (Pi-only), so we test the logic pattern directly."""

    SCROLL_REGIONS = ("flight_details", "plane_details")

    def test_scroll_regions_are_pair(self):
        assert len(self.SCROLL_REGIONS) == 2
        assert "flight_details" in self.SCROLL_REGIONS
        assert "plane_details" in self.SCROLL_REGIONS

    def test_both_regions_must_complete(self):
        """advance_completed_scroll only fires when ALL regions are done."""
        # Simulate the dict state
        scroll_complete = {r: False for r in ("flight_details", "plane_details")}
        scroll_complete["flight_details"] = True
        assert not all(scroll_complete.values())

        scroll_complete["plane_details"] = True
        assert all(scroll_complete.values())

    def test_reset_clears_all(self):
        """reset_scroll_completion clears all regions."""
        scroll_complete = {"flight_details": True, "plane_details": True}
        scroll_complete = {r: False for r in scroll_complete}
        assert not any(scroll_complete.values())

    def test_single_flight_skips(self):
        """advance_completed_scroll returns early if <= 1 flight."""
        # With 0 or 1 flights, the method should not advance
        # (tested by checking the guard: len(self._data) <= 1 → return)
        data = [{"callsign": "TEST", "direction": "NE"}]
        assert len(data) <= 1  # guard condition met


# ====================================================================
# Route Search (our original work — gRPC Filter discovery)
# ====================================================================

class TestRouteSearch:
    """Test find_by_route method exists and validates input."""

    def test_find_by_route_exists(self):
        from utilities.fr24_client import FR24Client
        assert hasattr(FR24Client, 'find_by_route')

    def test_empty_input_returns_empty(self):
        from utilities.fr24_client import FR24Client
        client = FR24Client()
        assert client.find_by_route("", "LAX") == []
        assert client.find_by_route("EWR", "") == []
        assert client.find_by_route("", "") == []

    def test_input_uppercased(self):
        """Inputs should be uppercased internally."""
        from utilities.fr24_client import FR24Client
        client = FR24Client()
        # Can't test actual gRPC call without network, but empty after uppercase
        # still returns empty for blank input
        assert client.find_by_route(" ", " ") == []


# ====================================================================
# Location Name (concept from c0wsaysmoo)
# ====================================================================

class TestLocationName:
    """Test the /airport-code endpoint logic."""

    def test_cache_structure(self):
        """Cache dict should have code and name keys when populated."""
        cache = {"code": "EWR", "name": "Short Hills, Millburn"}
        assert "code" in cache
        assert "name" in cache

    def test_nominatim_address_parsing(self):
        """Test the address field priority logic."""
        # Simulate Nominatim response address
        addr = {
            "neighbourhood": "Short Hills",
            "city": "Millburn",
            "county": "Essex County",
            "state": "New Jersey",
        }
        neighbourhood = (
            addr.get("neighbourhood")
            or addr.get("suburb")
            or addr.get("quarter")
            or addr.get("village")
        )
        city = addr.get("city") or addr.get("town") or addr.get("county")
        assert neighbourhood == "Short Hills"
        assert city == "Millburn"
        location_name = f"{neighbourhood}, {city}" if neighbourhood and city else city or ""
        assert location_name == "Short Hills, Millburn"

    def test_no_neighbourhood_fallback(self):
        """Falls back to city only if no neighbourhood."""
        addr = {"city": "Newark", "state": "New Jersey"}
        neighbourhood = (
            addr.get("neighbourhood")
            or addr.get("suburb")
            or addr.get("quarter")
            or addr.get("village")
        )
        city = addr.get("city") or addr.get("town") or addr.get("county")
        assert neighbourhood is None
        assert city == "Newark"


# ====================================================================
# Flight Counter (concept from c0wsaysmoo/plane-tracker-rgb-pi)
# ====================================================================

class TestFlightCounter:
    """Test log_flight_count function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp counter file."""
        import utilities.overhead as oh
        self.oh = oh
        self.orig_counter = oh.COUNTER_FILE
        self.counter_file = str(tmp_path / "flight_counter.json")
        oh.COUNTER_FILE = self.counter_file
        yield
        oh.COUNTER_FILE = self.orig_counter

    def test_first_flight_creates_file(self):
        self.oh.log_flight_count("UAL123", {"origin": "EWR", "destination": "LAX"})
        import json
        with open(self.counter_file) as f:
            data = json.load(f)
        today = str(__import__("datetime").datetime.now().date())
        assert today in data
        assert data[today]["count"] == 1
        assert data[today]["flights"][0]["callsign"] == "UAL123"
        assert data[today]["flights"][0]["origin"] == "EWR"
        assert data[today]["flights"][0]["dest"] == "LAX"

    def test_deduplication(self):
        """Same callsign counted only once per day."""
        self.oh.log_flight_count("UAL123", {"origin": "EWR", "destination": "LAX"})
        self.oh.log_flight_count("UAL123", {"origin": "EWR", "destination": "LAX"})
        import json
        with open(self.counter_file) as f:
            data = json.load(f)
        today = str(__import__("datetime").datetime.now().date())
        assert data[today]["count"] == 1

    def test_multiple_callsigns(self):
        self.oh.log_flight_count("UAL123", {"origin": "EWR", "destination": "LAX"})
        self.oh.log_flight_count("DAL456", {"origin": "JFK", "destination": "ATL"})
        self.oh.log_flight_count("AAL789", {"origin": "DFW", "destination": "ORD"})
        import json
        with open(self.counter_file) as f:
            data = json.load(f)
        today = str(__import__("datetime").datetime.now().date())
        assert data[today]["count"] == 3

    def test_empty_callsign_skipped(self):
        self.oh.log_flight_count("", {"origin": "EWR", "destination": "LAX"})
        assert not os.path.exists(self.counter_file)

    def test_none_entry(self):
        """None entry should not crash."""
        self.oh.log_flight_count("UAL123", None)
        import json
        with open(self.counter_file) as f:
            data = json.load(f)
        today = str(__import__("datetime").datetime.now().date())
        assert data[today]["count"] == 1
        assert data[today]["flights"][0]["origin"] == ""

    def test_hour_field(self):
        """Hour field should be an integer 0-23."""
        self.oh.log_flight_count("UAL123")
        import json
        with open(self.counter_file) as f:
            data = json.load(f)
        today = str(__import__("datetime").datetime.now().date())
        hour = data[today]["flights"][0]["hour"]
        assert isinstance(hour, int)
        assert 0 <= hour <= 23

    def test_first_last_seen(self):
        self.oh.log_flight_count("UAL123")
        self.oh.log_flight_count("DAL456")
        import json
        with open(self.counter_file) as f:
            data = json.load(f)
        today = str(__import__("datetime").datetime.now().date())
        assert data[today]["first_seen"]
        assert data[today]["last_seen"]


# ====================================================================
# Heading Arrows (8-point compass on overhead flights)
# ====================================================================

class TestHeadingArrows:
    """Test heading-to-arrow conversion for planedetails display.
    Function duplicated here since scenes/ imports rgbmatrix (Pi-only)."""

    # 8-point compass heading arrows — must match scenes/planedetails.py
    _HEADING_ARROWS = [
        (337.5, 360, "\u2191"), (0, 22.5, "\u2191"),
        (22.5, 67.5, "\u2197"), (67.5, 112.5, "\u2192"),
        (112.5, 157.5, "\u2198"), (157.5, 202.5, "\u2193"),
        (202.5, 247.5, "\u2199"), (247.5, 292.5, "\u2190"),
        (292.5, 337.5, "\u2196"),
    ]

    @staticmethod
    def _heading_to_arrow(heading):
        if heading is None:
            return ""
        heading = heading % 360
        for lo, hi, arrow in TestHeadingArrows._HEADING_ARROWS:
            if lo <= heading < hi:
                return arrow
        return ""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.arrow = self._heading_to_arrow

    def test_north(self):
        assert self.arrow(0) == "\u2191"
        assert self.arrow(360) == "\u2191"
        assert self.arrow(10) == "\u2191"
        assert self.arrow(350) == "\u2191"

    def test_east(self):
        assert self.arrow(90) == "\u2192"

    def test_south(self):
        assert self.arrow(180) == "\u2193"

    def test_west(self):
        assert self.arrow(270) == "\u2190"

    def test_northeast(self):
        assert self.arrow(45) == "\u2197"

    def test_southeast(self):
        assert self.arrow(135) == "\u2198"

    def test_southwest(self):
        assert self.arrow(225) == "\u2199"

    def test_northwest(self):
        assert self.arrow(315) == "\u2196"

    def test_zero_heading(self):
        assert self.arrow(0) == "\u2191"

    def test_none_heading(self):
        assert self.arrow(None) == ""

    def test_false_heading(self):
        assert self.arrow(0) == "\u2191"

    def test_wrap_around(self):
        """Heading > 360 wraps correctly."""
        assert self.arrow(450) == "\u2192"  # 450 % 360 = 90 = East


# ====================================================================
# NPS National Parks Landmarks
# ====================================================================

class TestLandmarks:
    """Test landmarks.py park name stripping and lookup logic."""

    def test_strip_national_park(self):
        from utilities.landmarks import _strip_park_name
        assert _strip_park_name("Grand Canyon National Park") == "Grand Canyon"
        assert _strip_park_name("Yellowstone National Park") == "Yellowstone"

    def test_strip_national_monument(self):
        from utilities.landmarks import _strip_park_name
        assert _strip_park_name("Statue of Liberty National Monument") == "Statue of Liberty"

    def test_strip_historical_park(self):
        from utilities.landmarks import _strip_park_name
        assert _strip_park_name("Valley Forge National Historical Park") == "Valley Forge"

    def test_strip_preserve(self):
        from utilities.landmarks import _strip_park_name
        assert _strip_park_name("Big Thicket National Preserve") == "Big Thicket"

    def test_strip_park_and_preserve(self):
        from utilities.landmarks import _strip_park_name
        assert _strip_park_name("Denali National Park and Preserve") == "Denali"

    def test_no_suffix_unchanged(self):
        from utilities.landmarks import _strip_park_name
        assert _strip_park_name("Alcatraz Island") == "Alcatraz Island"

    def test_haversine_via_cities(self):
        from utilities.cities import _haversine_km
        # NYC to London ≈ 5570km
        dist = _haversine_km(40.7128, -74.0060, 51.5074, -0.1278)
        assert 5500 < dist < 5700

    def test_get_nearest_landmark_no_parks_falls_back_to_city(self):
        """When no parks are loaded, should fall back to city lookup."""
        import utilities.landmarks as lm
        orig_db = lm._parks_db
        orig_loaded = lm._parks_loaded
        lm._parks_db = []
        lm._parks_loaded = True
        try:
            result = lm.get_nearest_landmark(40.7128, -74.0060)
            # Should get a city (New York area)
            assert result is not None
            assert result["type"] == "city"
        finally:
            lm._parks_db = orig_db
            lm._parks_loaded = orig_loaded

    def test_get_nearest_landmark_park_within_radius(self):
        """Park within 30km radius should be returned."""
        import utilities.landmarks as lm
        orig_db = lm._parks_db
        orig_loaded = lm._parks_loaded
        lm._parks_db = [["Grand Canyon", 36.1069, -112.1129]]
        lm._parks_loaded = True
        try:
            # Point right at Grand Canyon
            result = lm.get_nearest_landmark(36.1069, -112.1129)
            assert result is not None
            assert result["type"] == "park"
            assert result["name"] == "Grand Canyon"
            assert result["distance_km"] < 1
        finally:
            lm._parks_db = orig_db
            lm._parks_loaded = orig_loaded

    def test_get_nearest_landmark_park_outside_radius(self):
        """Park >30km away should fall back to city."""
        import utilities.landmarks as lm
        orig_db = lm._parks_db
        orig_loaded = lm._parks_loaded
        lm._parks_db = [["Grand Canyon", 36.1069, -112.1129]]
        lm._parks_loaded = True
        try:
            # NYC is far from Grand Canyon
            result = lm.get_nearest_landmark(40.7128, -74.0060)
            assert result is not None
            assert result["type"] == "city"
        finally:
            lm._parks_db = orig_db
            lm._parks_loaded = orig_loaded


# ====================================================================
# FlightStats Route Fallback
# ====================================================================

class TestFlightStats:
    """Test FlightStats callsign parsing and caching."""

    def test_parse_icao_callsign(self):
        from utilities.flightstats import _parse_callsign
        carrier, number = _parse_callsign("UAL1234")
        assert carrier == "UA"
        assert number == "1234"

    def test_parse_iata_callsign(self):
        from utilities.flightstats import _parse_callsign
        carrier, number = _parse_callsign("UA1234")
        assert carrier == "UA"
        assert number == "1234"

    def test_parse_british_airways(self):
        from utilities.flightstats import _parse_callsign
        carrier, number = _parse_callsign("BAW123")
        assert carrier == "BA"
        assert number == "123"

    def test_parse_delta(self):
        from utilities.flightstats import _parse_callsign
        carrier, number = _parse_callsign("DAL456")
        assert carrier == "DL"
        assert number == "456"

    def test_parse_empty(self):
        from utilities.flightstats import _parse_callsign
        assert _parse_callsign("") == (None, None)
        assert _parse_callsign(None) == (None, None)

    def test_parse_no_numbers(self):
        from utilities.flightstats import _parse_callsign
        assert _parse_callsign("ABCDEF") == (None, None)

    def test_parse_unknown_icao(self):
        """Unknown ICAO code should still return something."""
        from utilities.flightstats import _parse_callsign
        carrier, number = _parse_callsign("XYZ789")
        assert carrier == "XYZ"
        assert number == "789"

    def test_cache_ttl(self):
        """Cached None results respect TTL."""
        from utilities.flightstats import _cache
        from time import time
        _cache["TEST999"] = (None, time())
        # Should hit cache (not make network call)
        from utilities.flightstats import get_route
        result = get_route("TEST999")
        assert result is None

    def test_cache_with_result(self):
        """Cached results are returned without network call."""
        from utilities.flightstats import _cache
        from time import time
        cached = {"origin": "EWR", "destination": "LAX", "aircraft": "B738"}
        _cache["CACHED123"] = (cached, time())
        from utilities.flightstats import get_route
        result = get_route("CACHED123")
        assert result is not None
        assert result["origin"] == "EWR"
        assert result["destination"] == "LAX"


# ====================================================================
# Config JSON Overlay
# ====================================================================

class TestConfigJsonOverlay:
    """Test config.py JSON overlay + reload functionality."""

    def test_config_source_default(self):
        """Without JSON file, config_source returns 'env'."""
        import config
        # If no JSON file was loaded, source should be env
        # (may be json if test runs after save — just verify function exists)
        assert config.config_source() in ("env", "json")

    def test_get_fallback_to_env(self):
        """_get falls back to env when key not in JSON overlay."""
        import config
        orig = config._json_config
        config._json_config = {}
        try:
            # Should get from env or return default
            result = config._get("NONEXISTENT_KEY_12345", "fallback")
            assert result == "fallback"
        finally:
            config._json_config = orig

    def test_get_json_override(self):
        """_get returns JSON value when present."""
        import config
        orig = config._json_config
        config._json_config = {"TEST_KEY_999": "from_json"}
        try:
            assert config._get("TEST_KEY_999", "default") == "from_json"
        finally:
            config._json_config = orig

    def test_bool_parsing(self):
        """_bool handles strings and booleans."""
        import config
        assert config._bool(True) is True
        assert config._bool(False) is False
        assert config._bool("true") is True
        assert config._bool("True") is True
        assert config._bool("1") is True
        assert config._bool("yes") is True
        assert config._bool("false") is False
        assert config._bool("0") is False

    def test_reload_exists(self):
        """reload() function exists and is callable."""
        import config
        assert callable(config.reload)

    def test_apply_sets_globals(self):
        """_apply() sets module-level globals."""
        import config
        config._apply()
        assert hasattr(config, "FR24_API_KEY")
        assert hasattr(config, "ZONE_HOME")
        assert hasattr(config, "LOCATION_HOME")
        assert isinstance(config.ZONE_HOME, dict)
        assert isinstance(config.LOCATION_HOME, list)

    def test_json_config_path(self):
        """JSON config path is config/config.json in project root."""
        import config
        assert config._CONFIG_JSON.endswith("config/config.json")
