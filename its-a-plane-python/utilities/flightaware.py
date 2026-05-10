"""
flightaware.py — Route lookup via FlightAware AeroAPI.
Free tier: ~1,000 calls/month ($5 credit at $0.005/call).
Get a key at: https://flightaware.com/aeroapi/portal
Set FLIGHTAWARE_API_KEY in config.py to enable.
Set FLIGHTAWARE_MONTHLY_LIMIT to cap spending (default $4.00).
"""

import json
import os
import requests
from datetime import datetime, timezone
from time import time


def _to_unix(dt_str):
    """Convert ISO datetime string to Unix timestamp, or return None."""
    if not dt_str:
        return None
    try:
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str).timestamp()
    except Exception:
        try:
            return datetime.strptime(dt_str[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc).timestamp()
        except Exception:
            return None

try:
    from config import FLIGHTAWARE_API_KEY
except (ImportError, ModuleNotFoundError, NameError):
    FLIGHTAWARE_API_KEY = None

try:
    from config import FLIGHTAWARE_MONTHLY_LIMIT
except (ImportError, ModuleNotFoundError, NameError):
    FLIGHTAWARE_MONTHLY_LIMIT = 4.00

BASE_URL         = "https://aeroapi.flightaware.com/aeroapi"
COST_PER_CALL    = 0.005
CACHE_TTL        = 3600  # 1 hour

BASE_DIR         = os.path.dirname(os.path.dirname(__file__))
USAGE_FILE       = os.path.join(BASE_DIR, "flightaware_usage.json")

from utilities.airports import get_airport_coords as _airport_coords
from utilities.airlines import get_airline_name as _lookup_airline

_cache = {}


def is_available():
    return bool(FLIGHTAWARE_API_KEY)


def _load_usage():
    try:
        with open(USAGE_FILE, "r") as f:
            usage = json.load(f)
        if usage.get("month") != datetime.now().strftime("%Y-%m"):
            return {"month": datetime.now().strftime("%Y-%m"), "calls": 0, "cost": 0.0}
        return usage
    except (FileNotFoundError, json.JSONDecodeError):
        return {"month": datetime.now().strftime("%Y-%m"), "calls": 0, "cost": 0.0}


def _save_usage(usage):
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(usage, f, indent=2)
    except Exception as e:
        print(f"[FlightAware] Failed to save usage: {e}")



def _parse_flight(f):
    """Parse a FlightAware flight object into standardised fields."""
    origin = f.get("origin") or {}
    dest   = f.get("destination") or {}

    origin_lat = origin.get("latitude")
    origin_lon = origin.get("longitude")
    dest_lat   = dest.get("latitude")
    dest_lon   = dest.get("longitude")

    if origin.get("code_icao") and not origin_lat:
        coords = _airport_coords(origin["code_icao"])
        origin_lat = coords.get("lat")
        origin_lon = coords.get("lon")
    if dest.get("code_icao") and not dest_lat:
        coords = _airport_coords(dest["code_icao"])
        dest_lat = coords.get("lat")
        dest_lon = coords.get("lon")

    # operator_iata (e.g. "MQ") → marketing brand name ("American Eagle Airlines")
    # operator_icao (e.g. "ENY") → logo
    operator_iata = f.get("operator_iata", "")
    operator_icao = f.get("operator_icao", "")
    airline_name = (
        _lookup_airline(operator_iata)
        or _lookup_airline(operator_icao)
        or f.get("operator", "")
        or operator_icao
    )

    return {
        "airline_name": airline_name,
        "airline_icao": f.get("operator_icao", ""),
        "airline_iata": f.get("operator_iata", ""),
        "origin_iata":  origin.get("code_iata", ""),
        "origin_lat":   origin_lat,
        "origin_lon":   origin_lon,
        "dest_iata":    dest.get("code_iata", ""),
        "dest_lat":     dest_lat,
        "dest_lon":     dest_lon,
        "plane_type":   f.get("aircraft_type", ""),
        # FA field names: scheduled_out=gate departure, scheduled_in=gate arrival
        # actual_out=actual gate dep, estimated_in=estimated gate arr
        "time_scheduled_departure": _to_unix(f.get("scheduled_out")),
        "time_scheduled_arrival":   _to_unix(f.get("scheduled_in")),
        "time_real_departure":      _to_unix(f.get("actual_out") or f.get("estimated_out")),
        "time_estimated_arrival":   _to_unix(f.get("estimated_in") or f.get("actual_in")),
    }


def get_flight_details(callsign):
    """
    Look up route info for an overhead callsign.
    Returns standardised dict or empty dict if not found/over budget.
    """
    if not FLIGHTAWARE_API_KEY:
        return {}

    now    = time()
    cached = _cache.get(callsign)
    if cached and (now - cached["ts"]) < CACHE_TTL:
        return cached["data"]

    usage = _load_usage()
    if usage["cost"] >= FLIGHTAWARE_MONTHLY_LIMIT:
        return {}

    try:
        r = requests.get(
            f"{BASE_URL}/flights/{callsign}",
            headers={"x-apikey": FLIGHTAWARE_API_KEY},
            params={"max_pages": 1},
            timeout=10,
        )
        if r.status_code != 200:
                return {}

        flights = r.json().get("flights", [])
        usage["calls"] += 1
        usage["cost"]  += COST_PER_CALL
        _save_usage(usage)

        if not flights:
            _cache[callsign] = {"data": {}, "ts": now}
            return {}

        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)

        def _departed_recently(fl):
            """True if flight actually departed within last 12 hours."""
            actual = fl.get("actual_out") or fl.get("estimated_out")
            if not actual:
                return False
            try:
                dep = datetime.fromisoformat(actual.replace("Z", "+00:00"))
                return timedelta(0) <= (now_utc - dep) <= timedelta(hours=12)
            except Exception:
                return False

        # 1. En Route and departed recently
        # 2. En Route (any)
        # 3. Most recently departed with route
        # 4. First with route
        f = (
            next((fl for fl in flights
                  if fl.get("status") == "En Route"
                  and _departed_recently(fl)
                  and fl.get("origin") and fl.get("destination")), None)
            or next((fl for fl in flights
                  if fl.get("status") == "En Route"
                  and fl.get("origin") and fl.get("destination")), None)
            or next((fl for fl in sorted(flights,
                     key=lambda x: x.get("actual_out") or x.get("scheduled_out") or "",
                     reverse=True)
                  if _departed_recently(fl)
                  and fl.get("origin") and fl.get("destination")), None)
            or next((fl for fl in flights if fl.get("origin") and fl.get("destination")), None)
            or flights[0]
        )

        result = _parse_flight(f)
        _cache[callsign] = {"data": result, "ts": now}
        return result

    except Exception as e:
        print(f"[FlightAware] {callsign}: error — {e}")
        return {}


def get_tracked_flight(callsign):
    """
    Search for a specific tracked flight globally.
    Returns enriched dict or None.
    """
    if not FLIGHTAWARE_API_KEY:
        return None

    usage = _load_usage()
    if usage["cost"] >= FLIGHTAWARE_MONTHLY_LIMIT:
        return None

    try:
        r = requests.get(
            f"{BASE_URL}/flights/{callsign}",
            headers={"x-apikey": FLIGHTAWARE_API_KEY},
            params={"max_pages": 1},
            timeout=10,
        )
        if r.status_code != 200:
            return None

        flights = r.json().get("flights", [])
        usage["calls"] += 1
        usage["cost"]  += COST_PER_CALL
        _save_usage(usage)

        if not flights:
            return None

        f = next(
            (fl for fl in flights if fl.get("status") == "En Route"),
            flights[0]
        )

        parsed = _parse_flight(f)
        lat = f.get("last_position", {}).get("latitude")
        lon = f.get("last_position", {}).get("longitude")
        has_position = lat is not None and lon is not None


        return {
            "callsign":      callsign,
            "number":        f.get("ident", callsign),
            "airline_name":  parsed["airline_name"],
            "is_live":       has_position,
            "origin":        parsed["origin_iata"],
            "destination":   parsed["dest_iata"],
            "dest_lat":      parsed["dest_lat"],
            "dest_lon":      parsed["dest_lon"],
            "aircraft_type": f.get("aircraft_type", ""),
            "altitude":      f.get("last_position", {}).get("altitude", 0) or 0,
            "ground_speed":  f.get("last_position", {}).get("groundspeed", 0) or 0,
            "heading":       f.get("last_position", {}).get("heading", 0) or 0,
            "latitude":      lat,
            "longitude":     lon,
            "time_scheduled_departure": parsed["time_scheduled_departure"],
            "time_scheduled_arrival":   parsed["time_scheduled_arrival"],
            "time_real_departure":      parsed["time_real_departure"],
            "time_estimated_arrival":   parsed["time_estimated_arrival"],
        }

    except Exception as e:
        print(f"[FlightAware] tracked error for {callsign}: {e}")
        return None
