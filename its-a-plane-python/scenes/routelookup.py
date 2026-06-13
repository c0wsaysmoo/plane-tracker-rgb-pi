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

try:
    from utilities.flightradar import FR24Client as _FR24Client
    _fr24_client = _FR24Client()
    _has_fr24 = True
except Exception:
    _fr24_client = None
    _has_fr24 = False


USAGE_FILE = os.path.join(BASE_DIR, "api_usage.json")
API_LOG    = os.path.join(BASE_DIR, "api_calls.log")


def _billing_period_start(reset_day=1):
    import calendar
    day = max(1, min(31, int(reset_day)))
    today = datetime.now().date()
    def _clamp(year, month):
        return min(day, calendar.monthrange(year, month)[1])
    if today.day >= _clamp(today.year, today.month):
        return today.replace(day=_clamp(today.year, today.month)).isoformat()
    prev_year, prev_month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return today.replace(year=prev_year, month=prev_month, day=_clamp(prev_year, prev_month)).isoformat()


def _load_usage():
    try:
        from config import AIRLABS_RESET_DAY, FLIGHTAWARE_RESET_DAY, FR24_RESET_DAY
    except Exception:
        AIRLABS_RESET_DAY = FLIGHTAWARE_RESET_DAY = FR24_RESET_DAY = 1
    al_p = _billing_period_start(AIRLABS_RESET_DAY)
    fa_p = _billing_period_start(FLIGHTAWARE_RESET_DAY)
    fr_p = _billing_period_start(FR24_RESET_DAY)
    try:
        with open(USAGE_FILE, "r") as f:
            data = json.load(f)
        # Reset each source independently when its billing period rolls over
        if data.get("AirLabs_period_start") != al_p:
            data["AirLabs"] = 0
            data["AirLabs_period_start"] = al_p
        if data.get("FlightAware_period_start") != fa_p:
            data["FlightAware"] = 0.0
            data["FlightAware_period_start"] = fa_p
        if data.get("FR24_period_start") != fr_p:
            data["FR24"] = 0
            data["FR24_period_start"] = fr_p
        for _k in ("FlightStats", "FR24Unofficial", "month", "period_start"):
            data.pop(_k, None)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "AirLabs": 0,           "AirLabs_period_start": al_p,
            "FlightAware": 0.0,     "FlightAware_period_start": fa_p,
            "FR24": 0,              "FR24_period_start": fr_p,
        }


def _save_usage(data):
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


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
    """
    if not result:
        return None

    # Support both naming conventions
    origin_lat = result.get("origin_latitude") or result.get("origin_lat")
    origin_lon = result.get("origin_longitude") or result.get("origin_lon")
    dest_lat   = result.get("destination_latitude") or result.get("dest_lat")
    dest_lon   = result.get("destination_longitude") or result.get("dest_lon")

    # owner_icao = operating carrier ICAO for logo lookup
    owner_icao = result.get("airline_icao", "")
    if not owner_icao and len(callsign) >= 3 and callsign[:3].isalpha():
        owner_icao = callsign[:3]
    owner_icao = owner_icao or ""

    return {
        "airline":               result.get("airline_name", ""),
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
    Tries available APIs in config-defined order.
    """

    def __init__(self):
        self._last_source = None
        try:
            from config import API_SOURCE_ORDER as _order, API_SOURCE_ENABLED as _enabled
        except ImportError:
            _order   = ["AirLabs", "FlightAware", "FR24"]
            _enabled = {}

        active = [s for s in _order if _enabled.get(s, True)]
        if active:
            print(f"[RouteClient] Active sources: {' → '.join(active)}")
        else:
            print("[RouteClient] WARNING: No API keys configured — flights will show callsign only")

    @property
    def ok(self):
        return True

    @property
    def last_source(self):
        return self._last_source

    def get_flight_details(self, callsign, plane_lat, plane_lon,
                           plane_type="", registration="", distance=0.0):
        """Try each source in configuration order, return first successful result."""
        from time import time
        import re as _re

        # Check module-level cache first
        cached = _route_cache.get(callsign)
        if cached is not None and (time() - cached["ts"]) < CACHE_TTL:
            return cached["data"]  # may be {} for a cached miss

        def _cache_and_return(data):
            _route_cache[callsign] = {"data": data, "ts": time()}
            return data

        # Check callsign structure eligibility (matches private implementation logic)
        _is_icao = bool(_re.match(r"^[A-Z]{3}[A-Z0-9]+$", callsign))
        if not _is_icao:
            # Fallback early to default values if it's a tail registration number (e.g. N487CB)
            return _cache_and_return({
                "airline": "Private", "plane": plane_type,
                "origin": "?", "origin_latitude": None, "origin_longitude": None,
                "destination": "?", "destination_latitude": None, "destination_longitude": None,
                "owner_iata": "N/A", "owner_icao": callsign[:3] if len(callsign) >= 3 else "",
                "time_scheduled_departure": None, "time_scheduled_arrival": None,
                "time_real_departure": None, "time_estimated_arrival": None,
                "distance_origin": 0, "distance_destination": 0, "trail": [],
            })

        # Load API execution configs dynamically
        try:
            from config import API_SOURCE_ORDER as _order, API_SOURCE_ENABLED as _enabled
        except ImportError:
            _order   = ["AirLabs", "FlightAware", "FR24"]
            _enabled = {}

        # Dictionary routing setup mapping config strings to actual functional methods and checks
        _SOURCE_FNS = {
            "AirLabs":     (lambda cs: _airlabs_details(cs), _airlabs_ok),
            "FlightAware": (lambda cs: _fa_details(cs),      _fa_ok),
            "FR24":        (lambda cs: _fr24_client.get_flight_details(cs, plane_lat, plane_lon, plane_type, registration, distance)
                            if _fr24_client else None, lambda: bool(_has_fr24 and _fr24_client))
        }

        dubious_route = None
        dubious_key   = None
        dubious_count = 0

        for source in _order:
            if not _enabled.get(source, True):
                continue
            if source not in _SOURCE_FNS:
                continue

            fn, check = _SOURCE_FNS[source]
            if not check():
                continue

            result = fn(callsign)
            _log_usage(source, callsign,
                       result.get("origin_iata") if result else None,
                       result.get("dest_iata")   if result else None)

            if not result or result.get("origin_iata") in ("?", "", None):
                continue

            if _is_plausible(result, plane_lat, plane_lon):
                normalised = _normalise(result, callsign, plane_lat, plane_lon, registration)
                if normalised:
                    self._last_source = source
                    return _cache_and_return(normalised)
            else:
                key = (result.get("origin_iata"), result.get("dest_iata"))
                if dubious_route is None:
                    dubious_route = result
                    dubious_key   = key
                    dubious_count = 1
                elif key == dubious_key:
                    dubious_count += 1
                    if dubious_count >= 2:
                        _log_usage(f"CONSENSUS({dubious_count})", callsign, key[0], key[1])
                        normalised = _normalise(dubious_route, callsign, plane_lat, plane_lon, registration)
                        if normalised:
                            self._last_source = source
                            return _cache_and_return(normalised)

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
        try:
            from config import API_SOURCE_ORDER as _order, API_SOURCE_ENABLED as _enabled
        except ImportError:
            _order   = ["AirLabs", "FlightAware", "FR24"]
            _enabled = {}

        _TRACK_FNS = {
            "AirLabs":     (_airlabs_tracked, _airlabs_ok),
            "FlightAware": (_fa_tracked,      _fa_ok),
            "FR24":        (lambda cs: _fr24_client.get_tracked_flight(cs) if _fr24_client else None,
                            lambda: bool(_has_fr24 and _fr24_client))
        }

        for source in _order:
            if not _enabled.get(source, True):
                continue
            if source not in _TRACK_FNS:
                continue

            fn, check = _TRACK_FNS[source]
            if not check():
                continue

            result = fn(callsign)
            if result:
                _log_usage(f"{source}(tracked)", callsign, result.get("origin"), result.get("destination"))
                return result

        return None
