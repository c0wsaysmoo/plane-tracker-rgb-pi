import os
import json
import math
import socket
from time import sleep
from threading import Thread, Lock
from datetime import datetime
from typing import Optional, Tuple

from FlightRadar24.api import FlightRadar24API
from requests.exceptions import ConnectionError
from urllib3.exceptions import NewConnectionError, MaxRetryError

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

# Constants

RETRIES = 3
RATE_LIMIT_DELAY = 1
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 100000
EARTH_RADIUS_M = 3958.8
BLANK_FIELDS = ["", "N/A", "NONE"]

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LOG_FILE = os.path.join(BASE_DIR, "close.txt")
LOG_FILE_FARTHEST = os.path.join(BASE_DIR, "farthest.txt")

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
    """Internal helper for distance."""
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

# Distance wrappers

def distance_from_flight_to_home(flight):
    return haversine(
        flight.latitude, flight.longitude,
        LOCATION_DEFAULT[0], LOCATION_DEFAULT[1],
    )


def distance_to_point(flight, lat, lon):
    return haversine(flight.latitude, flight.longitude, lat, lon)

# Logging Closest Flights

def log_flight_data(entry: dict):
    """Track top-N closest flights and email only when NEW enters top-N."""
    try:
        entry["timestamp"] = email_alerts.get_timestamp()
        lst = safe_load_json(LOG_FILE)

        callsigns = {f.get("callsign"): f for f in lst}
        new_call = entry.get("callsign")
        new_dist = entry.get("distance", float("inf"))
        notify = False

        # Existing ? update if better
        if new_call in callsigns:
            idx = next(i for i, f in enumerate(lst) if f.get("callsign") == new_call)
            if new_dist < lst[idx].get("distance", float("inf")):
                lst[idx] = entry
            else:
                return
        else:
            lst.append(entry)

        # Sorting by closest
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

# Logging Farthest Flights

def log_farthest_flight(entry: dict):
    """Track farthest airports (origin or destination)."""
    try:
        d_o = entry.get("distance_origin", -1)
        d_d = entry.get("distance_destination", -1)

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
            # Only update if "distance" improved
            if entry["distance"] < existing.get("distance", 9e9):
                lst = [entry if f["_airport"] == airport else f for f in lst]
                updated = True
            else:
                return
        else:
            # New airport entering top-N
            if len(lst) >= MAX_FARTHEST:
                if far <= min(f["farthest_value"] for f in lst):
                    return
            lst.append(entry)
            notify = True
            
        lst.sort(key=lambda x: x["farthest_value"], reverse=True)
        lst = lst[:MAX_FARTHEST]
        safe_write_json(LOG_FILE_FARTHEST, lst)

        # --- ALWAYS generate local map for notify OR updated ---
        if notify or updated:
            html = map_generator.generate_farthest_map(lst, filename="farthest.html")

        # --- ONLY upload + email if this is a NEW airport ---
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
        print("Failed to log farthest flight:", e)


# Overhead Class

class Overhead:
    def __init__(self):
        self._api = FlightRadar24API()
        self._lock = Lock()
        self._data = []
        self._new_data = False
        self._processing = False

    # Public
    def grab_data(self):
        Thread(target=self._grab).start()

    # Safe nested dict access
    def safe_get(self, d, *keys, default=None):
        """Safely get nested dictionary values."""
        for key in keys:
            if not d or not isinstance(d, dict):
                return default
            d = d.get(key)
        return d if d is not None else default

    def _grab(self):
        with self._lock:
            self._new_data = False
            self._processing = True

        data = []

        try:
            bounds = self._api.get_bounds(ZONE_DEFAULT)
            flights = self._api.get_flights(bounds=bounds)

            # Altitude filter
            flights = [f for f in flights if MIN_ALTITUDE < f.altitude < MAX_ALTITUDE]

            # Sort & slice
            flights.sort(key=lambda f: distance_from_flight_to_home(f))
            flights = flights[:MAX_FLIGHT_LOOKUP]

            for f in flights:
                retries = RETRIES
                while retries:
                    sleep(RATE_LIMIT_DELAY)
                    try:
                        d = self._api.get_flight_details(f)

                        # Extract fields
                        plane = self.safe_get(d, "aircraft", "model", "code", default="") or f.airline_icao or ""
                        airline = self.safe_get(d, "airline", "name", default="")

                        def clean_code(val):
                            if not val or str(val).upper() in BLANK_FIELDS:
                                return ""
                            return val

                        origin = clean_code(f.origin_airport_iata)
                        destination = clean_code(f.destination_airport_iata)

                        callsign = f.callsign or ""

                        # Times
                        t = self.safe_get(d, "time", default={})
                        time_sched_dep = self.safe_get(t, "scheduled", "departure")
                        time_sched_arr = self.safe_get(t, "scheduled", "arrival")
                        time_real_dep = self.safe_get(t, "real", "departure")
                        time_est_arr = self.safe_get(t, "estimated", "arrival")

                        # Airport coordinates
                        o = self.safe_get(d, "airport", "origin")
                        origin_lat = self.safe_get(o, "position", "latitude")
                        origin_lon = self.safe_get(o, "position", "longitude")

                        dest = self.safe_get(d, "airport", "destination")
                        dest_lat = self.safe_get(dest, "position", "latitude")
                        dest_lon = self.safe_get(dest, "position", "longitude")

                        dist_o = distance_to_point(f, origin_lat, origin_lon) if origin_lat else 0
                        dist_d = distance_to_point(f, dest_lat, dest_lon) if dest_lat else 0

                        entry = {
                            "airline": airline,
                            "plane": plane,
                            "origin": origin,
                            "origin_latitude": origin_lat,
                            "origin_longitude": origin_lon,
                            "destination": destination,
                            "destination_latitude": dest_lat,
                            "destination_longitude": dest_lon,
                            "plane_latitude": f.latitude,
                            "plane_longitude": f.longitude,

                            "owner_iata": f.airline_iata or "N/A",
                            "owner_icao": self.safe_get(d, "owner", "code", "icao", default="") or f.airline_icao or "",

                            "time_scheduled_departure": time_sched_dep,
                            "time_scheduled_arrival": time_sched_arr,
                            "time_real_departure": time_real_dep,
                            "time_estimated_arrival": time_est_arr,

                            "vertical_speed": f.vertical_speed,
                            "callsign": callsign,

                            "distance_origin": dist_o,
                            "distance_destination": dist_d,
                            "distance": distance_from_flight_to_home(f),
                            "direction": degrees_to_cardinal(plane_bearing(f)),
                        }

                        data.append(entry)

                        # Log flights
                        log_flight_data(entry)
                        log_farthest_flight(entry)

                        break

                    except Exception as e:
                        retries -= 1

            with self._lock:
                self._new_data = True
                self._processing = False
                self._data = data

        except (ConnectionError, NewConnectionError, MaxRetryError):
            with self._lock:
                self._new_data = False
                self._processing = False
                
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
    def data_is_empty(self):
        return len(self._data) == 0
        
# Main

if __name__ == "__main__":
    o = Overhead()
    o.grab_data()

    while not o.new_data:
        print("processing...")
        sleep(1)

    print(o.data)

