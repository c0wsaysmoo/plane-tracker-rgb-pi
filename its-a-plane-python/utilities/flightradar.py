"""
flightradar.py — Route lookup via FlightRadar24 API.
Uses flight-summary/light endpoint — 1 credit per live flight.
Requires a paid FR24 subscription.

Set in config.py:
    FLIGHTRADAR24_KEY = "your-key-here"
"""

import json
import os
import requests
from datetime import datetime, timezone, timedelta
from time import time

try:
    from config import FLIGHTRADAR24_KEY as FR24_API_KEY
except (ImportError, ModuleNotFoundError, NameError):
    try:
        from config import FR24_API_KEY
    except (ImportError, ModuleNotFoundError, NameError):
        FR24_API_KEY = None

BASE_URL = "https://fr24api.flightradar24.com/api"

from utilities.airports import get_airport_coords as _airport_coords
from utilities.airports import icao_to_iata as _icao_to_iata
from utilities.airlines import get_airline_name as _lookup_airline

_cache     = {}
CACHE_TTL  = 3600  # 1 hour


def _get_airline_name(icao, iata="", operator=""):
    if operator and len(operator) > 4 and not operator.isupper():
        return operator
    return _lookup_airline(icao) or _lookup_airline(iata) or icao or ""


def _to_unix(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(str(dt_str).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def is_available():
    return bool(FR24_API_KEY)


class FR24Client:

    def __init__(self):
        pass

    @property
    def ok(self):
        return bool(FR24_API_KEY)

    def get_flight_details(self, callsign, plane_lat, plane_lon,
                           plane_type="", registration="", distance=0.0):
        if not FR24_API_KEY:
            return {}

        now = time()
        cached = _cache.get(callsign)
        if cached and (now - cached["ts"]) < CACHE_TTL:
            return cached["data"]

        try:
            # Use a 12-hour window around now to catch the current flight
            now_utc   = datetime.now(timezone.utc)
            from_utc  = (now_utc - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
            to_utc    = (now_utc + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")

            r = requests.get(
                f"{BASE_URL}/flight-summary/light",
                headers={
                    "Accept": "application/json",
                    "Accept-Version": "v1",
                    "Authorization": f"Bearer {FR24_API_KEY}",
                },
                params={
                    "callsigns":            callsign,
                    "flight_datetime_from": from_utc,
                    "flight_datetime_to":   to_utc,
                    "limit":                5,
                    "sort":                 "desc",
                },
                timeout=10,
            )

            if r.status_code != 200:
                return {}

            flights = r.json().get("data", [])
            if not flights:
                _cache[callsign] = {"data": {}, "ts": now}
                return {}

            # Prefer live flight (flight_ended=false), then most recent
            f = (
                next((fl for fl in flights if not fl.get("flight_ended", True)), None)
                or flights[0]
            )

            # light endpoint only has ICAO codes — convert to IATA via airports database
            origin_icao = f.get("orig_icao", "")
            dest_icao   = f.get("dest_icao_actual", "") or f.get("dest_icao", "")

            origin      = _icao_to_iata(origin_icao) if origin_icao else "?"
            destination = _icao_to_iata(dest_icao)   if dest_icao   else "?"

            painted_as   = f.get("painted_as", "")
            operating_as = f.get("operating_as", "") or f.get("operated_as", "")
            airline_name = _get_airline_name(painted_as, "", painted_as)

            origin_coords = _airport_coords(origin) or _airport_coords(origin_icao)
            dest_coords   = _airport_coords(destination) or _airport_coords(dest_icao)

            takeoff_ts = _to_unix(f.get("datetime_takeoff"))
            landed_ts  = _to_unix(f.get("datetime_landed"))


            result = {
                "airline_name": airline_name,
                "airline_icao": operating_as,
                "airline_iata": "",
                "origin_iata":  origin,
                "origin_lat":   origin_coords.get("lat"),
                "origin_lon":   origin_coords.get("lon"),
                "dest_iata":    destination,
                "dest_lat":     dest_coords.get("lat"),
                "dest_lon":     dest_coords.get("lon"),
                "plane_type":   f.get("type", plane_type),
                "time_scheduled_departure": takeoff_ts,
                "time_scheduled_arrival":   landed_ts,
                "time_real_departure":      takeoff_ts,
                "time_estimated_arrival":   landed_ts,
                "trail":        [],
            }

            _cache[callsign] = {"data": result, "ts": now}
            return result

        except Exception as e:
            print(f"[FR24] {callsign}: error — {e}")
            return {}

    def get_tracked_flight(self, callsign):
        if not FR24_API_KEY:
            return None

        result = self.get_flight_details(callsign, None, None)
        if not result or result.get("origin_iata") in ("?", "", None):
            return None

        return {
            "callsign":      callsign,
            "number":        callsign,
            "airline_name":  result.get("airline_name", ""),
            "is_live":       True,
            "origin":        result.get("origin_iata", ""),
            "destination":   result.get("dest_iata", ""),
            "dest_lat":      result.get("dest_lat"),
            "dest_lon":      result.get("dest_lon"),
            "aircraft_type": result.get("plane_type", ""),
            "altitude":      0,
            "ground_speed":  0,
            "heading":       0,
            "latitude":      None,
            "longitude":     None,
            "time_scheduled_departure": result.get("time_scheduled_departure"),
            "time_scheduled_arrival":   result.get("time_scheduled_arrival"),
            "time_real_departure":      result.get("time_real_departure"),
            "time_estimated_arrival":   result.get("time_estimated_arrival"),
        }
