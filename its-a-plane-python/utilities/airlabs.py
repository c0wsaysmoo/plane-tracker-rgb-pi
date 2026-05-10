"""
airlabs.py — Route and flight info via AirLabs /flight endpoint.
Free tier: 1,000 calls/month.
Get a key at: https://airlabs.co
Set AIRLABS_API_KEY in config.py to enable.

Uses /flight endpoint (not /flights) for richer data including:
- Unix timestamps (dep_time_ts, arr_time_ts) — no conversion needed
- Codeshare airline (cs_airline_iata) — marketing carrier name
- Live position (lat, lng)
- Aircraft type (aircraft_icao)
"""

import json
import os
import requests
from datetime import datetime, timezone

try:
    from config import AIRLABS_API_KEY
except (ImportError, ModuleNotFoundError, NameError):
    AIRLABS_API_KEY = None

BASE_DIR      = os.path.dirname(os.path.dirname(__file__))
USAGE_FILE    = os.path.join(BASE_DIR, "airlabs_usage.json")
MONTHLY_LIMIT = 1000
BASE_URL      = "https://airlabs.co/api/v9"

from utilities.airports import get_airport_coords as _airport_coords


def _to_unix(dt_str):
    """Convert datetime string to Unix timestamp or return None."""
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


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def _load_usage():
    try:
        with open(USAGE_FILE, "r") as f:
            usage = json.load(f)
        if usage.get("month") != datetime.now().strftime("%Y-%m"):
            return {"month": datetime.now().strftime("%Y-%m"), "calls": 0}
        return usage
    except (FileNotFoundError, json.JSONDecodeError):
        return {"month": datetime.now().strftime("%Y-%m"), "calls": 0}


def _save_usage(usage):
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump(usage, f, indent=2)
    except Exception as e:
        print(f"[AirLabs] Failed to save usage: {e}")


def _increment_usage():
    usage = _load_usage()
    usage["calls"] += 1
    _save_usage(usage)
    return usage["calls"]


def is_available():
    if not AIRLABS_API_KEY:
        return False
    usage = _load_usage()
    if usage["calls"] >= MONTHLY_LIMIT:
        print(f"[AirLabs] Monthly limit of {MONTHLY_LIMIT} calls reached — disabled for this month")
        return False
    return True


# ---------------------------------------------------------------------------
# Airline name lookup — gets marketing carrier name from codeshare
# ---------------------------------------------------------------------------

from utilities.airlines import get_airline_name as _lookup_airline

def _airline_name(code):
    """Look up airline name by IATA or ICAO code — local database only."""
    if not code:
        return ""
    return _lookup_airline(code) or ""


# ---------------------------------------------------------------------------
# Main lookup
# ---------------------------------------------------------------------------

def get_flight_details(callsign):
    """
    Look up route/schedule info for a callsign using AirLabs /flight endpoint.
    Returns standardised dict or empty dict if not found.
    """
    if not is_available():
        return {}

    try:
        r = requests.get(
            f"{BASE_URL}/flight",
            params={"flight_icao": callsign, "api_key": AIRLABS_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
                return {}

        data = r.json().get("response") or {}
        if not data:
                return {}

        calls = _increment_usage()

        origin_iata = data.get("dep_iata", "")
        dest_iata   = data.get("arr_iata", "")

        # Airport coords from adsb.lol since AirLabs doesn't return them
        origin_coords = _airport_coords(origin_iata)
        dest_coords   = _airport_coords(dest_iata)

        # flight_iata prefix (e.g. "AA" from "AA3945") = marketing brand name
        # airline_icao (e.g. "ENY") = operating carrier = logo
        cs_iata      = data.get("cs_airline_iata")
        airline_iata = data.get("airline_iata", "")
        airline_icao = data.get("airline_icao", "")
        flight_iata  = data.get("flight_iata", "")
        marketing_iata = flight_iata[:2] if flight_iata and len(flight_iata) >= 3 else ""

        if cs_iata:
            airline_name = _airline_name(cs_iata) or data.get("airline_name", "")
        elif marketing_iata and marketing_iata != airline_iata:
            airline_name = _airline_name(marketing_iata) or data.get("airline_name", "")
        else:
            airline_name = _airline_name(airline_icao) or data.get("airline_name", "")

        result = {
            "airline_name": airline_name,
            "airline_icao": airline_icao,
            "airline_iata": airline_iata,
            "origin_iata":  origin_iata,
            "origin_lat":   origin_coords.get("lat"),
            "origin_lon":   origin_coords.get("lon"),
            "dest_iata":    dest_iata,
            "dest_lat":     dest_coords.get("lat"),
            "dest_lon":     dest_coords.get("lon"),
            "plane_type":   data.get("aircraft_icao", ""),
            # Prefer Unix timestamps, fall back to string conversion
            "time_scheduled_departure": data.get("dep_time_ts") or _to_unix(data.get("dep_time")),
            "time_scheduled_arrival":   data.get("arr_time_ts") or _to_unix(data.get("arr_time")),
            "time_real_departure":      data.get("dep_actual_ts") or data.get("dep_estimated_ts") or _to_unix(data.get("dep_actual")),
            "time_estimated_arrival":   data.get("arr_estimated_ts") or data.get("arr_actual_ts") or _to_unix(data.get("arr_estimated")) or data.get("arr_time_ts"),
        }

        return result

    except Exception as e:
        print(f"[AirLabs] {callsign}: error — {e}")
        return {}


def get_tracked_flight(callsign):
    """
    Search for a specific tracked flight globally.
    Returns enriched dict or None if not found/airborne.
    """
    if not is_available():
        return None

    try:
        r = requests.get(
            f"{BASE_URL}/flight",
            params={"flight_icao": callsign, "api_key": AIRLABS_API_KEY},
            timeout=10,
        )
        if r.status_code != 200:
            return None

        data = r.json().get("response") or {}
        if not data:
            return None

        _increment_usage()

        lat = data.get("lat")
        lon = data.get("lng")
        has_position = lat is not None and lon is not None

        cs_iata      = data.get("cs_airline_iata")
        airline_iata = cs_iata or data.get("airline_iata", "")
        airline_name = _airline_name(airline_iata) or data.get("airline_name", "")

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
            "time_scheduled_departure": data.get("dep_time_ts"),
            "time_scheduled_arrival":   data.get("arr_time_ts"),
            "time_real_departure":      data.get("dep_actual_ts") or data.get("dep_estimated_ts"),
            "time_estimated_arrival":   data.get("arr_estimated_ts") or data.get("arr_actual_ts"),
        }

    except Exception as e:
        print(f"[AirLabs] tracked error for {callsign}: {e}")
        return None
