import os
import json
import math
import logging
from time import sleep, time
from threading import Thread, Lock

from utilities.fr24_client import FR24Client, LiveFlight
from httpx import ConnectError, TimeoutException

logger = logging.getLogger(__name__)

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

# Writable data directory — outside home dir to avoid systemd ProtectHome issues
DATA_DIR = os.environ.get("PLANE_TRACKER_DATA_DIR", "/var/lib/plane-tracker")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_FILE = os.path.join(DATA_DIR, "close.txt")
LOG_FILE_FARTHEST = os.path.join(DATA_DIR, "farthest.txt")
TRACKED_FILE = os.path.join(DATA_DIR, "tracked_flight.json")
MAPS_DIR = os.path.join(DATA_DIR, "maps")
os.makedirs(MAPS_DIR, exist_ok=True)


# Utility Functions

def safe_load_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, PermissionError, OSError) as e:
        if isinstance(e, (PermissionError, OSError)):
            logger.warning(f"Permission denied reading {path} — attempting to fix")
            try:
                os.chmod(path, 0o666)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except Exception:
                pass
        return []


def safe_write_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        # Ensure the file is readable/writable by all users
        os.chmod(path, 0o666)
    except PermissionError:
        logger.warning(f"Permission denied writing {path} — attempting to fix")
        try:
            os.chmod(path, 0o666)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e2:
            logger.error(f"Cannot write {path}: {e2}")


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

    # --- Time remaining: subtract elapsed time from last known ---
    last_time_str = data.get("time_remaining", "")
    if last_time_str:
        # Parse "H:MM" or "Mm" format
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

    # --- Distance remaining: subtract distance covered ---
    last_dist = data.get("dist_remaining")
    if last_dist is not None and speed_kts > 0:
        # Convert knots to display units per hour
        if DISTANCE_UNITS == "metric":
            speed_display = speed_kts * 1.852      # knots -> kph
        else:
            speed_display = speed_kts * 1.15078    # knots -> mph
        dist_covered = speed_display * elapsed_hrs
        data["dist_remaining"] = max(0, last_dist - dist_covered)

    # --- Update progress bar position using estimated dist_remaining ---
    total = data.get("total_distance")
    if total and total > 0 and data.get("dist_remaining") is not None:
        # total_distance is in display units, dist_remaining now estimated
        pass  # progress bar uses dist_remaining/total_distance automatically

    return data


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
        self._api = FR24Client()
        self._lock = Lock()
        self._data = []           # overhead flights
        self._tracked_data = None # tracked flight or None
        self._new_data = False
        self._processing = False
        self._tracked_was_live = False       # was the flight live last poll?
        self._tracked_miss_count = 0         # consecutive polls with no result
        self._TRACKED_MISS_THRESHOLD = 3     # fallback miss threshold (no ETA)
        self._tracked_last_callsign = ""     # last callsign we polled for
        self._tracked_last_eta = None        # last known estimated arrival (unix ts)
        self._tracked_last_data = None       # last known good tracked data
        self._first_flight_logged = False    # log first flight details as JSON

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
            flights = self._api.get_flights(bounds=ZONE_DEFAULT)
            logger.info(f"Overhead: {len(flights)} flights in zone (before altitude filter)")
            flights = [f for f in flights if MIN_ALTITUDE < f.altitude < MAX_ALTITUDE]
            logger.info(f"Overhead: {len(flights)} flights after altitude filter ({MIN_ALTITUDE}-{MAX_ALTITUDE}ft)")
            flights.sort(key=lambda f: distance_from_flight_to_home(f))
            flights = flights[:MAX_FLIGHT_LOOKUP]

            for f in flights:
                retries = RETRIES
                while retries:
                    sleep(RATE_LIMIT_DELAY)
                    try:
                        d = self._api.get_flight_details(f)

                        if not d:
                            retries -= 1
                            continue

                        # Log first flight details as pretty JSON for debugging
                        if not self._first_flight_logged:
                            self._first_flight_logged = True
                            logger.info(
                                "First flight API response:\n%s",
                                json.dumps(d, indent=2, default=str),
                            )

                        # Aircraft type from details, fallback to live feed
                        plane = self.safe_get(d, "aircraft", "model", "code", default="") or f.aircraft_code or ""

                        # Airline name from registered_owners (aircraft_info)
                        flight_number = self.safe_get(d, "schedule_info", "flight_number", default="")
                        airline_name = self.safe_get(d, "aircraft_info", "registered_owners", default="")
                        # Livery note: when painted_as_id differs from operated_by_id
                        painted_as_id = self.safe_get(d, "schedule_info", "painted_as_id", default=0) or 0
                        operated_by_id = self.safe_get(d, "schedule_info", "operated_by_id", default=0) or 0
                        has_special_livery = (painted_as_id != 0 and operated_by_id != 0 and painted_as_id != operated_by_id)

                        origin = f.origin_airport_iata or ""
                        destination = f.destination_airport_iata or ""
                        callsign = f.callsign or ""

                        t = self.safe_get(d, "time", default={})
                        time_sched_dep = self.safe_get(t, "scheduled", "departure")
                        time_sched_arr = self.safe_get(t, "scheduled", "arrival")
                        time_real_dep = self.safe_get(t, "real", "departure")
                        time_est_arr = self.safe_get(t, "estimated", "arrival")

                        # Airport coordinates: NOT available from gRPC FlightDetails.
                        # Use flight_progress distances instead (server-calculated, in METERS).
                        fp = self.safe_get(d, "flight_progress") or {}
                        traversed_m = fp.get("traversed_distance", 0) or 0
                        remaining_m = fp.get("remaining_distance", 0) or 0

                        # Convert meters to display units (miles or km)
                        if DISTANCE_UNITS == "metric":
                            dist_o = traversed_m / 1000.0   # meters → km
                            dist_d = remaining_m / 1000.0
                        else:
                            dist_o = traversed_m / 1609.344  # meters → miles
                            dist_d = remaining_m / 1609.344

                        origin_lat = None
                        origin_lon = None
                        dest_lat = None
                        dest_lon = None

                        # Extract airborne trail points only (alt > 0)
                        raw_trail = self.safe_get(d, "trail", default=[]) or []
                        trail = [
                            [pt["lat"], pt["lng"]]
                            for pt in raw_trail
                            if isinstance(pt, dict) and pt.get("alt", 0) > 0
                        ]

                        # Determine livery note text (only if special and short)
                        livery_note = ""
                        if has_special_livery and airline_name:
                            # If owner differs from the ICAO-implied airline, it's a livery note
                            livery_note = "special livery"

                        entry = {
                            "airline": airline_name,
                            "plane": plane,
                            "flight_number": flight_number,
                            "origin": origin,
                            "origin_latitude": origin_lat,
                            "origin_longitude": origin_lon,
                            "destination": destination,
                            "destination_latitude": dest_lat,
                            "destination_longitude": dest_lon,
                            "plane_latitude": f.latitude,
                            "plane_longitude": f.longitude,
                            "owner_iata": f.airline_iata or "N/A",
                            "owner_icao": f.airline_icao or "",
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
                            "trail": trail,
                            "livery_note": livery_note,
                        }

                        overhead_data.append(entry)
                        log_flight_data(entry)
                        log_farthest_flight(entry)
                        break

                    except Exception as e:
                        retries -= 1
                        if retries == 0:
                            logger.warning(f"Failed to get details for {f.callsign}: {e}")

            # --- STEP 2: Only look for tracked flight if sky is clear ---
            if not overhead_data:
                tracked_callsign = load_tracked_callsign()
                if tracked_callsign:

                    # If callsign changed, reset all state — new flight being tracked
                    if tracked_callsign != self._tracked_last_callsign:
                        self._tracked_last_callsign = tracked_callsign
                        self._tracked_was_live = False
                        self._tracked_miss_count = 0
                        self._tracked_last_eta = None
                        self._tracked_last_data = None

                    tracked_data = self._grab_tracked(tracked_callsign)

                    if tracked_data:
                        # Flight found — reset miss counter, store latest ETA and data
                        self._tracked_was_live = True
                        self._tracked_miss_count = 0
                        self._tracked_last_eta = tracked_data.get("time_estimated_arrival")
                        self._tracked_last_data = tracked_data
                    else:
                        if self._tracked_was_live:
                            # Was live before, now missing
                            now_ts = time()
                            eta    = self._tracked_last_eta

                            if eta is not None:
                                mins_since_eta = (now_ts - eta) / 60
                                if mins_since_eta > 0:
                                    # ETA has passed — use miss counter to confirm
                                    # before wiping (avoids false wipe on brief API hiccup)
                                    self._tracked_miss_count += 1
                                    if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                        self._do_auto_wipe()
                                    elif self._tracked_last_data:
                                        tracked_data = estimate_stale_data(self._tracked_last_data)
                                else:
                                    # ETA still in future — oceanic gap, serve estimated data
                                    self._tracked_miss_count = 0
                                    if self._tracked_last_data:
                                        tracked_data = estimate_stale_data(self._tracked_last_data)
                            else:
                                # No ETA data — fall back to miss counter
                                self._tracked_miss_count += 1
                                if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                    self._do_auto_wipe()
                                elif self._tracked_last_data:
                                    tracked_data = estimate_stale_data(self._tracked_last_data)
                        # If never live, don't increment — pre-flight waiting

            with self._lock:
                self._data = overhead_data
                self._tracked_data = tracked_data
                self._new_data = True
                self._processing = False

        except (ConnectionError, ConnectError, TimeoutException, OSError) as e:
            logger.warning(f"Overhead: Network error during _grab: {type(e).__name__}: {e}")
            with self._lock:
                self._new_data = False
                self._processing = False
        except Exception as e:
            logger.error(f"Overhead: Unexpected error in _grab: {type(e).__name__}: {e}", exc_info=True)
            with self._lock:
                self._new_data = False
                self._processing = False

    def _do_auto_wipe(self):
        """Wipe tracked_flight.json and reset all tracking state."""
        try:
            with open(TRACKED_FILE, "w", encoding="utf-8") as f:
                import json as _json
                _json.dump({"callsign": ""}, f)
            print("Tracked flight ended — auto-cleared.")
        except Exception as e:
            print(f"Failed to auto-clear tracked flight: {e}")
        self._tracked_was_live = False
        self._tracked_miss_count = 0
        self._tracked_last_eta = None
        self._tracked_last_data = None
        self._tracked_last_callsign = ""

    def _grab_tracked(self, flight_input):
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

            # Strategy 2: global live feed search matching on callsign
            # The official API doesn't support airline filtering server-side,
            # so we fetch the full live feed (up to 2000) and filter locally.
            if not match:
                flights = self._api.get_flights()
                match = next(
                    (f for f in flights
                     if (f.callsign or "").upper() == flight_input),
                    None,
                )

            if not match:
                return None

            sleep(RATE_LIMIT_DELAY)
            flight_details = self._api.get_flight_details(match)
            match.set_flight_details(flight_details)

            # Use flight_progress from the API for distances (in METERS)
            fp = self.safe_get(flight_details, "flight_progress") or {}
            remaining_m = fp.get("remaining_distance", 0) or 0
            total_m = fp.get("great_circle_distance", 0) or 0
            eta = fp.get("eta", 0) or 0

            # Convert meters to display units
            if DISTANCE_UNITS == "metric":
                dist_remaining = (remaining_m / 1000.0) if remaining_m else None  # → km
                total_distance = (total_m / 1000.0) if total_m else None
            else:
                dist_remaining = (remaining_m / 1609.344) if remaining_m else None  # → miles
                total_distance = (total_m / 1609.344) if total_m else None

            # Calculate time remaining from ETA
            time_remaining = None
            if eta and eta > time():
                mins_left = int((eta - time()) / 60)
                if mins_left > 0:
                    h = mins_left // 60
                    m = mins_left % 60
                    time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"
            # Fallback: use remaining_time from flight_progress (seconds)
            if not time_remaining:
                remaining_secs = fp.get("remaining_time", 0) or 0
                if remaining_secs > 0:
                    mins_left = remaining_secs // 60
                    h = mins_left // 60
                    m = mins_left % 60
                    time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"
            # Last fallback: distance/speed
            if not time_remaining and dist_remaining and match.ground_speed:
                if DISTANCE_UNITS == "metric":
                    dist_nm = dist_remaining * 0.539957
                else:
                    dist_nm = dist_remaining * 0.868976
                hrs_left = dist_nm / match.ground_speed
                mins_left = int(hrs_left * 60)
                if mins_left > 0:
                    h = mins_left // 60
                    m = mins_left % 60
                    time_remaining = f"{h}:{m:02d}" if h > 0 else f"{m}m"

            # Time fields for delay colour coding
            time_details = self.safe_get(flight_details, "time") or {}
            time_sched_dep = self.safe_get(time_details, "scheduled", "departure")
            time_sched_arr = self.safe_get(time_details, "scheduled", "arrival")
            time_real_dep  = self.safe_get(time_details, "real", "departure")
            time_est_arr   = self.safe_get(time_details, "estimated", "arrival") or eta or None

            return {
                "callsign": flight_input,
                "number": match.number or flight_input,
                "airline_name": match.airline_name or "",
                "is_live": True,
                "origin": match.origin_airport_iata or "",
                "destination": match.destination_airport_iata or "",
                "dest_lat": 0,
                "dest_lon": 0,
                "aircraft_type": match.aircraft_code or "",
                "altitude": match.altitude,
                "ground_speed": match.ground_speed or 0,
                "heading": match.heading or 0,
                "dist_remaining": dist_remaining,
                "total_distance": total_distance,
                "time_remaining": time_remaining,
                "latitude": match.latitude,
                "longitude": match.longitude,
                "last_seen_ts": time(),
                "vertical_speed": match.vertical_speed or 0,
                "time_scheduled_departure": time_sched_dep,
                "time_scheduled_arrival": time_sched_arr,
                "time_real_departure": time_real_dep,
                "time_estimated_arrival": time_est_arr,
            }

        except Exception as e:
            logger.error(f"Failed to grab tracked flight: {e}")
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
