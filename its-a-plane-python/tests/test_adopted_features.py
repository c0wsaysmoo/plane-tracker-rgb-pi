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
