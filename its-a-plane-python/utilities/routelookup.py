"""
routelookup.py — Orchestrates route lookup across available APIs.
Auto-detects which API keys are configured and uses them in order:
  1. AirLabs  (free tier, 1,000 calls/month)
  2. FlightAware (free $5 credit, ~1,000 calls/month)
  3. FR24 (paid, reliable fallback)

Only uses APIs that have keys configured in config.py.
Logs usage counts per source to api_usage.log.

Examples:
  Only AIRLABS_API_KEY set        → uses AirLabs only
  Only FLIGHTAWARE_API_KEY set    → uses FlightAware only
  Only FR24 credentials set       → uses FR24 only
  All three set                   → AirLabs → FlightAware → FR24
  AIRLABS + FLIGHTAWARE set       → AirLabs → FlightAware (no FR24)
"""

import json
import os
from datetime import datetime

BASE_DIR      = os.path.dirname(os.path.dirname(__file__))
# Module-level cache so it persists across grab cycles
_route_cache = {}  # callsign -> result dict
CACHE_TTL = 3600   # 1 hour

# Import each client — they self-disable if no key is configured
from utilities.airlabs     import get_flight_details as _airlabs_details, get_tracked_flight as _airlabs_tracked, is_available as _airlabs_ok
from utilities.flightaware import get_flight_details as _fa_details,      get_tracked_flight as _fa_tracked,      is_available as _fa_ok

# FR24 is optional — only imported if credentials exist
try:
    from config import OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET
    _has_opensky = bool(OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET)
except Exception:
    _has_opensky = False

try:
    from utilities.flightradar import FR24Client as _FR24Client
    _fr24_client = _FR24Client()
    _has_fr24 = True
except Exception:
    _fr24_client = None
    _has_fr24 = False


USAGE_FILE = os.path.join(BASE_DIR, "api_usage.json")

# ---------------------------------------------------------------------------
# Regional brand resolution
# Multi-brand regionals file under the marketing carrier's callsign prefix.
# e.g. SKW files as "SKW" but operates UA4370 → show "United Airlines"
# ---------------------------------------------------------------------------

AMBIGUOUS_REGIONALS = {
    # US
    "RPA", "SKW", "ENY", "JIA", "EDV", "GJS", "CPZ", "ASQ", "PDT", "JZA",
    # European
    "CLH", "LHX", "DLA", "HOP", "KLC", "CFE", "ANE", "BCY", "EAI", "FCM",
}

MARKETING_BRANDS = {
    # US
    "UA": "United Airlines",    "AA": "American Airlines",
    "DL": "Delta Air Lines",    "AS": "Alaska Airlines",
    "WN": "Southwest Airlines", "B6": "JetBlue Airways",
    "NK": "Spirit Airlines",    "F9": "Frontier Airlines",
    # European
    "LH": "Lufthansa",   "BA": "British Airways", "AF": "Air France",
    "KL": "KLM",         "IB": "Iberia",          "SK": "SAS",
    "EI": "Aer Lingus",  "AY": "Finnair",          "AC": "Air Canada",
}

# ---------------------------------------------------------------------------
# adsbdb GA owner lookup (free, no key, cached 1 hour)
# ---------------------------------------------------------------------------

import requests as _requests
from time import time as _time

_adsbdb_cache = {}
_ADSBDB_TTL   = 3600

def _adsbdb_owner(registration):
    """Look up registered owner of an N-number aircraft via adsbdb.com.
    Returns owner name string or empty string. Results cached 1 hour."""
    if not registration:
        return ""
    cached = _adsbdb_cache.get(registration)
    if cached and (_time() - cached["ts"]) < _ADSBDB_TTL:
        return cached["name"]
    try:
        r = _requests.get(
            f"https://api.adsbdb.com/v0/aircraft/{registration}",
            timeout=8,
        )
        if r.status_code == 404:
            _adsbdb_cache[registration] = {"name": "", "ts": _time()}
            return ""
        r.raise_for_status()
        ac = r.json().get("response", {}).get("aircraft") or {}
        name = ac.get("registered_owner", "")
        if name and name == name.upper():
            name = name.title()
        _adsbdb_cache[registration] = {"name": name, "ts": _time()}
        return name
    except Exception:
        # Cache failure briefly so we don't hammer on errors
        _adsbdb_cache[registration] = {"name": "", "ts": _time() - _ADSBDB_TTL + 300}
        return ""


def _load_usage():
    try:
        with open(USAGE_FILE, "r") as f:
            data = json.load(f)
        # Reset if month changed
        if data.get("month") != datetime.now().strftime("%Y-%m"):
            return {"month": datetime.now().strftime("%Y-%m"), "AirLabs": 0, "FlightAware": 0.0, "FR24": 0}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"month": datetime.now().strftime("%Y-%m"), "AirLabs": 0, "FlightAware": 0.0, "FR24": 0}


def _save_usage(data):
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


API_LOG = os.path.join(BASE_DIR, "api_calls.log")


def _log_usage(source, callsign, origin, dest):
    """Increment usage counter, overwrite usage file, and append to call log."""
    data = _load_usage()
    if "AirLabs" in source:
        data["AirLabs"] = data.get("AirLabs", 0) + 1
    elif "FlightAware" in source:
        data["FlightAware"] = round(data.get("FlightAware", 0.0) + 0.005, 3)
    elif "FR24" in source:
        data["FR24"] = data.get("FR24", 0) + 1
    _save_usage(data)

    # Append to call log — local time so it matches your timezone
    try:
        ts      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        route   = f"{origin or '?'}→{dest or '?'}"
        al      = data.get("AirLabs", 0)
        fa      = data.get("FlightAware", 0.0)
        fr      = data.get("FR24", 0)
        line    = f"{ts}  {source:20s}  {callsign:10s}  {route:12s}  [AL:{al} FA:${fa:.3f} FR:{fr}]\n"
        with open(API_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass





def _is_plausible(result, plane_lat, plane_lon):
    """
    Check that the plane's current position lies roughly between
    origin and destination. Rejects codeshare mismatches where the
    same flight number is used on a completely different route.
    Returns True if plausible or if coords are missing (can't check).
    """
    import math as _m

    o_lat = result.get("origin_latitude") or result.get("origin_lat")
    o_lon = result.get("origin_longitude") or result.get("origin_lon")
    d_lat = result.get("destination_latitude") or result.get("dest_lat")
    d_lon = result.get("destination_longitude") or result.get("dest_lon")

    if not all((o_lat, o_lon, d_lat, d_lon, plane_lat, plane_lon)):
        return True  # can't check, let it through

    def _nm(la1, lo1, la2, lo2):
        la1, lo1, la2, lo2 = map(_m.radians, (la1, lo1, la2, lo2))
        a = _m.sin((la2-la1)/2)**2 + _m.cos(la1)*_m.cos(la2)*_m.sin((lo2-lo1)/2)**2
        return 3440.07 * 2 * _m.atan2(_m.sqrt(a), _m.sqrt(1-a))

    total = _nm(o_lat, o_lon, d_lat, d_lon)
    to_o  = _nm(plane_lat, plane_lon, o_lat, o_lon)
    to_d  = _nm(plane_lat, plane_lon, d_lat, d_lon)

    return (to_o + to_d) <= total * 1.25


def _normalise(result, callsign, plane_lat, plane_lon, registration=""):
    """
    Convert any source's result into the standard entry dict
    that overhead.py / display scenes expect.
    Adds distance_origin and distance_destination.
    Applies regional brand resolution and adsbdb GA owner fallback.
    """
    if not result:
        return None

    import math

    try:
        from config import DISTANCE_UNITS
    except Exception:
        DISTANCE_UNITS = "imperial"

    def hav(lat1, lon1, lat2, lon2):
        if not all((lat1, lon1, lat2, lon2)):
            return 0
        lat1, lon1 = map(math.radians, (lat1, lon1))
        lat2, lon2 = map(math.radians, (lat2, lon2))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        miles = 3958.8 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return miles * 1.609 if DISTANCE_UNITS == "metric" else miles

    # Support both naming conventions
    origin_lat = result.get("origin_latitude") or result.get("origin_lat")
    origin_lon = result.get("origin_longitude") or result.get("origin_lon")
    dest_lat   = result.get("destination_latitude") or result.get("dest_lat")
    dest_lon   = result.get("destination_longitude") or result.get("dest_lon")

    # owner_icao used for logo — must be 3-letter ICAO, never 2-letter IATA
    owner_icao = result.get("airline_icao", "")
    if not owner_icao and len(callsign) >= 3 and callsign[:3].isalpha():
        owner_icao = callsign[:3]
    owner_icao = owner_icao or ""

    # Airline name: start with what the source gave us
    airline_name = result.get("airline_name", "") or result.get("operator", "") or ""

    # Regional brand override — if operator is a known regional, use the
    # marketing brand name from the callsign prefix (e.g. UA4370 → "United Airlines")
    # but keep owner_icao as the actual operator (SKW) so the correct logo shows
    if owner_icao in AMBIGUOUS_REGIONALS:
        iata_prefix = callsign[:2] if len(callsign) >= 3 else ""
        brand = MARKETING_BRANDS.get(iata_prefix, "")
        if brand:
            airline_name = brand

    # GA fallback — N-number with no airline name → look up registered owner
    if not airline_name and registration and registration.startswith("N") and registration[1:2].isdigit():
        airline_name = _adsbdb_owner(registration)

    return {
        "airline":               airline_name,
        "plane":                 result.get("plane_type", "") or result.get("aircraft_type", ""),
        "origin":                result.get("origin_iata", "?") or "?",
        "origin_latitude":       origin_lat,
        "origin_longitude":      origin_lon,
        "destination":           result.get("dest_iata", "?") or "?",
        "destination_latitude":  dest_lat,
        "destination_longitude": dest_lon,
        "owner_iata":            result.get("airline_iata", "N/A") or "N/A",
        "owner_icao":            owner_icao,
        "time_scheduled_departure": result.get("time_scheduled_departure"),
        "time_scheduled_arrival":   result.get("time_scheduled_arrival"),
        "time_real_departure":      result.get("time_real_departure"),
        "time_estimated_arrival":   result.get("time_estimated_arrival"),
        "trail":                 result.get("trail", []),
    }


class RouteClient:
    """
    Unified route lookup client.
    Tries available APIs in order: AirLabs → FlightAware → FR24.
    Only calls APIs that have keys configured.
    """

    def __init__(self):
        sources = []
        if _airlabs_ok():
            sources.append("AirLabs")
        if _fa_ok():
            sources.append("FlightAware")
        if _has_fr24:
            sources.append("FR24")
        if sources:
            print(f"[RouteClient] Active sources: {' → '.join(sources)}")
        else:
            print("[RouteClient] WARNING: No API keys configured — flights will show callsign only")

    @property
    def ok(self):
        return True

    def get_flight_details(self, callsign, plane_lat, plane_lon,
                           plane_type="", registration="", distance=0.0):
        """Try each source in order, return first successful result."""
        from time import time

        # Check module-level cache first
        cached = _route_cache.get(callsign)
        if cached is not None and (time() - cached["ts"]) < CACHE_TTL:
            return cached["data"]  # may be {} for a cached miss

        from time import time

        def _cache_and_return(data):
            _route_cache[callsign] = {"data": data, "ts": time()}
            return data

        # 1. AirLabs
        if _airlabs_ok():
            result = _airlabs_details(callsign)
            _log_usage("AirLabs", callsign, result.get("origin_iata") if result else None, result.get("dest_iata") if result else None)
            if result and result.get("origin_iata"):
                if not _is_plausible(result, plane_lat, plane_lon):
                    print(f"[RouteClient] AirLabs {result.get('origin_iata')}-{result.get('dest_iata')} "
                          f"rejected for {callsign} — trying next source")
                else:
                    normalised = _normalise(result, callsign, plane_lat, plane_lon, registration)
                    if normalised:
                        return _cache_and_return(normalised)

        # 2. FlightAware
        if _fa_ok():
            result = _fa_details(callsign)
            _log_usage("FlightAware", callsign, result.get("origin_iata") if result else None, result.get("dest_iata") if result else None)
            if result and result.get("origin_iata"):
                if not _is_plausible(result, plane_lat, plane_lon):
                    print(f"[RouteClient] FlightAware {result.get('origin_iata')}-{result.get('dest_iata')} "
                          f"rejected for {callsign} — trying next source")
                else:
                    normalised = _normalise(result, callsign, plane_lat, plane_lon, registration)
                    if normalised:
                        return _cache_and_return(normalised)

        # 3. FR24
        if _has_fr24 and _fr24_client:
            result = _fr24_client.get_flight_details(callsign, plane_lat, plane_lon,
                                                      plane_type, registration, distance)
            _log_usage("FR24", callsign, result.get("origin_iata") if result else None, result.get("dest_iata") if result else None)
            if result and result.get("origin_iata") not in ("?", "", None):
                if not _is_plausible(result, plane_lat, plane_lon):
                    print(f"[RouteClient] FR24 {result.get('origin_iata')}-{result.get('dest_iata')} "
                          f"rejected for {callsign} — all sources exhausted")
                else:
                    normalised = _normalise(result, callsign, plane_lat, plane_lon, registration)
                    if normalised:
                        return _cache_and_return(normalised)

        # All failed — cache the miss so we don't hammer APIs repeatedly
        from time import time
        _route_cache[callsign] = {"data": {}, "ts": time()}
        _log_usage("NONE", callsign, None, None)
        return {
            "airline": "Private", "plane": plane_type,
            "origin": "?", "origin_latitude": None, "origin_longitude": None,
            "destination": "?", "destination_latitude": None, "destination_longitude": None,
            "owner_iata": "N/A", "owner_icao": callsign[:3] if len(callsign) >= 3 else "",
            "time_scheduled_departure": None, "time_scheduled_arrival": None,
            "time_real_departure": None, "time_estimated_arrival": None,
            "distance_origin": 0, "distance_destination": 0, "trail": [],
        }

    def get_tracked_flight(self, callsign):
        """Search for a tracked flight globally across available sources."""

        # 1. AirLabs
        if _airlabs_ok():
            result = _airlabs_tracked(callsign)
            if result and result.get("is_live"):
                _log_usage("AirLabs(tracked)", callsign, result.get("origin"), result.get("destination"))
                return result

        # 2. FlightAware
        if _fa_ok():
            result = _fa_tracked(callsign)
            if result and result.get("is_live"):
                _log_usage("FlightAware(tracked)", callsign, result.get("origin"), result.get("destination"))
                return result

        # 3. FR24
        if _has_fr24 and _fr24_client:
            result = _fr24_client.get_tracked_flight(callsign)
            if result:
                _log_usage("FR24(tracked)", callsign, result.get("origin"), result.get("destination"))
                return result

        return None
