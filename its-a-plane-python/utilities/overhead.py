import os
import json
import math
from time import sleep, time
from threading import Thread, Lock

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
TRACKED_FILE = os.path.join(BASE_DIR, "tracked_flight.json")


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


def distance_to_point(flight, lat, lon):
    return haversine(flight.latitude, flight.longitude, lat, lon)



def load_tracked_callsign():
    """Read the tracked callsign from tracked_flight.json."""
    try:
        with open(TRACKED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("callsign", "").strip().upper()
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


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
            if entry["distance"] < existing.get("distance", 9e9):
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
        print("Failed to log farthest flight:", e)


# Overhead Class

class Overhead:
    def __init__(self):
        self._api = FlightRadar24API()
        self._lock = Lock()
        self._data = []           # overhead flights
        self._tracked_data = None # tracked flight or None
        self._new_data = False
        self._processing = False
        self._tracked_was_live = False       # was the flight live last poll?
        self._tracked_miss_count = 0         # consecutive polls with no result
        self._TRACKED_MISS_THRESHOLD = 3     # misses before auto-wipe
        self._tracked_last_callsign = ""     # last callsign we polled for

    def grab_data(self):
        Thread(target=self._grab, daemon=True).start()

    def safe_get(self, d, *keys, default=None):
        for key in keys:
            if not d or not isinstance(d, dict):
                return default
            d = d.get(key)
        return d if d is not None else default

    def _grab(self):
        with self._lock:
            self._new_data = False
            self._processing = True

        overhead_data = []
        tracked_data = None

        try:
            # --- STEP 1: Check zone for overhead flights ---
            bounds = self._api.get_bounds(ZONE_DEFAULT)
            flights = self._api.get_flights(bounds=bounds)
            flights = [f for f in flights if MIN_ALTITUDE < f.altitude < MAX_ALTITUDE]
            flights.sort(key=lambda f: distance_from_flight_to_home(f))
            flights = flights[:MAX_FLIGHT_LOOKUP]

            for f in flights:
                retries = RETRIES
                while retries:
                    sleep(RATE_LIMIT_DELAY)
                    try:
                        d = self._api.get_flight_details(f)

                        plane = self.safe_get(d, "aircraft", "model", "code", default="") or f.airline_icao or ""
                        airline = self.safe_get(d, "airline", "name", default="")

                        origin = f.origin_airport_iata or ""
                        destination = f.destination_airport_iata or ""
                        callsign = f.callsign or ""

                        t = self.safe_get(d, "time", default={})
                        time_sched_dep = self.safe_get(t, "scheduled", "departure")
                        time_sched_arr = self.safe_get(t, "scheduled", "arrival")
                        time_real_dep = self.safe_get(t, "real", "departure")
                        time_est_arr = self.safe_get(t, "estimated", "arrival")

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

                        overhead_data.append(entry)
                        log_flight_data(entry)
                        log_farthest_flight(entry)
                        break

                    except Exception as e:
                        retries -= 1

            # --- STEP 2: Only look for tracked flight if sky is clear ---
            if not overhead_data:
                tracked_callsign = load_tracked_callsign()
                if tracked_callsign:

                    # If callsign changed, reset all state — new flight being tracked
                    if tracked_callsign != self._tracked_last_callsign:
                        self._tracked_last_callsign = tracked_callsign
                        self._tracked_was_live = False
                        self._tracked_miss_count = 0

                    tracked_data = self._grab_tracked(tracked_callsign)

                    if tracked_data:
                        # Flight found — reset miss counter
                        self._tracked_was_live = True
                        self._tracked_miss_count = 0
                    else:
                        if self._tracked_was_live:
                            # Was live before, now missing — increment miss counter
                            self._tracked_miss_count += 1
                            if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                # Flight has landed — auto-wipe tracked_flight.json
                                try:
                                    with open(TRACKED_FILE, "w", encoding="utf-8") as f:
                                        import json as _json
                                        _json.dump({"callsign": ""}, f)
                                    print("Tracked flight landed — auto-cleared.")
                                except Exception as e:
                                    print(f"Failed to auto-clear tracked flight: {e}")
                                self._tracked_was_live = False
                                self._tracked_miss_count = 0
                        # If it was never live, miss count stays at 0 (pre-flight)

            with self._lock:
                self._data = overhead_data
                self._tracked_data = tracked_data
                self._new_data = True
                self._processing = False

        except (ConnectionError, NewConnectionError, MaxRetryError):
            with self._lock:
                self._new_data = False
                self._processing = False

    def _grab_tracked(self, flight_input):
        """
        Search for a tracked flight by flight number or callsign.
        Accepts IATA flight number (AA5056), ICAO callsign (AAL1583),
        or operator callsign (JIA5056).
        Matches on f.number first (catches regionals), then f.callsign.
        """
        flight_input = flight_input.strip().upper()
        airline_icao = flight_input[:3] if len(flight_input) >= 3 and flight_input[:3].isalpha() else None
        match = None

        try:
            # Strategy 1: airline-filtered search by callsign (fast, works for mainline)
            if airline_icao:
                flights = self._api.get_flights(airline=airline_icao)
                match = next(
                    (f for f in flights if (f.callsign or "").upper() == flight_input),
                    None,
                )

            # Strategy 2: high-limit global search matching on flight NUMBER
            # This catches regionals where callsign differs from flight number
            # e.g. user enters AA5056, actual callsign is JIA5056
            # but f.number == "AA5056" on both
            if not match:
                config = self._api.get_flight_tracker_config()
                config.limit = 10000
                self._api.set_flight_tracker_config(config)
                flights = self._api.get_flights()
                # Reset limit back to default
                config.limit = 1500
                self._api.set_flight_tracker_config(config)
                match = next(
                    (f for f in flights if
                     (f.number or "").upper() == flight_input or
                     (f.callsign or "").upper() == flight_input),
                    None,
                )

            if not match:
                return None

            sleep(RATE_LIMIT_DELAY)
            flight_details = self._api.get_flight_details(match)
            match.set_flight_details(flight_details)

            # Calculate time remaining from estimated arrival
            time_remaining = None
            est_arr = self.safe_get(flight_details, "time", "estimated", "arrival")
            if est_arr:
                mins_left = int((est_arr - time()) / 60)
                if mins_left > 0:
                    h = mins_left // 60
                    m = mins_left % 60
                    time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"

            origin_lat = match.origin_airport_latitude
            origin_lon = match.origin_airport_longitude
            dest_lat = match.destination_airport_latitude
            dest_lon = match.destination_airport_longitude

            dist_remaining = (
                haversine(match.latitude, match.longitude, dest_lat, dest_lon)
                if dest_lat else None
            )

            # Total route distance origin -> destination for progress bar
            total_distance = (
                haversine(origin_lat, origin_lon, dest_lat, dest_lon)
                if origin_lat and dest_lat else None
            )

            # Time fields for delay colour coding (same as JourneyScene)
            time_details = self.safe_get(flight_details, "time") or {}
            time_sched_dep = self.safe_get(time_details, "scheduled", "departure")
            time_sched_arr = self.safe_get(time_details, "scheduled", "arrival")
            time_real_dep  = self.safe_get(time_details, "real", "departure")
            time_est_arr   = self.safe_get(time_details, "estimated", "arrival")

            return {
                "callsign": flight_input,
                "number": match.number or flight_input,
                "origin": match.origin_airport_iata or "",
                "destination": match.destination_airport_iata or "",
                "aircraft_type": match.aircraft_code or "",
                "altitude": match.altitude,
                "ground_speed": match.ground_speed or 0,
                "dist_remaining": dist_remaining,
                "total_distance": total_distance,
                "time_remaining": time_remaining,
                "latitude": match.latitude,
                "longitude": match.longitude,
                "vertical_speed": match.vertical_speed or 0,
                "time_scheduled_departure": time_sched_dep,
                "time_scheduled_arrival": time_sched_arr,
                "time_real_departure": time_real_dep,
                "time_estimated_arrival": time_est_arr,
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
        return len(self._data) == 0


if __name__ == "__main__":
    o = Overhead()
    o.grab_data()
    while not o.new_data:
        print("processing...")
        sleep(1)
    print("Overhead:", o.data)
    print("Tracked:", o.tracked_data)
