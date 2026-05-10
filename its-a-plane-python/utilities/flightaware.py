"""
flightaware.py — Route lookup via FlightAware AeroAPI.
Free tier: ~1,000 calls/month ($5 credit at $0.005/call) per key.
Supports multiple API keys — rotates when one hits its budget cap.

In config.py set either:
    FLIGHTAWARE_API_KEY  = "single_key"
    FLIGHTAWARE_API_KEYS = ["key1", "key2", ...]
    FLIGHTAWARE_MONTHLY_LIMIT = 4.00  # $ cap per key (default $4.00)
"""

import json
import os
import requests
from datetime import datetime, timezone, timedelta
from time import time

BASE_DIR     = os.path.dirname(os.path.dirname(__file__))
USAGE_FILE   = os.path.join(BASE_DIR, "flightaware_usage.json")
BASE_URL     = "https://aeroapi.flightaware.com/aeroapi"
COST_PER_CALL = 0.005
CACHE_TTL    = 3600

from utilities.airports import get_airport_coords as _airport_coords

_cache = {}

_AIRLINE_NAMES = {
    "ENY": "American Eagle",  "MQ": "American Eagle",
    "SKW": "SkyWest Airlines","OO": "SkyWest Airlines",
    "RPA": "United Express",  "YX": "United Express",
    "GJS": "United Express",  "G7": "United Express",
    "EDV": "Delta Connection","9E": "Delta Connection",
    "JIA": "American Eagle",  "CPZ": "United Express",
    "AAL": "American Airlines","AA": "American Airlines",
    "UAL": "United Airlines",  "UA": "United Airlines",
    "DAL": "Delta Air Lines",  "DL": "Delta Air Lines",
    "SWA": "Southwest Airlines","WN": "Southwest Airlines",
    "FFT": "Frontier Airlines","F9": "Frontier Airlines",
    "NKS": "Spirit Airlines",  "NK": "Spirit Airlines",
    "JBU": "JetBlue Airways",  "B6": "JetBlue Airways",
    "ASA": "Alaska Airlines",  "AS": "Alaska Airlines",
    "HAL": "Hawaiian Airlines","HA": "Hawaiian Airlines",
    "ETD": "Etihad Airways",   "EY": "Etihad Airways",
    "KAL": "Korean Air",       "KE": "Korean Air",
    "ANA": "All Nippon Airways","NH": "All Nippon Airways",
    "BAW": "British Airways",  "BA": "British Airways",
    "DLH": "Lufthansa",        "LH": "Lufthansa",
    "AFR": "Air France",       "AF": "Air France",
}

def _get_airline_name(icao, iata, fa_operator):
    if fa_operator and len(fa_operator) > 4 and not fa_operator.isupper():
        return fa_operator
    return (_AIRLINE_NAMES.get(icao) or _AIRLINE_NAMES.get(iata)
            or fa_operator or icao or "")


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def _load_keys():
    try:
        from config import FLIGHTAWARE_API_KEYS
        if isinstance(FLIGHTAWARE_API_KEYS, list) and FLIGHTAWARE_API_KEYS:
            return [k for k in FLIGHTAWARE_API_KEYS if k]
    except (ImportError, AttributeError):
        pass
    try:
        from config import FLIGHTAWARE_API_KEY
        if FLIGHTAWARE_API_KEY:
            return [FLIGHTAWARE_API_KEY]
    except (ImportError, AttributeError):
        pass
    return []

def _load_limit():
    try:
        from config import FLIGHTAWARE_MONTHLY_LIMIT
        return float(FLIGHTAWARE_MONTHLY_LIMIT)
    except (ImportError, AttributeError):
        return 4.00

_KEYS  = _load_keys()
_LIMIT = _load_limit()


# ---------------------------------------------------------------------------
# Usage tracking — per key
# ---------------------------------------------------------------------------

def _load_usage():
    try:
        with open(USAGE_FILE, "r") as f:
            usage = json.load(f)
        if usage.get("month") != datetime.now().strftime("%Y-%m"):
            return {"month": datetime.now().strftime("%Y-%m"), "keys": {}}
        if "keys" not in usage:
            # Migrate old format
            usage = {"month": usage["month"], "keys": {}}
            if _KEYS:
                usage["keys"][_KEYS[0]] = {"calls": usage.get("calls", 0),
                                            "cost": usage.get("cost", 0.0)}
        return usage
    except (FileNotFoundError, json.JSONDecodeError):
        return {"month": datetime.now().strftime("%Y-%m"), "keys": {}}

def _save_usage(usage):
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(usage, f, indent=2)
    except Exception:
        pass

def _get_active_key():
    if not _KEYS:
        return None, None
    usage = _load_usage()
    for i, key in enumerate(_KEYS):
        kdata = usage["keys"].get(key, {"calls": 0, "cost": 0.0})
        cost  = kdata.get("cost", 0.0)
        if cost < _LIMIT:
            if len(_KEYS) > 1:
                print(f"[FlightAware] Using key {i+1}/{len(_KEYS)} (${cost:.3f}/${_LIMIT:.2f})")
            return key, usage
        else:
            print(f"[FlightAware] Key {i+1}/{len(_KEYS)} exhausted (${cost:.3f}) — trying next")
    print(f"[FlightAware] All {len(_KEYS)} key(s) exhausted for this month")
    return None, None

def _increment_usage(key, usage):
    if key not in usage["keys"]:
        usage["keys"][key] = {"calls": 0, "cost": 0.0}
    usage["keys"][key]["calls"] += 1
    usage["keys"][key]["cost"]   = round(usage["keys"][key]["cost"] + COST_PER_CALL, 3)
    _save_usage(usage)
    return usage["keys"][key]["cost"]

def is_available():
    key, _ = _get_active_key()
    return key is not None

def _load_usage_pub():
    """Public usage summary across all keys."""
    usage = _load_usage()
    total_cost  = sum(v.get("cost", 0) for v in usage["keys"].values())
    total_calls = sum(v.get("calls", 0) for v in usage["keys"].values())
    return {"calls": total_calls, "cost": total_cost}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_unix(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(str(dt_str).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def _parse_flight(f):
    origin = f.get("origin") or {}
    dest   = f.get("destination") or {}
    origin_lat = origin.get("latitude")
    origin_lon = origin.get("longitude")
    dest_lat   = dest.get("latitude")
    dest_lon   = dest.get("longitude")
    if origin.get("code_icao") and not origin_lat:
        c = _airport_coords(origin["code_icao"])
        origin_lat, origin_lon = c.get("lat"), c.get("lon")
    if dest.get("code_icao") and not dest_lat:
        c = _airport_coords(dest["code_icao"])
        dest_lat, dest_lon = c.get("lat"), c.get("lon")
    airline_name = _get_airline_name(
        f.get("operator_icao", ""), f.get("operator_iata", ""), f.get("operator", ""))
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
        "time_scheduled_departure": _to_unix(f.get("scheduled_out")),
        "time_scheduled_arrival":   _to_unix(f.get("scheduled_in")),
        "time_real_departure":      _to_unix(f.get("actual_out") or f.get("estimated_out")),
        "time_estimated_arrival":   _to_unix(f.get("estimated_in") or f.get("actual_in")),
    }


# ---------------------------------------------------------------------------
# Main lookup
# ---------------------------------------------------------------------------

def get_flight_details(callsign):
    key, usage = _get_active_key()
    if not key:
        return {}

    now = time()
    cached = _cache.get(callsign)
    if cached and (now - cached["ts"]) < CACHE_TTL:
        return cached["data"]

    try:
        r = requests.get(
            f"{BASE_URL}/flights/{callsign}",
            headers={"x-apikey": key},
            params={"max_pages": 1},
            timeout=10,
        )
        if r.status_code != 200:
            return {}

        flights = r.json().get("flights", [])
        cost = _increment_usage(key, usage)

        if not flights:
            _cache[callsign] = {"data": {}, "ts": now}
            return {}

        now_utc = datetime.now(timezone.utc)

        def _departed_recently(fl):
            actual = fl.get("actual_out") or fl.get("estimated_out")
            if not actual:
                return False
            try:
                dep = datetime.fromisoformat(actual.replace("Z", "+00:00"))
                return timedelta(0) <= (now_utc - dep) <= timedelta(hours=12)
            except Exception:
                return False

        f = (
            next((fl for fl in flights if fl.get("status") == "En Route"
                  and _departed_recently(fl)
                  and fl.get("origin") and fl.get("destination")), None)
            or next((fl for fl in flights if fl.get("status") == "En Route"
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
    key, usage = _get_active_key()
    if not key:
        return None
    try:
        r = requests.get(
            f"{BASE_URL}/flights/{callsign}",
            headers={"x-apikey": key},
            params={"max_pages": 1},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        flights = r.json().get("flights", [])
        _increment_usage(key, usage)
        if not flights:
            return None
        f = next((fl for fl in flights if fl.get("status") == "En Route"), flights[0])
        parsed = _parse_flight(f)
        lat = f.get("last_position", {}).get("latitude")
        lon = f.get("last_position", {}).get("longitude")
        return {
            "callsign":      callsign,
            "number":        f.get("ident", callsign),
            "airline_name":  parsed["airline_name"],
            "is_live":       lat is not None,
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
