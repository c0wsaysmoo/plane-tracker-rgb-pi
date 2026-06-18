import os
import json
import math
import logging
import requests
from time import time
from datetime import datetime, timezone, timedelta
from threading import Thread, Lock

from utilities.fr24_client import FR24Client
from httpx import ConnectError, TimeoutException

logger = logging.getLogger(__name__)

from config import (
    DISTANCE_UNITS,
    MAX_FARTHEST,
    MAX_CLOSEST,
)

from setup import email_alerts

# Lazy imports — folium (used by map_generator) may not be installed in test envs
map_generator = None
upload_helper = None

def _ensure_map_imports():
    global map_generator, upload_helper
    if map_generator is None:
        from web import map_generator as _mg, upload_helper as _uh
        map_generator = _mg
        upload_helper = _uh

# Optional config values
try:
    from config import MIN_ALTITUDE
except (ImportError, ModuleNotFoundError, NameError):
    MIN_ALTITUDE = 0

try:
    from config import ZONE_HOME, LOCATION_HOME
    ZONE_DEFAULT = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME
except (ImportError, ModuleNotFoundError, NameError):
    ZONE_DEFAULT = {"tl_y": 41.904318, "tl_x": -87.647367,
                    "br_y": 41.851654, "br_x": -87.573027}
    LOCATION_DEFAULT = [41.882724, -87.623350]


# Local databases for offline lookups (no API calls needed)
try:
    from utilities.airports import get_airport_coords as _local_airport_coords
    _HAS_LOCAL_AIRPORTS = True
except ImportError:
    _HAS_LOCAL_AIRPORTS = False

try:
    from utilities.airlines import get_airline_name as _local_airline_name
    _HAS_LOCAL_AIRLINES = True
except ImportError:
    _HAS_LOCAL_AIRLINES = False

# Constants
RETRIES = 3
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 100000
EARTH_RADIUS_M = 3958.8
ADSBDB_BASE = "https://api.adsbdb.com"

# Helicopter types — set owner_icao to "HELI" for helicopter logo display
HELICOPTER_TYPES = {
    "S76", "EC35", "EC55", "EC30", "A109", "A139", "A169",
    "B06", "B407", "B429", "R44", "R66", "R22",
    "AS50", "AS55", "AS65", "H60", "BK17", "MD52", "MD50",
    "S92", "AW13", "AW16", "AW10", "B212", "B412",
    "EC45", "EC75", "S61", "S70", "H500", "BALL",
}

# Multi-brand regionals — these operators fly for multiple airlines.
# For these, we use flight_number from flight_details to determine the
# marketing brand (the airline the passenger bought the ticket from).
AMBIGUOUS_REGIONALS = {
    # US regionals
    "RPA", "SKW", "ENY", "JIA", "EDV", "GJS", "CPZ", "ASQ", "PDT", "JZA",
    # European regionals (operate under major carrier brands)
    "CLH", "LHX", "DLA", "HOP", "KLC", "CFE", "ANE", "BCY", "EAI", "FCM", "GER",
}

# Marketing IATA prefix → brand display name
MARKETING_BRANDS = {
    # US
    "UA": "United Airlines", "AA": "American Airlines", "DL": "Delta Air Lines",
    "AS": "Alaska Airlines", "WN": "Southwest Airlines",
    "B6": "JetBlue Airways", "NK": "Spirit Airlines", "F9": "Frontier Airlines",
    # European
    "LH": "Lufthansa", "BA": "British Airways", "AF": "Air France",
    "KL": "KLM", "IB": "Iberia", "SK": "SAS", "EI": "Aer Lingus",
    "AY": "Finnair", "AC": "Air Canada",
}

# IATA 2-letter → ICAO 3-letter (for logo file lookup)
IATA_TO_ICAO = {
    "AA": "AAL", "UA": "UAL", "DL": "DAL", "AS": "ASA", "WN": "SWA",
    "B6": "JBU", "NK": "NKS", "F9": "FFT", "LH": "DLH", "BA": "BAW",
    "AF": "AFR", "KL": "KLM", "IB": "IBE", "SK": "SAS", "EI": "EIN",
    "AY": "FIN", "AC": "ACA", "JL": "JAL", "NH": "ANA", "QF": "QFA",
    # Regional operators (for cs_airline_iata → ICAO prefix in route-based search)
    "YX": "RPA", "MQ": "ENY", "OH": "JIA", "PT": "PDT", "OO": "SKW",
    "9E": "EDV", "G7": "GJS", "QX": "QXE",
}

# Mainline → regional operator ICAO prefixes
# Regional carriers fly under mainline flight numbers but use their own ICAO callsigns
# Updated 2026-06-16; AirLabs flight_icao (Strategy 3) handles all carriers globally —
# this static map is a fallback when AirLabs doesn't return flight_icao.
REGIONAL_OPERATORS = {
    # US mainline
    "AAL": ["ENY", "JIA", "PDT", "RPA", "SKW"],  # Envoy, PSA, Piedmont, Republic, SkyWest
    "UAL": ["RPA", "SKW", "GJS"],                  # Republic, SkyWest, GoJet
    "DAL": ["EDV", "RPA", "SKW"],                  # Endeavor, Republic, SkyWest
    "ASA": ["QXE", "SKW"],                          # Horizon Air, SkyWest
    # European mainline
    "BAW": ["CFE", "SHT"],                 # BA CityFlyer, BA Shuttle (domestic UK callsign)
    "DLH": ["LHX", "DLA"],                 # Lufthansa City Airlines, Air Dolomiti
    "AFR": ["HOP"],                         # HOP! (Air France regional)
    "KLM": ["KLC"],                         # KLM Cityhopper
    "IBE": ["IBS", "ANE"],                 # Iberia Express, Air Nostrum
    "SAS": ["SZS", "SVS"],                 # SAS Connect, SAS Link
    "FIN": ["FCM"],                         # Norra (Nordic Regional Airlines)
    "EIN": ["EAI"],                         # Emerald Airlines (Aer Lingus Regional)
    # Canadian
    "ACA": ["JZA", "PVL"],                 # Jazz Aviation, PAL Airlines
    # Japanese
    "JAL": ["JLJ", "JAC", "JTA", "NTH", "RAC"],  # J-Air, JAC, JTA, Hokkaido, Ryukyu
    "ANA": ["AKX"],                         # ANA Wings
    # Australian
    "QFA": ["QLK", "SSQ", "NWK", "NJS"],  # QantasLink, Sunstate, Network, NJS
}

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# Writable data directory — outside home dir to avoid systemd ProtectHome issues
DATA_DIR = os.environ.get("PLANE_TRACKER_DATA_DIR", "/var/lib/plane-tracker")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, "close.txt")
LOG_FILE_FARTHEST = os.path.join(DATA_DIR, "farthest.txt")
TRACKED_FILE = os.path.join(DATA_DIR, "tracked_flight.json")
MAPS_DIR = os.path.join(DATA_DIR, "maps")
os.makedirs(MAPS_DIR, exist_ok=True)
COUNTER_FILE = os.path.join(DATA_DIR, "flight_counter.json")

# In-memory caches for adsbdb lookups (GA aircraft owner info)
_aircraft_cache = {}  # registration -> {data, ts}
_CACHE_TTL = 3600     # 1 hour
_CACHE_MAX_SIZE = 500  # Evict oldest entries beyond this


# Utility Functions

def safe_load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError) as e:
        if isinstance(e, (PermissionError, OSError)):
            logger.warning(f"Permission denied reading {path} — attempting to fix")
            try:
                os.chmod(path, 0o666)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except Exception:
                pass
        return []


def safe_write_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        # Ensure the file is readable/writable by all users
        os.chmod(path, 0o666)
    except PermissionError:
        logger.warning(f"Permission denied writing {path} — attempting to fix")
        try:
            os.chmod(path, 0o666)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e2:
            logger.error(f"Cannot write {path}: {e2}")


def ordinal(n: int):
    return f"{n}{'tsnrhtdd'[(n//10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"


def haversine(lat1, lon1, lat2, lon2):
    """Distance between two points. Returns miles or km based on DISTANCE_UNITS.
    Returns 0 if any coordinate is None (fixes: haversine guard for None values)."""
    # Guard against None values — use `any(v is None ...)` instead of `not all(...)`
    # because `not all(...)` fails for airports at 0.0 latitude/longitude
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return 0
    lat1, lon1 = map(math.radians, (lat1, lon1))
    lat2, lon2 = map(math.radians, (lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2)**2 +
        math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    miles = EARTH_RADIUS_M * c
    return miles * 1.609344 if DISTANCE_UNITS == "metric" else miles


def _estimate_eta_3phase(altitude_ft, vspeed_fpm, ground_speed_kts, dist_remaining_nm):
    """3-phase ETA estimate: climb/cruise/descent with approach buffer.

    Concept from c0wsaysmoo/plane-tracker-rgb-pi calculate_eta().
    Returns estimated minutes to destination, or None if inputs are invalid.
    """
    if not ground_speed_kts or ground_speed_kts <= 0 or dist_remaining_nm <= 0:
        return None

    altitude_ft = altitude_ft or 0
    vspeed_fpm = vspeed_fpm or 0

    CRUISE_ALT = 35000  # typical cruise altitude in feet
    CLIMB_RATE = 2000   # feet per minute
    DESCENT_RATIO = 3   # 3:1 glide rule — 3nm per 1000ft descent

    remaining_nm = dist_remaining_nm

    # Estimate descent distance (top-of-descent to destination)
    descent_alt = min(altitude_ft, CRUISE_ALT)
    tod_nm = (descent_alt / 1000) * DESCENT_RATIO
    descent_speed = ground_speed_kts * 0.75
    descent_mins = (tod_nm / descent_speed * 60) if descent_speed > 0 else 0

    if vspeed_fpm > 200:
        # Climbing — estimate time to cruise, then cruise the rest
        alt_to_climb = max(0, CRUISE_ALT - altitude_ft)
        climb_mins = alt_to_climb / CLIMB_RATE if CLIMB_RATE > 0 else 0
        climb_nm = (climb_mins / 60) * ground_speed_kts

        cruise_nm = max(0, remaining_nm - climb_nm - tod_nm)
        cruise_mins = (cruise_nm / ground_speed_kts * 60) if ground_speed_kts > 0 else 0
        total_mins = climb_mins + cruise_mins + descent_mins

    elif vspeed_fpm < -200:
        # Descending — use reduced speed for remaining distance
        total_mins = (remaining_nm / descent_speed * 60) if descent_speed > 0 else 0

    else:
        # Cruising — cruise to top-of-descent, then descent
        cruise_nm = max(0, remaining_nm - tod_nm)
        cruise_mins = (cruise_nm / ground_speed_kts * 60) if ground_speed_kts > 0 else 0
        total_mins = cruise_mins + descent_mins

    # Approach maneuvering buffer
    if remaining_nm <= 15:
        total_mins += (6 / ground_speed_kts * 60)  # 6nm buffer
    elif remaining_nm <= 50:
        total_mins *= 1.15  # 15% buffer

    return max(0, total_mins)


def estimate_stale_data(last_data):
    data = dict(last_data)
    data["is_live"] = False

    speed_kts = data.get("ground_speed", 0)
    last_ts   = data.get("last_seen_ts")

    if not last_ts:
        return data

    elapsed_hrs  = (time() - last_ts) / 3600
    elapsed_mins = elapsed_hrs * 60

    # --- Time remaining: subtract elapsed time from last known ---
    last_time_str = data.get("time_remaining", "")
    if last_time_str:
        # Parse "H:MM" or "Mm" format
        try:
            if ":" in last_time_str:
                parts = last_time_str.split(":")
                last_mins = int(parts[0]) * 60 + int(parts[1])
            else:
                last_mins = int(last_time_str.replace("m", ""))
            est_mins = max(0, last_mins - int(elapsed_mins))
            h = est_mins // 60
            m = est_mins % 60
            data["time_remaining"] = f"{h}:{m:02d}" if h > 0 else f"{m}m"
        except (ValueError, IndexError):
            pass

    # --- Distance remaining: subtract distance covered ---
    last_dist = data.get("dist_remaining")
    if last_dist is not None and speed_kts > 0:
        # Convert knots to display units per hour
        if DISTANCE_UNITS == "metric":
            speed_display = speed_kts * 1.852      # knots -> kph
        else:
            speed_display = speed_kts * 1.15078    # knots -> mph
        dist_covered = speed_display * elapsed_hrs
        data["dist_remaining"] = max(0, last_dist - dist_covered)

    return data


def degrees_to_cardinal(deg):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) / 45)
    return dirs[idx % 8]


def plane_bearing(flight, home=LOCATION_DEFAULT):
    lat1, lon1 = map(math.radians, home)
    lat2, lon2 = map(math.radians, (flight.latitude, flight.longitude))
    b = math.atan2(
        math.sin(lon2 - lon1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    )
    return (math.degrees(b) + 360) % 360


def distance_from_flight_to_home(flight):
    return haversine(
        flight.latitude, flight.longitude,
        LOCATION_DEFAULT[0], LOCATION_DEFAULT[1],
    )



# --- Local database lookups (no API calls needed) ---

def _airport_coords(code):
    """Look up airport coordinates from local database (no API calls).
    Accepts IATA (JFK, LHR) or ICAO (KJFK, EGLL). Returns {lat, lon} or empty dict."""
    if not code:
        return {}
    if _HAS_LOCAL_AIRPORTS:
        return _local_airport_coords(code)
    return {}


def _airline_name_lookup(icao_code):
    """Look up airline name from local database. Returns empty string if not found."""
    if not icao_code:
        return ""
    if _HAS_LOCAL_AIRLINES:
        return _local_airline_name(icao_code)
    return ""


def _evict_aircraft_cache():
    """Evict oldest entries if cache exceeds max size."""
    if len(_aircraft_cache) > _CACHE_MAX_SIZE:
        sorted_keys = sorted(_aircraft_cache, key=lambda k: _aircraft_cache[k]["ts"])
        for k in sorted_keys[:len(_aircraft_cache) - _CACHE_MAX_SIZE]:
            del _aircraft_cache[k]


def _adsbdb_aircraft(registration):
    """Fetch aircraft owner info by registration from adsbdb (free, cached 1hr).
    Used for GA flights (N-numbers) where FR24 has no airline name."""
    cached = _aircraft_cache.get(registration)
    if cached and (time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    url = f"{ADSBDB_BASE}/v0/aircraft/{registration}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 404:
            _aircraft_cache[registration] = {"data": {}, "ts": time()}
            _evict_aircraft_cache()
            return {}
        r.raise_for_status()
        ac = r.json().get("response", {}).get("aircraft")
        if not ac:
            _aircraft_cache[registration] = {"data": {}, "ts": time()}
            _evict_aircraft_cache()
            return {}
        result = {
            "owner": ac.get("registered_owner", ""),
            "type": ac.get("icao_type", ""),
            "manufacturer": ac.get("manufacturer", ""),
            "registration": ac.get("registration", ""),
        }
        _aircraft_cache[registration] = {"data": result, "ts": time()}
        _evict_aircraft_cache()
        return result
    except Exception as e:
        logger.debug(f"adsbdb aircraft error for {registration}: {e}")
        # Cache error for 5 minutes to avoid hammering
        _aircraft_cache[registration] = {"data": {}, "ts": time() - _CACHE_TTL + 300}
        return {}


def log_flight_count(callsign, entry=None):
    """Log unique callsign to daily flight counter. De-duplicates per day.
    Concept from c0wsaysmoo/plane-tracker-rgb-pi."""
    if not callsign:
        return
    if entry is None:
        entry = {}
    now = datetime.now()
    today = str(now.date())
    now_str = now.strftime("%H:%M:%S")

    try:
        with open(COUNTER_FILE, "r", encoding="utf-8") as f:
            log = json.load(f)
        if not isinstance(log, dict):
            log = {}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        log = {}

    if today not in log:
        log[today] = {"date": today, "count": 0, "flights": [],
                      "first_seen": now_str, "last_seen": now_str}

    seen = {e["callsign"] for e in log[today].get("flights", [])}
    if callsign not in seen:
        log[today]["flights"].append({
            "callsign": callsign,
            "time": now_str,
            "hour": now.hour,
            "origin": entry.get("origin", ""),
            "dest": entry.get("destination", ""),
            "aircraft": entry.get("plane", ""),
        })
        log[today]["count"] = len(log[today]["flights"])
        log[today]["last_seen"] = now_str

        # Prune entries older than configured retention period
        try:
            from config import STATS_LOG_DAYS
        except (ImportError, NameError):
            STATS_LOG_DAYS = 90
        cutoff = str((now - timedelta(days=STATS_LOG_DAYS)).date())
        old_keys = [k for k in log if k < cutoff and k != today]
        for k in old_keys:
            del log[k]

        safe_write_json(COUNTER_FILE, log)


def load_tracked_callsign():
    """Read tracked flight data from tracked_flight.json.

    Returns (callsign, scheduled_departure, cached_route) tuple.
    Concept: cached_route and scheduled_departure from c0wsaysmoo/plane-tracker-rgb-pi.
    """
    try:
        with open(TRACKED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cs = data.get("callsign", "").strip().upper()
        dep = data.get("scheduled_departure")   # Unix timestamp or None
        route = data.get("cached_route")         # dict saved at search time, or None
        return cs, dep, route
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError):
        return "", None, None


# Logging Closest Flights

def log_flight_data(entry: dict):
    try:
        entry["timestamp"] = email_alerts.get_timestamp()
        lst = safe_load_json(LOG_FILE)

        callsigns = {f.get("callsign"): f for f in lst}
        new_call = entry.get("callsign")
        new_dist = entry.get("distance", float("inf"))
        notify = False

        if new_call in callsigns:
            idx = next(i for i, f in enumerate(lst) if f.get("callsign") == new_call)
            if new_dist < lst[idx].get("distance", float("inf")):
                lst[idx] = entry
            else:
                return
        else:
            lst.append(entry)

        lst.sort(key=lambda x: x.get("distance", float("inf")))
        top_n = lst[:MAX_CLOSEST]

        if new_call not in [f["callsign"] for f in top_n]:
            return

        rank = next(i + 1 for i, f in enumerate(top_n) if f["callsign"] == new_call)

        if new_call not in callsigns:
            notify = True

        safe_write_json(LOG_FILE, top_n)

        if notify:
            _ensure_map_imports()
            html = map_generator.generate_closest_map(top_n, filename="closest.html")
            url = upload_helper.upload_map_to_server(html)
            subject = f"New {ordinal(rank)} Closest Flight - {entry.get('callsign','Unknown')}"
            email_alerts.send_flight_summary(subject, entry, map_url=url)

    except Exception as e:
        logger.error(f"Failed to log closest flight: {e}")


def log_farthest_flight(entry: dict):
    try:
        d_o = entry.get("distance_origin") or -1
        d_d = entry.get("distance_destination") or -1

        if d_o < 0 and d_d < 0:
            return

        reason = "origin" if d_o >= d_d else "destination"
        far = d_o if reason == "origin" else d_d
        airport = entry.get(reason)

        if not airport:
            return

        entry["timestamp"] = email_alerts.get_timestamp()
        entry["reason"] = reason
        entry["farthest_value"] = far
        entry["_airport"] = airport

        lst = safe_load_json(LOG_FILE_FARTHEST)
        airport_map = {f["_airport"]: f for f in lst}

        existing = airport_map.get(airport)
        notify = False
        updated = False

        if existing:
            if (entry.get("distance") or 9e9) < existing.get("distance", 9e9):
                lst = [entry if f["_airport"] == airport else f for f in lst]
                updated = True
            else:
                return
        else:
            if len(lst) >= MAX_FARTHEST:
                if far <= min(f["farthest_value"] for f in lst):
                    return
            lst.append(entry)
            notify = True

        lst.sort(key=lambda x: x["farthest_value"], reverse=True)
        lst = lst[:MAX_FARTHEST]
        safe_write_json(LOG_FILE_FARTHEST, lst)

        html = None
        if notify or updated:
            _ensure_map_imports()
            html = map_generator.generate_farthest_map(lst, filename="farthest.html")

        if notify and html:
            url = upload_helper.upload_map_to_server(html)
            rank = next(i for i, f in enumerate(lst) if f["_airport"] == airport) + 1
            cs = entry.get("callsign", "UNKNOWN")
            if rank == 1:
                subject = f"New Farthest Flight ({reason}) - {cs}"
            else:
                subject = f"{ordinal(rank)}-Farthest Flight ({reason}) - {cs}"
            email_alerts.send_flight_summary(subject, entry, reason, map_url=url)

    except Exception as e:
        logger.error(f"Failed to log farthest flight: {e}", exc_info=True)


# Overhead Class

class Overhead:
    def __init__(self):
        self._api = FR24Client()
        self._lock = Lock()
        self._data = []           # overhead flights
        self._tracked_data = None # tracked flight or None
        self._new_data = False
        self._processing = False
        self._tracked_was_live = False       # was the flight live last poll?
        self._tracked_miss_count = 0         # consecutive polls with no result
        self._TRACKED_MISS_THRESHOLD = 3     # fallback miss threshold (no ETA)
        self._MAX_TRACKED_HOURS = 36         # hard staleness cap for tracked flights
        self._tracked_last_callsign = ""     # last callsign we polled for
        self._tracked_last_eta = None        # last known estimated arrival (unix ts)
        self._tracked_last_data = None       # last known good tracked data
        self._tracked_schedule_cache = {}    # callsign -> AirLabs schedule result (or None)
        self._tracked_alt_callsign = ""     # operating carrier callsign found via regional lookup
        self._tracked_route_cached = None   # cached route dict (from search time or first-airborne)
        self._first_flight_logged = False    # log first flight details as JSON
        self._cycle_count = 0               # total grab_data cycles
        self._total_flights_seen = 0        # lifetime flight count

        # Eagerly load cities + parks DBs in background (avoids blocking render on first use)
        Thread(target=self._preload_cities, daemon=True).start()
        Thread(target=self._preload_parks, daemon=True).start()

    @staticmethod
    def _preload_cities():
        try:
            from utilities.cities import _load
            _load()
        except Exception:
            pass

    @staticmethod
    def _preload_parks():
        try:
            from utilities.landmarks import _load_parks
            _load_parks()
        except Exception:
            pass

    def _log_pipeline_summary(self, stats: dict):
        """
        Log a pretty summary of the data pipeline cycle.

        Displays: API calls made, data sources used, flights processed,
        helicopters detected, and data enrichment statistics.
        """
        elapsed = stats.get("elapsed_ms", 0)
        self._cycle_count += 1
        self._total_flights_seen += stats.get("flights_processed", 0)

        lines = [
            "",
            "┌─────────────────────────────────────────────────────────",
            f"│ 🛩️  Pipeline Cycle #{self._cycle_count}  ({elapsed:.0f}ms)",
            "├─────────────────────────────────────────────────────────",
            f"│ FR24 API Status:       {'✓ OK' if self._api.fr24_ok else '✗ UNREACHABLE'}",
            f"│ Zone flights (raw):    {stats.get('zone_raw', 0)}",
            f"│ After altitude filter: {stats.get('zone_filtered', 0)} "
            f"(min={MIN_ALTITUDE}ft, max={MAX_ALTITUDE}ft)",
            f"│ Flights processed:     {stats.get('flights_processed', 0)}",
            f"│ Details fetched (API): {stats.get('details_fetched', 0)}",
            "├─── Data Sources ───────────────────────────────────────",
            f"│ Local airports used:   {stats.get('airport_lookups', 0)} "
            f"({'✓ loaded' if _HAS_LOCAL_AIRPORTS else '✗ not available'})",
            f"│ Local airlines used:   {stats.get('airline_lookups', 0)} "
            f"({'✓ loaded' if _HAS_LOCAL_AIRLINES else '✗ not available'})",
            f"│ adsbdb GA lookups:     {stats.get('adsbdb_lookups', 0)}",
            f"│ Helicopter detected:   {stats.get('helicopters', 0)}",
        ]

        # Tracked flight info
        tracked = stats.get("tracked_status", "")
        if tracked:
            lines.append("├─── Tracked Flight ─────────────────────────────────────")
            lines.append(f"│ Status: {tracked}")
            if stats.get("tracked_callsign"):
                lines.append(f"│ Callsign: {stats['tracked_callsign']}")

        # Flight details table
        flights = stats.get("flight_details", [])
        if flights:
            lines.append("├─── Overhead Flights ───────────────────────────────────")
            lines.append("│  #  Callsign   Type  Route         Dist   Source")
            lines.append("│ ─── ────────── ───── ───────────── ────── ──────────")
            for i, fd in enumerate(flights, 1):
                cs = fd.get("callsign", "?")[:9].ljust(9)
                ac = fd.get("plane", "?")[:5].ljust(5)
                orig = fd.get("origin", "?")[:3]
                dest = fd.get("destination", "?")[:3]
                route = f"{orig}→{dest}".ljust(13)
                dist = f"{fd.get('distance', 0):.1f}".rjust(5)
                src = fd.get("data_source", "fr24")[:10]
                lines.append(f"│ {i:>2}  {cs} {ac} {route} {dist}  {src}")

        lines.append("├─── Lifetime Stats ─────────────────────────────────────")
        lines.append(f"│ Total cycles:         {self._cycle_count}")
        lines.append(f"│ Total flights seen:   {self._total_flights_seen}")
        lines.append(f"│ Aircraft cache size:  {len(_aircraft_cache)}")
        lines.append("└─────────────────────────────────────────────────────────")

        logger.info("\n".join(lines))

    def grab_data(self):
        with self._lock:
            if self._processing:
                logger.debug("grab_data: previous _grab still running, skipping")
                return
            self._processing = True
        Thread(target=self._grab, daemon=True).start()

    def safe_get(self, d, *keys, default=None):
        for key in keys:
            if d is None or not isinstance(d, dict):
                return default
            d = d.get(key)
        return d if d is not None else default

    def _grab(self):
        with self._lock:
            self._new_data = False

        overhead_data = []
        tracked_data = None
        _grab_start = time()

        # Pipeline stats for diagnostic logging
        stats = {
            "zone_raw": 0,
            "zone_filtered": 0,
            "flights_processed": 0,
            "details_fetched": 0,
            "airport_lookups": 0,
            "airline_lookups": 0,
            "adsbdb_lookups": 0,
            "helicopters": 0,
            "tracked_status": "",
            "tracked_callsign": "",
            "flight_details": [],
        }

        try:
            # --- STEP 1: Check zone for overhead flights ---
            flights = self._api.get_flights(bounds=ZONE_DEFAULT)
            stats["zone_raw"] = len(flights)
            flights = [f for f in flights if MIN_ALTITUDE < f.altitude < MAX_ALTITUDE]
            # Filter out blocked callsigns
            try:
                from config import BLOCKED_CALLSIGNS
                if BLOCKED_CALLSIGNS:
                    flights = [f for f in flights if f.callsign.upper() not in BLOCKED_CALLSIGNS]
            except (ImportError, NameError):
                pass
            stats["zone_filtered"] = len(flights)
            flights.sort(key=lambda f: distance_from_flight_to_home(f))
            flights = flights[:MAX_FLIGHT_LOOKUP]

            for f in flights:
                retries = RETRIES
                while retries:
                    try:
                        d = self._api.get_flight_details(f)
                        stats["details_fetched"] += 1

                        if not d:
                            retries -= 1
                            continue

                        # Log first flight details as pretty JSON for debugging
                        if not self._first_flight_logged:
                            self._first_flight_logged = True
                            logger.info(
                                "First flight API response:\n%s",
                                json.dumps(d, indent=2, default=str),
                            )

                        # Aircraft type from details, fallback to live feed
                        plane = self.safe_get(d, "aircraft", "model", "code", default="") or f.aircraft_code or ""

                        # Airline name: try local database first, then FR24's registered_owners
                        flight_number = self.safe_get(d, "schedule_info", "flight_number", default="")
                        airline_name = self.safe_get(d, "aircraft_info", "registered_owners", default="")

                        # Determine airline ICAO from callsign prefix
                        owner_icao = f.airline_icao or ""

                        # Marketing brand lookup for ambiguous regionals
                        if owner_icao in AMBIGUOUS_REGIONALS and flight_number:
                            # flight_number is like "AA4370" — extract IATA prefix
                            iata_prefix = flight_number[:2] if len(flight_number) >= 3 else ""
                            brand = MARKETING_BRANDS.get(iata_prefix, "")
                            if brand:
                                airline_name = brand
                                # Update logo to match marketing brand
                                brand_icao = IATA_TO_ICAO.get(iata_prefix)
                                if brand_icao:
                                    owner_icao = brand_icao
                            else:
                                # Fallback: use local DB or registered_owners
                                local_airline = _airline_name_lookup(owner_icao)
                                if local_airline:
                                    airline_name = local_airline
                                    stats["airline_lookups"] += 1
                        else:
                            # Non-regional: use local DB if available
                            local_airline = _airline_name_lookup(owner_icao)
                            if local_airline:
                                airline_name = local_airline
                                stats["airline_lookups"] += 1

                        # Helicopter detection — override owner_icao for logo display
                        if plane in HELICOPTER_TYPES:
                            owner_icao = "HELI"
                            stats["helicopters"] += 1

                        # GA airplane icon for N-number flights (no airline ICAO prefix)
                        elif not owner_icao and f.registration and f.registration.startswith("N") and f.registration[1:2].isdigit():
                            owner_icao = "GA"

                        # GA owner lookup for N-number aircraft with no airline
                        if (not airline_name and f.registration
                                and f.registration.startswith("N")
                                and f.registration[1:2].isdigit()):
                            stats["adsbdb_lookups"] += 1
                            ac_info = _adsbdb_aircraft(f.registration)
                            if ac_info.get("owner"):
                                airline_name = ac_info["owner"]
                                if airline_name == airline_name.upper():
                                    airline_name = airline_name.title()

                        origin = f.origin_airport_iata or ""
                        destination = f.destination_airport_iata or ""
                        callsign = f.callsign or ""
                        route_source = "fr24_grpc"

                        # FlightStats fallback when gRPC has no route
                        if not origin and not destination and callsign:
                            try:
                                from utilities.flightstats import get_route
                                fs = get_route(callsign)
                                if fs:
                                    origin = fs.get("origin", "")
                                    destination = fs.get("destination", "")
                                    if origin or destination:
                                        route_source = "flightstats"
                                        if fs.get("aircraft") and not plane:
                                            plane = fs["aircraft"]
                            except Exception:
                                pass

                        t = self.safe_get(d, "time", default={})
                        time_sched_dep = self.safe_get(t, "scheduled", "departure")
                        time_sched_arr = self.safe_get(t, "scheduled", "arrival")
                        time_real_dep = self.safe_get(t, "real", "departure")
                        time_est_arr = self.safe_get(t, "estimated", "arrival")

                        # Airport coordinates from local database (no API calls)
                        origin_lat = None
                        origin_lon = None
                        dest_lat = None
                        dest_lon = None

                        if origin:
                            coords = _airport_coords(origin)
                            origin_lat = coords.get("lat")
                            origin_lon = coords.get("lon")
                            if origin_lat is not None:
                                stats["airport_lookups"] += 1
                        if destination:
                            coords = _airport_coords(destination)
                            dest_lat = coords.get("lat")
                            dest_lon = coords.get("lon")
                            if dest_lat is not None:
                                stats["airport_lookups"] += 1

                        # Calculate distances: prefer local airport coords, fallback to flight_progress
                        fp = self.safe_get(d, "flight_progress") or {}
                        traversed_km = (fp.get("traversed_distance", 0) or 0) / 1000.0
                        remaining_km = (fp.get("remaining_distance", 0) or 0) / 1000.0

                        # Use local airport coords for distance if available
                        if origin_lat is not None and origin_lon is not None:
                            dist_o = haversine(f.latitude, f.longitude, origin_lat, origin_lon)
                        elif traversed_km:
                            # Fallback to flight_progress (values are in km)
                            if DISTANCE_UNITS == "metric":
                                dist_o = traversed_km
                            else:
                                dist_o = traversed_km / 1.609344
                        else:
                            dist_o = 0

                        if dest_lat is not None and dest_lon is not None:
                            dist_d = haversine(f.latitude, f.longitude, dest_lat, dest_lon)
                        elif remaining_km:
                            # Fallback to flight_progress (values are in km)
                            if DISTANCE_UNITS == "metric":
                                dist_d = remaining_km
                            else:
                                dist_d = remaining_km / 1.609344
                        else:
                            dist_d = 0

                        # Extract airborne trail points only (alt > 0)
                        raw_trail = self.safe_get(d, "trail", default=[]) or []
                        trail = [
                            [pt["lat"], pt["lng"]]
                            for pt in raw_trail
                            if isinstance(pt, dict) and pt.get("alt", 0) > 0
                        ][-200:]  # cap trail length for memory

                        entry = {
                            "airline": airline_name,
                            "plane": plane,
                            "flight_number": flight_number,
                            "origin": origin,
                            "origin_latitude": origin_lat,
                            "origin_longitude": origin_lon,
                            "destination": destination,
                            "destination_latitude": dest_lat,
                            "destination_longitude": dest_lon,
                            "plane_latitude": f.latitude,
                            "plane_longitude": f.longitude,
                            "owner_iata": f.airline_iata or "N/A",
                            "owner_icao": owner_icao,
                            "time_scheduled_departure": time_sched_dep,
                            "time_scheduled_arrival": time_sched_arr,
                            "time_real_departure": time_real_dep,
                            "time_estimated_arrival": time_est_arr,
                            "altitude": f.altitude or 0,
                            "vertical_speed": f.vertical_speed,
                            "callsign": callsign,
                            "distance_origin": dist_o,
                            "distance_destination": dist_d,
                            "distance": distance_from_flight_to_home(f),
                            "direction": degrees_to_cardinal(plane_bearing(f)),
                            "heading": f.heading if hasattr(f, 'heading') else 0,
                            "trail": trail,
                        }

                        overhead_data.append(entry)
                        stats["flights_processed"] += 1

                        # Track flight details for pipeline summary
                        stats["flight_details"].append({
                            "callsign": callsign,
                            "plane": plane,
                            "origin": origin,
                            "destination": destination,
                            "distance": entry["distance"],
                            "data_source": route_source,
                        })

                        log_flight_data(entry)
                        log_farthest_flight(entry)
                        log_flight_count(callsign, entry)
                        break

                    except Exception as e:
                        retries -= 1
                        if retries == 0:
                            logger.warning(f"Failed to get details for {f.callsign}: {e}")

            # --- STEP 2: Tracked flight (always check; display shows it when clock is up) ---
            tracked_callsign, scheduled_dep, cached_route = load_tracked_callsign()
            if tracked_callsign:
                stats["tracked_callsign"] = tracked_callsign

                # If callsign changed, reset all state — new flight being tracked
                if tracked_callsign != self._tracked_last_callsign:
                    self._tracked_last_callsign = tracked_callsign
                    self._tracked_was_live = False
                    self._tracked_miss_count = 0
                    self._tracked_last_eta = None
                    self._tracked_last_data = None
                    self._tracked_schedule_cache.clear()
                    self._tracked_alt_callsign = ""
                    self._tracked_route_cached = cached_route
                    # Persist set_ts for staleness detection across restarts
                    try:
                        with open(TRACKED_FILE, "r", encoding="utf-8") as f:
                            tf = json.load(f)
                        if "set_ts" not in tf:
                            tf["set_ts"] = int(time())
                            with open(TRACKED_FILE, "w", encoding="utf-8") as f:
                                json.dump(tf, f)
                            os.chmod(TRACKED_FILE, 0o666)
                    except Exception:
                        pass

                # Hard staleness guard — if tracked >36h, auto-wipe
                try:
                    with open(TRACKED_FILE, "r", encoding="utf-8") as f:
                        tf_data = json.load(f)
                    set_ts = tf_data.get("set_ts", 0)
                    if set_ts and (time() - set_ts) > self._MAX_TRACKED_HOURS * 3600:
                        logger.info(
                            f"Tracked flight {tracked_callsign} has been tracked "
                            f"for >{self._MAX_TRACKED_HOURS}h — auto-wiping"
                        )
                        self._do_auto_wipe()
                        tracked_callsign = ""
                except Exception:
                    pass

                if not tracked_callsign:
                    # Wiped by staleness guard — skip grab
                    pass
                else:
                    # Departure window guard: don't search FR24 until 30 min before
                    # scheduled departure. Prevents matching an earlier same-day leg.
                    # Concept from c0wsaysmoo/plane-tracker-rgb-pi.
                    _skip_poll = False
                    if scheduled_dep and not self._tracked_was_live:
                        mins_to_dep = (scheduled_dep - time()) / 60
                        if mins_to_dep > 30:
                            logger.info(
                                f"Tracked {tracked_callsign} departs in "
                                f"{mins_to_dep:.0f}m — not polling FR24 yet"
                            )
                            _skip_poll = True
                            # Build SCHEDULED display from cached_route if available
                            if cached_route:
                                sched_cs = tracked_callsign
                                if len(sched_cs) >= 3 and sched_cs[:2] in IATA_TO_ICAO and sched_cs[2:3].isdigit():
                                    icao_pfx = IATA_TO_ICAO.get(sched_cs[:2])
                                    if icao_pfx:
                                        sched_cs = icao_pfx + sched_cs[2:]
                                tracked_data = {
                                    "callsign": sched_cs,
                                    "number": tracked_callsign,
                                    "airline_name": cached_route.get("airline_name", ""),
                                    "is_live": False,
                                    "is_scheduled": True,
                                    "origin": cached_route.get("origin", ""),
                                    "destination": cached_route.get("destination", ""),
                                    "dep_time": cached_route.get("dep_time", ""),
                                    "arr_time": cached_route.get("arr_time", ""),
                                    "schedule_status": "scheduled",
                                    "aircraft_type": cached_route.get("aircraft_type", ""),
                                    "altitude": 0, "ground_speed": 0, "heading": 0,
                                    "vertical_speed": 0, "dist_remaining": None,
                                    "total_distance": None, "time_remaining": None,
                                    "latitude": None, "longitude": None,
                                    "last_seen_ts": 0, "dest_lat": 0, "dest_lon": 0,
                                }

                    if not _skip_poll:
                        # Position-only mode: after first airborne, skip expensive
                        # get_flight_details and use cached route data instead.
                        # Concept from c0wsaysmoo/plane-tracker-rgb-pi.
                        pos_only = self._tracked_was_live and self._tracked_route_cached is not None
                        tracked_data = self._grab_tracked(
                            tracked_callsign, zone_flights=flights,
                            update_position_only=pos_only,
                        )

                if tracked_data and tracked_data.get("is_live"):
                    just_became_live = not self._tracked_was_live
                    self._tracked_was_live = True
                    self._tracked_miss_count = 0

                    if just_became_live:
                        # First airborne detection — cache route data.
                        # Concept from c0wsaysmoo/plane-tracker-rgb-pi.
                        new_route = {
                            k: tracked_data.get(k)
                            for k in ("origin", "destination", "dest_lat", "dest_lon",
                                      "aircraft_type", "airline_name", "number",
                                      "total_distance", "callsign")
                        }
                        # Merge coords from cached_route if FR24 didn't provide them
                        if cached_route:
                            for k in ("origin_lat", "origin_lon", "dest_lat", "dest_lon",
                                      "time_scheduled_departure", "time_scheduled_arrival"):
                                if not new_route.get(k):
                                    new_route[k] = cached_route.get(k)

                        # Plausibility check: verify plane position is consistent
                        # with the route (within 1.25x great circle distance).
                        # Concept from c0wsaysmoo/plane-tracker-rgb-pi.
                        _plausible = True
                        o_lat = new_route.get("origin_lat")
                        o_lon = new_route.get("origin_lon")
                        d_lat = new_route.get("dest_lat")
                        d_lon = new_route.get("dest_lon")
                        p_lat = tracked_data.get("latitude")
                        p_lon = tracked_data.get("longitude")
                        if o_lat and o_lon and d_lat and d_lon and p_lat and p_lon:
                            # Ratio (to_o + to_d) / total is unit-independent
                            total = haversine(o_lat, o_lon, d_lat, d_lon)
                            to_o = haversine(p_lat, p_lon, o_lat, o_lon)
                            to_d = haversine(p_lat, p_lon, d_lat, d_lon)
                            if total > 0 and (to_o + to_d) > total * 1.25:
                                _plausible = False
                                logger.warning(
                                    f"Tracked flight position implausible for "
                                    f"{new_route.get('origin')}→{new_route.get('destination')} "
                                    f"— keeping cached route"
                                )

                        if _plausible:
                            self._tracked_route_cached = new_route
                        elif cached_route and not self._tracked_route_cached:
                            # FR24 route implausible, use cached route from search time
                            self._tracked_route_cached = cached_route

                        logger.info(f"Tracked flight {tracked_callsign} airborne — route cached")
                    elif self._tracked_route_cached:
                        # Subsequent cycles: merge cached route + live position
                        tracked_data = {**self._tracked_route_cached, **tracked_data}
                        # Recalculate dist_remaining from live position + cached dest
                        dest_lat = self._tracked_route_cached.get("dest_lat")
                        dest_lon = self._tracked_route_cached.get("dest_lon")
                        if dest_lat and dest_lon and tracked_data.get("latitude"):
                            tracked_data["dist_remaining"] = haversine(
                                tracked_data["latitude"], tracked_data["longitude"],
                                dest_lat, dest_lon
                            )
                            # Recalculate time_remaining: speed is in knots (from FR24 gRPC)
                            speed = tracked_data.get("ground_speed", 0)
                            if speed and speed > 50 and tracked_data["dist_remaining"]:
                                if DISTANCE_UNITS == "metric":
                                    dist_nm = tracked_data["dist_remaining"] * 0.539957
                                else:
                                    dist_nm = tracked_data["dist_remaining"] * 0.868976
                                hrs = dist_nm / speed
                                mins = int(hrs * 60)
                                if mins > 0:
                                    h, m = divmod(mins, 60)
                                    tracked_data["time_remaining"] = f"{h}:{m:02d}" if h else f"{m}m"

                    # Only update ETA when available — position-only cycles don't
                    # have it, so preserve the value from the first-airborne cycle
                    eta_val = tracked_data.get("time_estimated_arrival")
                    if eta_val is not None:
                        self._tracked_last_eta = eta_val
                    self._tracked_last_data = tracked_data
                elif tracked_data:
                    # SCHEDULED or NOT TRACKABLE — not live, don't set was_live
                    pass
                else:
                    if self._tracked_was_live:
                        # Was live before, now missing
                        now_ts = time()
                        eta    = self._tracked_last_eta

                        if eta is not None:
                            mins_since_eta = (now_ts - eta) / 60
                            if mins_since_eta > 0:
                                # ETA has passed — use miss counter to confirm
                                # before wiping (avoids false wipe on brief API hiccup)
                                self._tracked_miss_count += 1
                                if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                    self._do_auto_wipe()
                                elif self._tracked_last_data:
                                    tracked_data = estimate_stale_data(self._tracked_last_data)
                            else:
                                # ETA still in future — oceanic gap, serve estimated data
                                # Don't reset miss counter (preserve accumulation for post-ETA)
                                if self._tracked_last_data:
                                    tracked_data = estimate_stale_data(self._tracked_last_data)
                        else:
                            # No ETA data — check AirLabs scheduled arrival as reality check
                            sched = self._tracked_schedule_cache.get(tracked_callsign)
                            sched_arr_utc = sched.get("arr_time_utc") if sched else None
                            sched_status = sched.get("status", "") if sched else ""
                            if sched_arr_utc and sched_status != "cancelled":
                                try:
                                    arr_ts = datetime.strptime(sched_arr_utc, "%Y-%m-%d %H:%M").replace(
                                        tzinfo=timezone.utc).timestamp()
                                    if now_ts < arr_ts:
                                        # Scheduled arrival still in future — don't wipe
                                        if self._tracked_last_data:
                                            tracked_data = estimate_stale_data(self._tracked_last_data)
                                    else:
                                        # Past scheduled arrival — use miss counter
                                        self._tracked_miss_count += 1
                                        if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                            self._do_auto_wipe()
                                        elif self._tracked_last_data:
                                            tracked_data = estimate_stale_data(self._tracked_last_data)
                                except (ValueError, TypeError):
                                    # Bad date format — fall through to miss counter
                                    self._tracked_miss_count += 1
                                    if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                        self._do_auto_wipe()
                                    elif self._tracked_last_data:
                                        tracked_data = estimate_stale_data(self._tracked_last_data)
                            else:
                                # No schedule data either — fall back to miss counter
                                self._tracked_miss_count += 1
                                if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                    self._do_auto_wipe()
                                elif self._tracked_last_data:
                                    tracked_data = estimate_stale_data(self._tracked_last_data)
                    else:
                        # Never been live — try AirLabs schedule
                        # Cache successful results; retry on failure (airlabs module has 5-min TTL)
                        sched = self._tracked_schedule_cache.get(tracked_callsign)
                        if sched is None:
                            from utilities.airlabs import get_flight_schedule
                            sched = get_flight_schedule(tracked_callsign)
                            if sched:
                                self._tracked_schedule_cache[tracked_callsign] = sched

                        # Expiry check: if cached arrival time has passed, re-fetch
                        # AirLabs for delay/actual info before deciding to wipe
                        if sched:
                            sched_arr = sched.get("arr_time_utc")
                            if sched_arr:
                                try:
                                    arr_ts = datetime.strptime(
                                        sched_arr, "%Y-%m-%d %H:%M"
                                    ).replace(tzinfo=timezone.utc).timestamp()
                                    if time() > arr_ts + 3600:
                                        # 1h past cached arrival — re-fetch for delay info
                                        from utilities.airlabs import get_flight_schedule
                                        fresh = get_flight_schedule(tracked_callsign)
                                        if fresh:
                                            # Pick best available arrival time (actual > estimated > scheduled)
                                            # Use `or ""` to handle None values from AirLabs JSON nulls
                                            best_arr = (
                                                (fresh.get("arr_actual_utc") or "")
                                                or (fresh.get("arr_estimated_utc") or "")
                                                or (fresh.get("arr_time_utc") or "")
                                            )
                                            if best_arr:
                                                fresh["arr_time_utc"] = best_arr
                                                arr_ts = datetime.strptime(
                                                    best_arr, "%Y-%m-%d %H:%M"
                                                ).replace(tzinfo=timezone.utc).timestamp()
                                            # If best_arr is empty, arr_ts retains the value
                                            # from the original cached schedule above — correct
                                            self._tracked_schedule_cache[tracked_callsign] = fresh
                                            sched = fresh
                                        # arr_ts falls through from the original parse if
                                        # fresh is None or has no arrival times
                                        if time() > arr_ts + 7200:
                                            logger.info(
                                                f"Tracked flight {tracked_callsign} arrival "
                                                f"passed >2h ago (never went live) — auto-wiping"
                                            )
                                            self._do_auto_wipe()
                                            sched = None
                                except (ValueError, TypeError):
                                    pass

                        # Track miss count for NOT TRACKABLE status
                        # Only flag NOT TRACKABLE after scheduled departure has passed
                        # (avoids false flag for flights set hours before departure)
                        self._tracked_miss_count += 1
                        _dep_passed = False
                        if sched:
                            dep_utc = sched.get("dep_time_utc")
                            if dep_utc:
                                try:
                                    dep_ts = datetime.strptime(
                                        dep_utc, "%Y-%m-%d %H:%M"
                                    ).replace(tzinfo=timezone.utc).timestamp()
                                    _dep_passed = time() > dep_ts + 1800  # 30 min after dep
                                except (ValueError, TypeError):
                                    pass

                        if sched:
                            # Convert callsign to ICAO for logo lookup (UA353 → UAL353)
                            sched_cs = tracked_callsign
                            if len(sched_cs) >= 3 and sched_cs[:2] in IATA_TO_ICAO and sched_cs[2:3].isdigit():
                                icao_pfx = IATA_TO_ICAO.get(sched_cs[:2])
                                if icao_pfx:
                                    sched_cs = icao_pfx + sched_cs[2:]
                            tracked_data = {
                                "callsign": sched_cs,
                                "number": sched.get("flight_number", tracked_callsign),
                                "airline_name": "",
                                "is_live": False,
                                "is_scheduled": True,
                                "not_trackable": _dep_passed and self._tracked_miss_count > 20,
                                "origin": sched.get("origin", ""),
                                "destination": sched.get("destination", ""),
                                "dep_time": sched.get("dep_time", ""),
                                "arr_time": sched.get("arr_time", ""),
                                "schedule_status": sched.get("status", ""),
                                "aircraft_type": "",
                                "altitude": 0,
                                "ground_speed": 0,
                                "heading": 0,
                                "vertical_speed": 0,
                                "dist_remaining": None,
                                "total_distance": None,
                                "time_remaining": None,
                                "latitude": None,
                                "longitude": None,
                                "last_seen_ts": 0,
                                "dest_lat": 0,
                                "dest_lon": 0,
                            }

            # Keep schedule cache even after flight goes live — arr_time_utc
            # is used as reality check to prevent premature auto-wipe when
            # a taxiing plane briefly appears then drops from the feed.

            # Update tracked status for pipeline summary
            if tracked_data:
                if tracked_data.get("is_live"):
                    stats["tracked_status"] = "LIVE"
                elif tracked_data.get("not_trackable"):
                    stats["tracked_status"] = "NOT TRACKABLE"
                elif tracked_data.get("is_scheduled"):
                    stats["tracked_status"] = "SCHEDULED"
                else:
                    stats["tracked_status"] = "ESTIMATED (stale)"
            elif stats.get("tracked_callsign"):
                stats["tracked_status"] = "NOT FOUND"
            else:
                stats["tracked_status"] = ""

            # --- ISS pass data (pre-warm cache on background thread) ---
            try:
                from utilities.iss import get_iss_pass_data
                get_iss_pass_data()
            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"ISS pass data fetch failed: {e}")

            # --- Pipeline Summary ---
            stats["elapsed_ms"] = (time() - _grab_start) * 1000
            self._log_pipeline_summary(stats)

            with self._lock:
                self._data = overhead_data
                self._tracked_data = tracked_data
                self._new_data = True

        except (ConnectionError, ConnectError, TimeoutException, OSError) as e:
            logger.warning(f"Overhead: Network error during _grab: {type(e).__name__}: {e}")
            with self._lock:
                self._data = []
                self._tracked_data = None
                self._new_data = True
        except Exception as e:
            logger.error(f"Overhead: Unexpected error in _grab: {type(e).__name__}: {e}", exc_info=True)
            with self._lock:
                self._data = []
                self._tracked_data = None
                self._new_data = True
        finally:
            with self._lock:
                self._processing = False

    def _do_auto_wipe(self):
        """Wipe tracked_flight.json and reset all tracking state."""
        try:
            with open(TRACKED_FILE, "w", encoding="utf-8") as f:
                json.dump({"callsign": "", "set_ts": 0}, f)
            try:
                os.chmod(TRACKED_FILE, 0o666)
            except OSError:
                pass
            logger.info("Tracked flight ended — auto-cleared.")
        except Exception as e:
            logger.error(f"Failed to auto-clear tracked flight: {e}")
        self._tracked_was_live = False
        self._tracked_miss_count = 0
        self._tracked_last_eta = None
        self._tracked_last_data = None
        self._tracked_schedule_cache.clear()
        self._tracked_last_callsign = ""
        self._tracked_alt_callsign = ""
        self._tracked_route_cached = None

    def _grab_tracked(self, flight_input, zone_flights=None, update_position_only=False):
        flight_input = flight_input.strip().upper()

        # Convert IATA format (UA353, B6555) to ICAO (UAL353, JBU555) for gRPC filter
        if len(flight_input) >= 3 and flight_input[:2] in IATA_TO_ICAO and flight_input[2:3].isdigit():
            flight_input = IATA_TO_ICAO[flight_input[:2]] + flight_input[2:]

        match = None

        try:
            # Strategy 0: check zone flights already fetched (no extra API call)
            if zone_flights:
                match = next(
                    (f for f in zone_flights if (f.callsign or "").upper() == flight_input),
                    None,
                )

            # Strategy 1: server-side callsign filter (searches FR24's full worldwide feed)
            if not match:
                match = self._api.find_by_callsign(flight_input)

            # Strategy 2: try cached alt callsign from previous regional lookup
            if not match and self._tracked_alt_callsign:
                match = self._api.find_by_callsign(self._tracked_alt_callsign)

            # Strategy 3: try operating carrier prefix + same flight number
            # (e.g., AAL4728 → RPA4728). Works when marketing and operating
            # flight numbers match (most common case for US regionals).
            if not match:
                sched = self._tracked_schedule_cache.get(flight_input)
                if not sched:
                    for k in self._tracked_schedule_cache:
                        sched = self._tracked_schedule_cache[k]
                        break
                cs_airline = (sched.get("cs_airline_iata", "") if sched else "")
                cs_icao = IATA_TO_ICAO.get(cs_airline, "")
                if cs_icao:
                    flight_num = flight_input.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                    alt_cs = cs_icao + flight_num
                    if alt_cs != flight_input:
                        match = self._api.find_by_callsign(alt_cs)
                        if match:
                            self._tracked_alt_callsign = alt_cs
                            logger.info(f"Found tracked flight via prefix swap: {alt_cs}")

            # Strategy 4: route-based search for regional/codeshare flights
            # Fallback when prefix swap fails (operating carrier uses different
            # flight numbers) — searches by route and filters by carrier prefix.
            if not match:
                sched = self._tracked_schedule_cache.get(flight_input)
                if not sched:
                    for k in self._tracked_schedule_cache:
                        sched = self._tracked_schedule_cache[k]
                        break
                if sched:
                    origin = sched.get("origin", "")
                    dest = sched.get("destination", "")
                    cs_airline = sched.get("cs_airline_iata", "")  # e.g., YX (Republic)
                    # Convert operating carrier IATA to ICAO prefix for callsign matching
                    cs_icao = IATA_TO_ICAO.get(cs_airline, "")
                    if origin and dest and (cs_icao or cs_airline):
                        route_flights = self._api.find_by_route(origin, dest)
                        if route_flights:
                            # Filter to operating carrier prefix
                            prefix = cs_icao or cs_airline
                            candidates = [
                                f for f in route_flights
                                if (f.callsign or "").upper().startswith(prefix)
                            ]
                            if not candidates:
                                # Fallback: try all known regional prefixes
                                icao_pfx = flight_input.rstrip("0123456789")
                                reg_prefixes = REGIONAL_OPERATORS.get(icao_pfx, [])
                                candidates = [
                                    f for f in route_flights
                                    if any((f.callsign or "").upper().startswith(rp)
                                           for rp in reg_prefixes)
                                ]
                            if len(candidates) == 1:
                                match = candidates[0]
                            elif len(candidates) > 1:
                                # Multiple matches — pick closest to scheduled departure
                                dep_ts = sched.get("dep_time_ts")
                                if dep_ts:
                                    # Pick flight with latest departure (most recently departed)
                                    candidates.sort(
                                        key=lambda f: abs(
                                            (getattr(f, 'time', None) or 0) - dep_ts
                                        )
                                    )
                                match = candidates[0]
                            if match:
                                self._tracked_alt_callsign = (match.callsign or "").upper()
                                logger.info(
                                    f"Found tracked flight via route search "
                                    f"{origin}→{dest}: {match.callsign}"
                                )

            if not match:
                return None

            # Position-only mode: skip expensive get_flight_details, return
            # just live position data. Route info comes from cached_route.
            # Concept from c0wsaysmoo/plane-tracker-rgb-pi (just_became_live pattern).
            if update_position_only:
                return {
                    "callsign": (match.callsign or "").upper(),
                    "is_live": True,
                    "altitude": match.altitude or 0,
                    "ground_speed": match.ground_speed or 0,
                    "heading": match.heading or 0,
                    "vertical_speed": match.vertical_speed,
                    "latitude": match.latitude,
                    "longitude": match.longitude,
                    "last_seen_ts": time(),
                }

            flight_details = self._api.get_flight_details(match)
            match.set_flight_details(flight_details)

            # Use flight_progress from the API for distances (values are in KM)
            fp = self.safe_get(flight_details, "flight_progress") or {}
            remaining_km = fp.get("remaining_distance", 0) or 0
            total_km = fp.get("great_circle_distance", 0) or 0
            eta = fp.get("eta", 0) or 0

            # Look up airport coordinates from local database for distance calculations
            origin_code = match.origin_airport_iata or ""
            dest_code = match.destination_airport_iata or ""
            dest_coords = _airport_coords(dest_code)
            origin_coords = _airport_coords(origin_code)

            dest_lat = dest_coords.get("lat")
            dest_lon = dest_coords.get("lon")

            # Calculate distance remaining: prefer local airport coords
            if dest_lat is not None and dest_lon is not None:
                dist_remaining = haversine(match.latitude, match.longitude, dest_lat, dest_lon)
            elif remaining_km:
                # Fallback to flight_progress (values are in km)
                if DISTANCE_UNITS == "metric":
                    dist_remaining = remaining_km
                else:
                    dist_remaining = remaining_km / 1.609344
            else:
                dist_remaining = None

            # Total distance: prefer local airport coords
            origin_lat = origin_coords.get("lat")
            origin_lon = origin_coords.get("lon")
            if (origin_lat is not None and origin_lon is not None
                    and dest_lat is not None and dest_lon is not None):
                total_distance = haversine(origin_lat, origin_lon, dest_lat, dest_lon)
            elif total_km:
                # Fallback to flight_progress (values are in km)
                if DISTANCE_UNITS == "metric":
                    total_distance = total_km
                else:
                    total_distance = total_km / 1.609344
            else:
                total_distance = None

            # Calculate time remaining from ETA
            time_remaining = None
            if eta and eta > time():
                mins_left = int((eta - time()) / 60)
                if mins_left > 0:
                    h = mins_left // 60
                    m = mins_left % 60
                    time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"
            # Fallback: use remaining_time from flight_progress (seconds)
            if not time_remaining:
                remaining_secs = fp.get("remaining_time", 0) or 0
                if remaining_secs > 0:
                    mins_left = remaining_secs // 60
                    if mins_left > 0:
                        h = mins_left // 60
                        m = mins_left % 60
                        time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"
            # Last fallback: 3-phase ETA (climb/cruise/descent model)
            if not time_remaining and dist_remaining and match.ground_speed:
                if DISTANCE_UNITS == "metric":
                    dist_nm = dist_remaining * 0.539957
                else:
                    dist_nm = dist_remaining * 0.868976
                mins_left = _estimate_eta_3phase(
                    match.altitude or 0,
                    match.vertical_speed or 0,
                    match.ground_speed,
                    dist_nm,
                )
                if mins_left and mins_left > 0:
                    mins_left = int(mins_left)
                    h = mins_left // 60
                    m = mins_left % 60
                    time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"

            # Time fields for delay colour coding
            time_details = self.safe_get(flight_details, "time") or {}
            time_sched_dep = self.safe_get(time_details, "scheduled", "departure")
            time_sched_arr = self.safe_get(time_details, "scheduled", "arrival")
            time_real_dep  = self.safe_get(time_details, "real", "departure")
            time_est_arr   = self.safe_get(time_details, "estimated", "arrival") or eta or None

            # Airline name: try local database, then FR24
            airline_name = match.airline_name or ""
            if not airline_name:
                airline_icao_code = match.airline_icao or ""
                airline_name = _airline_name_lookup(airline_icao_code)

            # GA owner lookup for N-number aircraft
            if (not airline_name and match.registration
                    and match.registration.startswith("N")
                    and match.registration[1:2].isdigit()):
                ac_info = _adsbdb_aircraft(match.registration)
                if ac_info.get("owner"):
                    airline_name = ac_info["owner"]
                    if airline_name == airline_name.upper():
                        airline_name = airline_name.title()

            return {
                "callsign": flight_input,
                "number": match.number or flight_input,
                "airline_name": airline_name,
                "is_live": True,
                "origin": match.origin_airport_iata or "",
                "destination": match.destination_airport_iata or "",
                "dest_lat": dest_lat or 0,
                "dest_lon": dest_lon or 0,
                "aircraft_type": match.aircraft_code or "",
                "altitude": match.altitude or 0,
                "ground_speed": match.ground_speed or 0,
                "heading": match.heading or 0,
                "dist_remaining": dist_remaining,
                "total_distance": total_distance,
                "time_remaining": time_remaining,
                "latitude": match.latitude,
                "longitude": match.longitude,
                "last_seen_ts": time(),
                "vertical_speed": match.vertical_speed or 0,
                "time_scheduled_departure": time_sched_dep,
                "time_scheduled_arrival": time_sched_arr,
                "time_real_departure": time_real_dep,
                "time_estimated_arrival": time_est_arr,
            }

        except Exception as e:
            logger.error(f"Failed to grab tracked flight: {e}")
            return None

    # Properties

    @property
    def new_data(self):
        with self._lock:
            return self._new_data

    @property
    def processing(self):
        with self._lock:
            return self._processing

    @property
    def data(self):
        with self._lock:
            self._new_data = False
            return self._data

    @property
    def tracked_data(self):
        with self._lock:
            return self._tracked_data

    @property
    def data_is_empty(self):
        # FIX: Acquire lock to be consistent with all other properties
        with self._lock:
            return len(self._data) == 0

    @property
    def iss_pass_data(self):
        """Return ISS pass data if a pass is active, else None.

        The background _grab thread pre-warms the iss module's internal cache.
        This property calls get_iss_pass_data() which returns cached results
        without blocking the render thread.
        """
        try:
            from utilities.iss import get_iss_pass_data
            return get_iss_pass_data()
        except ImportError:
            return None


if __name__ == "__main__":
    from time import sleep
    o = Overhead()
    o.grab_data()
    while not o.new_data:
        print("processing...")
        sleep(1)
    print("Overhead:", o.data)
    print("Tracked:", o.tracked_data)
