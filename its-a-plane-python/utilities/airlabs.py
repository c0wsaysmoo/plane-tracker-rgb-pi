"""
airlabs.py — Route and flight info via AirLabs /flight endpoint.
Free tier: 1,000 calls/month per key.
Supports multiple API keys — rotates to next when one hits the limit.

In config.py set either a single key or a list:
    AIRLABS_API_KEYS = "single_key"
    AIRLABS_API_KEYS = ["key1", "key2", ...]
"""

import json
import os
import requests
from datetime import datetime, timezone

BASE_DIR      = os.path.dirname(os.path.dirname(__file__))
USAGE_FILE    = os.path.join(BASE_DIR, "airlabs_usage.json")
MONTHLY_LIMIT = 1050
BASE_URL      = "https://airlabs.co/api/v9"

from utilities.airports import get_airport_coords as _airport_coords
from utilities.airlines import get_airline_name as _lookup_airline


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def _load_keys():
    """Load AirLabs API keys from config. AIRLABS_API_KEYS can be a
    single string or a list of strings."""
    try:
        import config as _config
        val = getattr(_config, "AIRLABS_API_KEYS", None)
        if not val:
            return []
        if isinstance(val, list):
            return [k.strip() for k in val if k and k.strip()]
        return [val.strip()]
    except Exception:
        return []

_KEYS = _load_keys()


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
            old_calls = usage.get("calls", 0)
            usage = {"month": usage["month"], "keys": {}}
            if _KEYS:
                usage["keys"][_KEYS[0]] = old_calls
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
    """Return the first key that hasn't hit the monthly limit, or None."""
    if not _KEYS:
        return None
    usage = _load_usage()
    for i, key in enumerate(_KEYS):
        calls = usage["keys"].get(key, 0)
        if calls < MONTHLY_LIMIT:
            return key
    return None


def _increment_usage(key):
    usage = _load_usage()
    usage["keys"][key] = usage["keys"].get(key, 0) + 1
    _save_usage(usage)
    return usage["keys"][key]


def is_available():
    return _get_active_key() is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_unix(dt_str):
    if not dt_str:
        return None
    dt_str = str(dt_str)
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        pass
    try:
        return datetime.strptime(dt_str[:16], "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        pass
    return None


def _airline_name(code):
    if not code:
        return ""
    return _lookup_airline(code) or ""


# ---------------------------------------------------------------------------
# Main lookup
# ---------------------------------------------------------------------------

def get_flight_details(callsign):
    key = _get_active_key()
    if not key:
        return {}
    try:
        r = requests.get(
            f"{BASE_URL}/flight",
            params={"flight_icao": callsign, "api_key": key},
            timeout=10,
        )
        if r.status_code != 200:
            return {}
        data = r.json().get("response") or {}
        if not data:
            return {}

        calls = _increment_usage(key)

        origin_iata = data.get("dep_iata", "")
        dest_iata   = data.get("arr_iata", "")
        origin_coords = _airport_coords(origin_iata)
        dest_coords   = _airport_coords(dest_iata)

        cs_iata      = data.get("cs_airline_iata")
        airline_iata = data.get("airline_iata", "")
        airline_icao = data.get("airline_icao", "")
        if cs_iata:
            airline_name = _airline_name(cs_iata) or data.get("airline_name", "")
        else:
            airline_name = _airline_name(airline_icao) or data.get("airline_name", "")

        return {
            "airline_name": airline_name,
            "airline_icao": airline_icao,
            "airline_iata": cs_iata or airline_iata,
            "origin_iata":  origin_iata,
            "origin_lat":   origin_coords.get("lat"),
            "origin_lon":   origin_coords.get("lon"),
            "dest_iata":    dest_iata,
            "dest_lat":     dest_coords.get("lat"),
            "dest_lon":     dest_coords.get("lon"),
            "plane_type":   data.get("aircraft_icao", ""),
            "time_scheduled_departure": data.get("dep_time_ts") or _to_unix(data.get("dep_time")),
            "time_scheduled_arrival":   data.get("arr_time_ts") or _to_unix(data.get("arr_time")),
            "time_real_departure":      data.get("dep_actual_ts") or data.get("dep_estimated_ts") or _to_unix(data.get("dep_actual")),
            "time_estimated_arrival":   data.get("arr_estimated_ts") or data.get("arr_actual_ts") or _to_unix(data.get("arr_estimated")) or data.get("arr_time_ts"),
        }
    except Exception as e:
        print(f"[AirLabs] {callsign}: error — {e}")
        return {}


def get_tracked_flight(callsign):
    key = _get_active_key()
    if not key:
        return None
    try:
        r = requests.get(
            f"{BASE_URL}/flight",
            params={"flight_icao": callsign, "api_key": key},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json().get("response") or {}
        if not data:
            return None

        _increment_usage(key)

        lat = data.get("lat")
        lon = data.get("lng")
        has_position = lat is not None and lon is not None

        cs_iata      = data.get("cs_airline_iata")
        airline_icao = data.get("airline_icao", "")
        airline_iata = data.get("airline_iata", "")
        if cs_iata:
            airline_name = _airline_name(cs_iata) or data.get("airline_name", "")
        else:
            airline_name = _airline_name(airline_icao) or data.get("airline_name", "")

        origin_iata = data.get("dep_iata", "")
        dest_iata   = data.get("arr_iata", "")
        dest_coords = _airport_coords(dest_iata)

        return {
            "callsign":      callsign,
            "number":        callsign,
            "airline_name":  airline_name,
            "is_live":       has_position,
            "origin":        origin_iata,
            "destination":   dest_iata,
            "dest_lat":      dest_coords.get("lat"),
            "dest_lon":      dest_coords.get("lon"),
            "aircraft_type": data.get("aircraft_icao", ""),
            "altitude":      data.get("alt", 0) or 0,
            "ground_speed":  data.get("speed", 0) or 0,
            "heading":       data.get("dir", 0) or 0,
            "latitude":      lat,
            "longitude":     lon,
            "time_scheduled_departure": data.get("dep_time_ts") or _to_unix(data.get("dep_time")),
            "time_scheduled_arrival":   data.get("arr_time_ts") or _to_unix(data.get("arr_time")),
            "time_real_departure":      data.get("dep_actual_ts") or data.get("dep_estimated_ts"),
            "time_estimated_arrival":   data.get("arr_estimated_ts") or data.get("arr_actual_ts") or data.get("arr_time_ts"),
        }
    except Exception as e:
        print(f"[AirLabs] tracked error for {callsign}: {e}")
        return None
