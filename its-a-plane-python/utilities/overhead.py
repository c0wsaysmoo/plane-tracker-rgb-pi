import os
import json
import math
import requests
from time import sleep, time
from threading import Thread, Lock

from config import (
    DISTANCE_UNITS,
    CLOCK_FORMAT,
    MAX_FARTHEST,
    MAX_CLOSEST,
)

from setup import email_alerts
from web import map_generator, upload_helper

# Optional config values
try:
    from config import MIN_ALTITUDE
try:
    from config import ZONE_HOME, LOCATION_HOME
    ZONE_DEFAULT = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME
# Optional: explicit search radius in nautical miles (overrides zone-based calculation)
try:
    from config import SEARCH_RADIUS_NM
# Constants
RETRIES = 3
RATE_LIMIT_DELAY = 0.5
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 100000
EARTH_RADIUS_M = 3958.8
BLANK_FIELDS = ["", "N/A", "NONE"]

ADSB_LOL_BASE = "https://api.adsb.lol"
ADSBDB_BASE = "https://api.adsbdb.com"
AIRLABS_BASE = "https://airlabs.co/api/v9"

# ICAO prefixes for airlines that reuse callsigns so heavily that adsbdb
# route data is unreliable — skip straight to AirLabs/FlightAware fallback
# Optional FlightAware AeroAPI key for route fallback (GA, charter, non-standard callsigns)
try:
    from config import FLIGHTAWARE_API_KEY
# Monthly spending limit for FlightAware (dollars). Default $4 to stay under $5 free credit.
try:
    from config import FLIGHTAWARE_MONTHLY_LIMIT
FLIGHTAWARE_BASE = "https://aeroapi.flightaware.com/aeroapi"
FLIGHTAWARE_COST_PER_CALL = 0.01  # dollars per result set (verified from billing API)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LOG_FILE = os.path.join(BASE_DIR, "close.txt")
LOG_FILE_FARTHEST = os.path.join(BASE_DIR, "farthest.txt")
TRACKED_FILE = os.path.join(BASE_DIR, "tracked_flight.json")
FA_USAGE_FILE = os.path.join(BASE_DIR, "flightaware_usage.json")



def _compute_search_radius():
    """Compute search radius in NM from ZONE_HOME bounding box, or use explicit config."""
    if SEARCH_RADIUS_NM is not None:
        return SEARCH_RADIUS_NM
    # Approximate radius from bounding box diagonal / 2
    lat1, lon1 = ZONE_DEFAULT["tl_y"], ZONE_DEFAULT["tl_x"]
    lat2, lon2 = ZONE_DEFAULT["br_y"], ZONE_DEFAULT["br_x"]
    diag_miles = haversine(lat1, lon1, lat2, lon2)
    # Convert to nautical miles
    if DISTANCE_UNITS == "metric":
        diag_nm = diag_miles * 0.539957  # km to nm
    else:
        diag_nm = diag_miles * 0.868976  # miles to nm
    return max(10, diag_nm / 2)


# Utility Functions

def safe_load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def safe_write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def ordinal(n: int):
    return f"{n}{'tsnrhtdd'[(n//10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"


def haversine(lat1, lon1, lat2, lon2):
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
    return miles * 1.609 if DISTANCE_UNITS == "metric" else miles


def estimate_stale_data(last_data):
    data = dict(last_data)
    data["is_live"] = False

    speed_kts = data.get("ground_speed", 0)
    last_ts   = data.get("last_seen_ts")

    if not last_ts:
        return data

    elapsed_hrs  = (time() - last_ts) / 3600
    elapsed_mins = elapsed_hrs * 60

    last_time_str = data.get("time_remaining", "")
    if last_time_str:
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

    last_dist = data.get("dist_remaining")
    if last_dist is not None and speed_kts > 0:
        if DISTANCE_UNITS == "metric":
            speed_display = speed_kts * 1.852
        else:
            speed_display = speed_kts * 1.15078
        dist_covered = speed_display * elapsed_hrs
        data["dist_remaining"] = max(0, last_dist - dist_covered)

    return data


def degrees_to_cardinal(deg):
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) / 45)
    return dirs[idx % 8]


def bearing_from_home(lat, lon, home=LOCATION_DEFAULT):
    lat1, lon1 = map(math.radians, home)
    lat2, lon2 = map(math.radians, (lat, lon))
    b = math.atan2(
        math.sin(lon2 - lon1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    )
    return (math.degrees(b) + 360) % 360


def load_tracked_callsign():
    """Read the tracked callsign from tracked_flight.json."""
    try:
        with open(TRACKED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("callsign", "").strip().upper()
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


# --- API helpers ---

def _adsb_lol_nearby(lat, lon, radius_nm):
    """Fetch aircraft near a point from adsb.lol. Returns list of dicts."""
    url = f"{ADSB_LOL_BASE}/v2/lat/{lat}/lon/{lon}/dist/{int(radius_nm)}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get("ac", [])
    except Exception as e:
        print(f"adsb.lol nearby error: {e}")
        return []


def _adsb_lol_callsign(callsign):
    """Fetch aircraft by callsign from adsb.lol. Returns list of dicts."""
    url = f"{ADSB_LOL_BASE}/v2/callsign/{callsign}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get("ac", [])
    except Exception as e:
        print(f"adsb.lol callsign error: {e}")
        return []


def _adsbdb_route(callsign):
    """Fetch route info (airline, origin, destination) from adsbdb.com.
    Returns dict with keys: airline_name, airline_icao, airline_iata,
    origin_iata, origin_lat, origin_lon, dest_iata, dest_lat, dest_lon.
    Returns empty dict if not found.
    """
    url = f"{ADSBDB_BASE}/v0/callsign/{callsign}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        data = r.json()
        fr = data.get("response", {}).get("flightroute")
        if not fr:
            return {}

        airline = fr.get("airline") or {}
        origin = fr.get("origin") or {}
        dest = fr.get("destination") or {}

        return {
            "airline_name": airline.get("name", ""),
            "airline_icao": airline.get("icao", ""),
            "airline_iata": airline.get("iata", ""),
            "origin_iata": origin.get("iata_code", ""),
            "origin_lat": origin.get("latitude"),
            "origin_lon": origin.get("longitude"),
            "dest_iata": dest.get("iata_code", ""),
            "dest_lat": dest.get("latitude"),
            "dest_lon": dest.get("longitude"),
        }
    except Exception as e:
        print(f"adsbdb route error for {callsign}: {e}")
        return {}


def _adsbdb_aircraft(registration):
    """Fetch aircraft info by registration (N-number) from adsbdb.com.
    Returns dict with owner, type, manufacturer, or empty dict."""
    url = f"{ADSBDB_BASE}/v0/aircraft/{registration}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        ac = r.json().get("response", {}).get("aircraft")
        if not ac:
            return {}
        return {
            "owner": ac.get("registered_owner", ""),
            "type": ac.get("icao_type", ""),
            "manufacturer": ac.get("manufacturer", ""),
            "registration": ac.get("registration", ""),
        }
    except Exception as e:
        print(f"adsbdb aircraft error for {registration}: {e}")
        return {}



def _route_makes_sense(plane_lat, plane_lon, origin_lat, origin_lon, dest_lat, dest_lon):
    """Check if a route is geographically plausible for the aircraft's position.
    Returns True if the plane is reasonably close to the great circle path
    between origin and destination. Returns True if we can't determine (missing coords)."""
    if not all((plane_lat, plane_lon, origin_lat, origin_lon, dest_lat, dest_lon)):
        return True  # Can't check, assume OK

    route_dist = haversine(origin_lat, origin_lon, dest_lat, dest_lon)
    if route_dist < 50:
        return True  # Short route, hard to validate

    dist_to_origin = haversine(plane_lat, plane_lon, origin_lat, origin_lon)
    dist_to_dest = haversine(plane_lat, plane_lon, dest_lat, dest_lon)

    # The sum of distances from plane to origin + plane to dest should be
    # roughly equal to the route distance (with some tolerance for curved paths).
    # If it's way more than the route distance, the plane isn't on this route.
    detour_ratio = (dist_to_origin + dist_to_dest) / route_dist
    return detour_ratio < 1.8


def _airlabs_route(callsign):
    """Fetch route info from AirLabs as fallback. Requires AIRLABS_API_KEY.
    Returns dict in same format as _adsbdb_route, or empty dict."""
    if not AIRLABS_API_KEY:
        return {}

    # Convert ICAO callsign to flight_icao parameter (e.g. SWA1444)
    url = f"{AIRLABS_BASE}/flight"
    params = {"flight_icao": callsign, "api_key": AIRLABS_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json().get("response") or {}
        if not data:
            return {}

        return {
            "airline_name": data.get("airline_name", ""),
            "airline_icao": data.get("airline_icao", ""),
            "airline_iata": data.get("airline_iata", ""),
            "origin_iata": data.get("dep_iata", ""),
            "origin_lat": data.get("dep_lat"),
            "origin_lon": data.get("dep_lng"),
            "dest_iata": data.get("arr_iata", ""),
            "dest_lat": data.get("arr_lat"),
            "dest_lon": data.get("arr_lng"),
        }
    except Exception as e:
        print(f"AirLabs route error for {callsign}: {e}")
        return {}


def _airport_coords(code):
    """Look up airport coordinates from adsb.lol by ICAO code.
    Accepts ICAO (KJFK, EGLL) or IATA (JFK, LHR) — tries ICAO first,
    then prepends 'K' for US 3-letter codes. Returns {lat, lon} or empty dict."""
    candidates = [code]
    if len(code) == 3:
        candidates.insert(0, "K" + code)  # Try US ICAO first (KJFK)
    for c in candidates:
        try:
            r = requests.get(f"{ADSB_LOL_BASE}/api/0/airport/{c}", timeout=5)
            if r.status_code == 200:
                d = r.json()
                if isinstance(d, dict) and d.get("lat") and d.get("lon"):
                    return {"lat": d["lat"], "lon": d["lon"]}
        except Exception:
            pass
    return {}


# --- FlightAware AeroAPI route fallback ---

_fa_cache = {}  # {callsign: {"data": dict, "ts": float}}
FA_CACHE_TTL = 3600  # 1 hour


def _fa_load_usage():
    """Load FlightAware usage tracking. Resets monthly."""
    from datetime import datetime
    try:
        with open(FA_USAGE_FILE, "r", encoding="utf-8") as f:
            usage = json.load(f)
        # Reset if month changed
        if usage.get("month") != datetime.now().strftime("%Y-%m"):
            return {"month": datetime.now().strftime("%Y-%m"), "calls": 0, "cost": 0.0}
        return usage
    except (FileNotFoundError, json.JSONDecodeError):
        return {"month": datetime.now().strftime("%Y-%m"), "calls": 0, "cost": 0.0}


def _fa_save_usage(usage):
    try:
        safe_write_json(FA_USAGE_FILE, usage)
    except Exception as e:
        print(f"Failed to save FA usage: {e}")


def _flightaware_route(callsign):
    """Fetch route from FlightAware AeroAPI (last resort fallback).
    Works for GA, charter, ferry flights, and non-standard callsigns.
    Returns dict in same format as _adsbdb_route, or empty dict.
    Respects monthly budget limit and 1-hour cache."""
    if not FLIGHTAWARE_API_KEY:
        return {}

    # Check cache (1 hour TTL)
    now = time()
    cached = _fa_cache.get(callsign)
    if cached and (now - cached["ts"]) < FA_CACHE_TTL:
        return cached["data"]

    # Check budget
    usage = _fa_load_usage()
    if usage["cost"] >= FLIGHTAWARE_MONTHLY_LIMIT:
        return {}

    try:
        r = requests.get(
            f"{FLIGHTAWARE_BASE}/flights/{callsign}",
            headers={"x-apikey": FLIGHTAWARE_API_KEY},
            params={"max_pages": 1},
            timeout=10,
        )
        if r.status_code != 200:
            return {}

        flights = r.json().get("flights", [])

        # Track the API call cost
        usage["calls"] += 1
        usage["cost"] += FLIGHTAWARE_COST_PER_CALL
        _fa_save_usage(usage)

        if not flights:
            _fa_cache[callsign] = {"data": {}, "ts": now}
            return {}

        # Prefer an en route flight with both origin and destination
        f = next(
            (fl for fl in flights
             if fl.get("status") == "En Route"
             and fl.get("origin") and fl.get("destination")),
            # Fall back to any flight with both endpoints
            next(
                (fl for fl in flights
                 if fl.get("origin") and fl.get("destination")),
                flights[0]
            )
        )
        origin = f.get("origin") or {}
        dest = f.get("destination") or {}

        origin_lat = origin.get("latitude")
        origin_lon = origin.get("longitude")
        dest_lat = dest.get("latitude")
        dest_lon = dest.get("longitude")

        # FA sometimes returns airport codes without coordinates —
        # look them up from adsb.lol if missing
        if origin.get("code_icao") and not origin_lat:
            coords = _airport_coords(origin["code_icao"])
            origin_lat = coords.get("lat")
            origin_lon = coords.get("lon")
        if dest.get("code_icao") and not dest_lat:
            coords = _airport_coords(dest["code_icao"])
            dest_lat = coords.get("lat")
            dest_lon = coords.get("lon")

        result = {
            "airline_name": f.get("operator", ""),
            "airline_icao": f.get("operator_icao", ""),
            "airline_iata": f.get("operator_iata", ""),
            "origin_iata": origin.get("code_iata", ""),
            "origin_lat": origin_lat,
            "origin_lon": origin_lon,
            "dest_iata": dest.get("code_iata", ""),
            "dest_lat": dest_lat,
            "dest_lon": dest_lon,
        }

        _fa_cache[callsign] = {"data": result, "ts": now}
        return result

    except Exception as e:
        print(f"FlightAware error for {callsign}: {e}")
        return {}


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
            html = map_generator.generate_closest_map(top_n, filename="closest.html")
            url = upload_helper.upload_map_to_server(html)
            subject = f"New {ordinal(rank)} Closest Flight - {entry.get('callsign','Unknown')}"
            email_alerts.send_flight_summary(subject, entry, map_url=url)

    except Exception as e:
        print("Failed to log closest flight:", e)


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

        if notify or updated:
            html = map_generator.generate_farthest_map(lst, filename="farthest.html")

        if notify:
            url = upload_helper.upload_map_to_server(html)
            rank = next(i for i, f in enumerate(lst) if f["_airport"] == airport) + 1
            cs = entry.get("callsign", "UNKNOWN")
            if rank == 1:
                subject = f"New Farthest Flight ({reason}) - {cs}"
            else:
                subject = f"{ordinal(rank)}-Farthest Flight ({reason}) - {cs}"
            email_alerts.send_flight_summary(subject, entry, reason, map_url=url)

    except Exception as e:
        import traceback
        print("Failed to log farthest flight:", e)
        traceback.print_exc()


# Overhead Class

class Overhead:
    def __init__(self):
        self._lock = Lock()
        self._data = []           # overhead flights
        self._tracked_data = None # tracked flight or None
        self._new_data = False
        self._processing = False
        self._tracked_was_live = False
        self._tracked_miss_count = 0
        self._TRACKED_MISS_THRESHOLD = 3
        self._tracked_last_callsign = ""
        self._tracked_last_eta = None
        self._tracked_last_data = None
        self._search_radius = _compute_search_radius()

    def grab_data(self):
        Thread(target=self._grab, daemon=True).start()

    def _grab(self):
        with self._lock:
            self._new_data = False
            self._processing = True

        overhead_data = []
        tracked_data = None

        try:
            # --- STEP 1: Check zone for overhead flights via adsb.lol ---
            home_lat, home_lon = LOCATION_DEFAULT
            aircraft = _adsb_lol_nearby(home_lat, home_lon, self._search_radius)

            # Filter by altitude (adsb.lol returns alt_baro in feet)
            aircraft = [
                ac for ac in aircraft
                if isinstance(ac.get("alt_baro"), (int, float))
                and MIN_ALTITUDE < ac["alt_baro"] < MAX_ALTITUDE
            ]

            # Sort by distance from home and take closest N
            for ac in aircraft:
                ac["_dist"] = haversine(
                    ac.get("lat", 0), ac.get("lon", 0),
                    home_lat, home_lon,
                )
            aircraft.sort(key=lambda ac: ac["_dist"])
            aircraft = aircraft[:MAX_FLIGHT_LOOKUP]

            # Enrich each with route data from adsbdb
            for ac in aircraft:
                callsign = (ac.get("flight") or "").strip()
                if not callsign:
                    continue

                sleep(RATE_LIMIT_DELAY)

                try:
                    plane_lat = ac.get("lat", 0)
                    plane_lon = ac.get("lon", 0)
                    registration = ac.get("r", "")

                    # Step 1: Try adsbdb for route (free, unlimited)
                    route = _adsbdb_route(callsign)

                    origin_lat = route.get("origin_lat")
                    origin_lon = route.get("origin_lon")
                    dest_lat = route.get("dest_lat")
                    dest_lon = route.get("dest_lon")

                    need_fallback = False
                    if not route:
                        need_fallback = True
                    elif not _route_makes_sense(
                        plane_lat, plane_lon,
                        origin_lat, origin_lon, dest_lat, dest_lon
                    ):
                        print(f"  Route {route.get('origin_iata','?')}->{route.get('dest_iata','?')} "
                              f"implausible for {callsign}")
                        need_fallback = True

                    # Step 2: Fallback chain — AirLabs (free) then FlightAware (paid)
                    if need_fallback:
                        fallback = _airlabs_route(callsign)
                        if fallback:
                        else:
                            fallback = _flightaware_route(callsign)
                            if fallback:
                        if fallback:
                            route = fallback
                            origin_lat = route.get("origin_lat")
                            origin_lon = route.get("origin_lon")
                            dest_lat = route.get("dest_lat")
                            dest_lon = route.get("dest_lon")
                        else:
                            route = {}
                            origin_lat = origin_lon = dest_lat = dest_lon = None

                    airline = route.get("airline_name", "")
                    origin = route.get("origin_iata", "")
                    destination = route.get("dest_iata", "")

                    # Step 3: If we have airport codes but missing coordinates, look them up
                    if origin and not (origin_lat and origin_lon):
                        coords = _airport_coords(origin)
                        origin_lat = coords.get("lat")
                        origin_lon = coords.get("lon")
                    if destination and not (dest_lat and dest_lon):
                        coords = _airport_coords(destination)
                        dest_lat = coords.get("lat")
                        dest_lon = coords.get("lon")

                    # Step 4: If no airline name, look up aircraft owner by registration
                    if not airline and registration:
                        ac_info = _adsbdb_aircraft(registration)
                        if ac_info.get("owner"):
                            airline = ac_info["owner"]

                    # Audit log

                    dist_o = haversine(plane_lat, plane_lon, origin_lat, origin_lon) if (origin_lat and origin_lon) else 0
                    dist_d = haversine(plane_lat, plane_lon, dest_lat, dest_lon) if (dest_lat and dest_lon) else 0

                    # Aircraft type from adsb.lol 't' field
                    plane_type = ac.get("t", "")

                    # ICAO airline code: from adsbdb or derive from callsign prefix
                    owner_icao = route.get("airline_icao", "")
                    if not owner_icao and len(callsign) >= 3 and callsign[:3].isalpha():
                        owner_icao = callsign[:3]

                    owner_iata = route.get("airline_iata", "") or "N/A"

                    entry = {
                        "airline": airline,
                        "plane": plane_type,
                        "origin": origin,
                        "origin_latitude": origin_lat,
                        "origin_longitude": origin_lon,
                        "destination": destination,
                        "destination_latitude": dest_lat,
                        "destination_longitude": dest_lon,
                        "plane_latitude": plane_lat,
                        "plane_longitude": plane_lon,
                        "owner_iata": owner_iata,
                        "owner_icao": owner_icao,
                        "time_scheduled_departure": None,
                        "time_scheduled_arrival": None,
                        "time_real_departure": None,
                        "time_estimated_arrival": None,
                        "vertical_speed": ac.get("baro_rate", 0) or 0,
                        "callsign": callsign,
                        "distance_origin": dist_o,
                        "distance_destination": dist_d,
                        "distance": ac["_dist"],
                        "direction": degrees_to_cardinal(
                            bearing_from_home(plane_lat, plane_lon)
                        ),
                        "trail": [],
                    }

                    overhead_data.append(entry)
                    log_flight_data(entry)
                    log_farthest_flight(entry)

                except Exception as e:
                    print(f"Error enriching flight {callsign}: {e}")

            # --- STEP 2: Only look for tracked flight if sky is clear ---
            if not overhead_data:
                tracked_callsign = load_tracked_callsign()
                if tracked_callsign:

                    if tracked_callsign != self._tracked_last_callsign:
                        self._tracked_last_callsign = tracked_callsign
                        self._tracked_was_live = False
                        self._tracked_miss_count = 0
                        self._tracked_last_eta = None
                        self._tracked_last_data = None

                    tracked_data = self._grab_tracked(tracked_callsign)

                    if tracked_data:
                        self._tracked_was_live = True
                        self._tracked_miss_count = 0
                        self._tracked_last_eta = tracked_data.get("time_estimated_arrival")
                        self._tracked_last_data = tracked_data
                    else:
                        if self._tracked_was_live:
                            now_ts = time()
                            eta    = self._tracked_last_eta

                            if eta is not None:
                                mins_since_eta = (now_ts - eta) / 60
                                if mins_since_eta > 0:
                                    self._tracked_miss_count += 1
                                    if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                        self._do_auto_wipe()
                                    elif self._tracked_last_data:
                                        tracked_data = estimate_stale_data(self._tracked_last_data)
                                else:
                                    self._tracked_miss_count = 0
                                    if self._tracked_last_data:
                                        tracked_data = estimate_stale_data(self._tracked_last_data)
                            else:
                                self._tracked_miss_count += 1
                                if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                    self._do_auto_wipe()
                                elif self._tracked_last_data:
                                    tracked_data = estimate_stale_data(self._tracked_last_data)

            with self._lock:
                self._data = overhead_data
                self._tracked_data = tracked_data
                self._new_data = True
                self._processing = False

        except Exception as e:
            print(f"Error in _grab: {e}")
            with self._lock:
                self._new_data = False
                self._processing = False

    def _do_auto_wipe(self):
        """Wipe tracked_flight.json and reset all tracking state."""
        try:
            with open(TRACKED_FILE, "w", encoding="utf-8") as f:
                json.dump({"callsign": ""}, f)
            print("Tracked flight ended — auto-cleared.")
        except Exception as e:
            print(f"Failed to auto-clear tracked flight: {e}")
        self._tracked_was_live = False
        self._tracked_miss_count = 0
        self._tracked_last_eta = None
        self._tracked_last_data = None
        self._tracked_last_callsign = ""

    def _grab_tracked(self, flight_input):
        """Look up a specific tracked flight by callsign using adsb.lol + adsbdb."""
        flight_input = flight_input.strip().upper()

        try:
            # Search adsb.lol by callsign
            results = _adsb_lol_callsign(flight_input)

            # If not found by exact callsign, the flight may not be airborne yet
            if not results:
                return None

            ac = results[0]
            plane_lat = ac.get("lat")
            plane_lon = ac.get("lon")

            # Fall back to lastPosition if current position unavailable
            # (common over oceans or when ADS-B signal is intermittent)
            if plane_lat is None or plane_lon is None:
                last_pos = ac.get("lastPosition") or {}
                plane_lat = last_pos.get("lat")
                plane_lon = last_pos.get("lon")

            has_position = plane_lat is not None and plane_lon is not None

            # Get route info from adsbdb
            sleep(RATE_LIMIT_DELAY)
            route = _adsbdb_route(flight_input)

            origin = route.get("origin_iata", "")
            destination = route.get("dest_iata", "")
            dest_lat = route.get("dest_lat")
            dest_lon = route.get("dest_lon")
            origin_lat = route.get("origin_lat")
            origin_lon = route.get("origin_lon")

            # Distance remaining to destination
            dist_remaining = None
            if has_position and dest_lat and dest_lon:
                dist_remaining = haversine(plane_lat, plane_lon, dest_lat, dest_lon)

            # Time remaining estimate from distance and ground speed
            time_remaining = None
            ground_speed_kts = ac.get("gs", 0) or 0
            if dist_remaining and ground_speed_kts > 0:
                if DISTANCE_UNITS == "metric":
                    dist_nm = dist_remaining * 0.539957
                else:
                    dist_nm = dist_remaining * 0.868976
                hrs_left = dist_nm / ground_speed_kts
                mins_left = int(hrs_left * 60)
                if mins_left > 0:
                    h = mins_left // 60
                    m = mins_left % 60
                    time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"

            # Total route distance for progress bar
            total_distance = (
                haversine(origin_lat, origin_lon, dest_lat, dest_lon)
                if (origin_lat and origin_lon and dest_lat and dest_lon) else None
            )

            airline_name = route.get("airline_name", "")

            return {
                "callsign": flight_input,
                "number": flight_input,
                "airline_name": airline_name,
                "is_live": has_position,
                "origin": origin,
                "destination": destination,
                "dest_lat": dest_lat,
                "dest_lon": dest_lon,
                "aircraft_type": ac.get("t", ""),
                "altitude": ac.get("alt_baro", 0) or 0,
                "ground_speed": ground_speed_kts,
                "heading": ac.get("track", 0) or 0,
                "dist_remaining": dist_remaining,
                "total_distance": total_distance,
                "time_remaining": time_remaining,
                "latitude": plane_lat,
                "longitude": plane_lon,
                "last_seen_ts": time(),
                "vertical_speed": ac.get("baro_rate", 0) or 0,
                "time_scheduled_departure": None,
                "time_scheduled_arrival": None,
                "time_real_departure": None,
                "time_estimated_arrival": None,
            }

        except Exception as e:
            print(f"Failed to grab tracked flight: {e}")
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
        with self._lock:
            return len(self._data) == 0


if __name__ == "__main__":
    o = Overhead()
    o.grab_data()
    while not o.new_data:
        print("processing...")
        sleep(1)
    print("Overhead:", o.data)
    print("Tracked:", o.tracked_data)
