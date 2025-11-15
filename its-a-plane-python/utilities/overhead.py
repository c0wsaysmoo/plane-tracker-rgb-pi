from FlightRadar24.api import FlightRadar24API
from threading import Thread, Lock
from time import sleep
import math
from typing import Optional, Tuple
from config import DISTANCE_UNITS, CLOCK_FORMAT, MAX_FARTHEST
import os, json, socket
from datetime import datetime
from requests.exceptions import ConnectionError
from urllib3.exceptions import NewConnectionError
from urllib3.exceptions import MaxRetryError
from setup import email_alerts


try:
    # Attempt to load config data
    from config import MIN_ALTITUDE

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    MIN_ALTITUDE = 0  # feet

RETRIES = 3
RATE_LIMIT_DELAY = 1
MAX_FLIGHT_LOOKUP = 5
MAX_ALTITUDE = 100000  # feet
EARTH_RADIUS_M = 3958.8  # Earth's radius in miles
BLANK_FIELDS = ["", "N/A", "NONE"]

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

LOG_FILE = os.path.join(BASE_DIR, "close.txt")
LOG_FILE_FARTHEST = os.path.join(BASE_DIR, "farthest.txt")

def log_flight_data(entry: dict):
    """Log only a new closest flight and send email alert."""
    try:
        entry["timestamp"] = email_alerts.get_timestamp()

        # Load previous closest flight
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                current = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            current = None

        new_d = entry.get("distance", float("inf"))
        old_d = current.get("distance", float("inf")) if current else float("inf")

        if new_d < old_d:
            # Save new closest flight
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=4)

            subject = f"New Closest Flight: {entry.get('callsign','Unknown')}"
            email_alerts.send_flight_summary(subject, entry)

    except Exception as e:
        print("Failed to log closest flight:", e)

def log_farthest_flight(entry: dict):
    """
    Track farthest-airport flights.

    Rules:
      1) The "farthest airport" is whichever of origin/destination is farthest from home.
      2) For the same airport:
            - KEEP the flight closest to me (distance)
      3) If a different airport:
            - Only store it if its farthest_value is farther than at least one in list
      4) Email only when:
            - A new airport enters the list
    """
    try:
        d_o = entry.get("distance_origin", -1)
        d_d = entry.get("distance_destination", -1)

        if d_o < 0 and d_d < 0:
            return

        # Pick the farthest airport for this entry
        if d_o >= d_d:
            far = d_o
            airport = entry.get("origin")
            reason = "origin"
        else:
            far = d_d
            airport = entry.get("destination")
            reason = "destination"

        if not airport:
            return

        # Attach computed info
        entry["timestamp"] = email_alerts.get_timestamp()
        entry["reason"] = reason
        entry["farthest_value"] = far
        entry["_airport"] = airport

        new_dist_me = entry.get("distance", 9e9)

        # Load existing farthest list
        try:
            with open(LOG_FILE_FARTHEST, "r", encoding="utf-8") as f:
                lst = json.load(f)
                lst = lst if isinstance(lst, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            lst = []

        airport_map = {f.get("_airport"): f for f in lst}
        existing = airport_map.get(airport)

        notify = False

        # --- Case A: Already have same airport ---
        if existing:
            old_dist_me = existing.get("distance", 9e9)

            # Only replace if closer to me
            if new_dist_me < old_dist_me:
                for i, f in enumerate(lst):
                    if f.get("_airport") == airport:
                        lst[i] = entry
                        break
            else:
                return  # Not closer ? ignore

        # --- Case B: New airport ---
        else:
            # If list full, must outrank at least one
            if len(lst) >= MAX_FARTHEST:
                min_far = min(f.get("farthest_value", 0) for f in lst)
                if far <= min_far:
                    return
            lst.append(entry)
            notify = True  # Only new airport triggers email

        # Sort & trim
        lst.sort(key=lambda x: x.get("farthest_value", 0), reverse=True)
        lst = lst[:MAX_FARTHEST]

        # Save updated list
        with open(LOG_FILE_FARTHEST, "w", encoding="utf-8") as f:
            json.dump(lst, f, indent=4)

        # Only send email for new airports
        if not notify:
            return

        callsign = entry.get("callsign", "UNKNOWN")
        try:
            rank = next(idx for idx, f in enumerate(lst) if f.get("_airport") == airport) + 1
        except StopIteration:
            return

        def ordinal(n):
            return f"{n}{'tsnrhtdd'[(n//10%10!=1)*(n%10<4)*n%10::4]}"

        rank_txt = ordinal(rank)
        if rank == 1:
            subject = f"New Farthest Flight ({reason}) - {callsign}"
        else:
            subject = f"{rank_txt}-Farthest Flight ({reason}) - {callsign}"

        email_alerts.send_flight_summary(subject, entry, reason)

    except Exception as e:
        print("Failed to log farthest flight:", e)


        
try:
    # Attempt to load config data
    from config import ZONE_HOME, LOCATION_HOME

    ZONE_DEFAULT = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    ZONE_DEFAULT = {"tl_y": 62.61, "tl_x": -13.07, "br_y": 49.71, "br_x": 3.46}
    LOCATION_DEFAULT = [51.509865, -0.118092, EARTH_RADIUS_M]
    
def polar_to_cartesian(lat, long, alt):
        DEG2RAD = math.pi / 180
        return [
            alt * math.cos(DEG2RAD * lat) * math.sin(DEG2RAD * long),
            alt * math.sin(DEG2RAD * lat),
            alt * math.cos(DEG2RAD * lat) * math.cos(DEG2RAD * long),
        ]


def distance_from_flight_to_home(flight, home=LOCATION_DEFAULT):
    try:
        # Convert latitude and longitude from degrees to radians
        lat1, lon1 = math.radians(flight.latitude), math.radians(flight.longitude)
        lat2, lon2 = math.radians(home[0]), math.radians(home[1])

        # Differences in coordinates
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        # Haversine formula
        a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        # Haversine distance in miles using the defined Earth radius
        dist_miles = EARTH_RADIUS_M * c

        # Convert distance units if needed
        if DISTANCE_UNITS == "metric":
            dist_km = dist_miles * 1.609  # Convert miles to kilometers
            return dist_km
        else:
            return dist_miles

    except AttributeError:
        # on error say it's far away
        return 1e6
               
def plane_bearing(flight, home=LOCATION_DEFAULT):
  # Convert latitude and longitude to radians
  lat1 = math.radians(home[0])
  long1 = math.radians(home[1])
  lat2 = math.radians(flight.latitude)
  long2 = math.radians(flight.longitude)

  # Calculate the bearing
  bearing = math.atan2(
      math.sin(long2 - long1) * math.cos(lat2),
      math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(long2 - long1)
  )

  # Convert the bearing to degrees
  bearing = math.degrees(bearing)

  # Make sure the bearing is positives
  return (bearing + 360) % 360
  
def degrees_to_cardinal(d):
    '''
    note: this is highly approximate...
    '''
    dirs = ["N", "NE",  "E",  "SE", 
            "S",  "SW",  "W",  "NW",]
    ix = int((d + 22.5)/45)
    return dirs[ix % 8]

def distance_from_flight_to_origin(flight, origin_latitude, origin_longitude, origin_altitude):
    if hasattr(flight, 'latitude') and hasattr(flight, 'longitude') and hasattr(flight, 'altitude'):
        try:
            # Convert latitude and longitude from degrees to radians
            lat1, lon1 = math.radians(flight.latitude), math.radians(flight.longitude)
            lat2, lon2 = math.radians(origin_latitude), math.radians(origin_longitude)

            # Differences in coordinates
            dlat = lat2 - lat1
            dlon = lon2 - lon1

            # Haversine formula
            a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

            # Haversine distance in miles using the defined Earth radius
            dist_miles = EARTH_RADIUS_M * c

            # Convert distance units if needed
            if DISTANCE_UNITS == "metric":
                dist_km = dist_miles * 1.609  # Convert miles to kilometers
                return dist_km
            else:
                return dist_miles
        except Exception as e:
            print("Error:", e)
            return None
    else:
        print("Flight data is missing required attributes: latitude, longitude, or altitude")
        return None

def distance_from_flight_to_destination(flight, destination_latitude, destination_longitude, destination_altitude):
    if hasattr(flight, 'latitude') and hasattr(flight, 'longitude') and hasattr(flight, 'altitude'):
        try:
            # Convert latitude and longitude from degrees to radians
            lat1, lon1 = math.radians(flight.latitude), math.radians(flight.longitude)
            lat2, lon2 = math.radians(destination_latitude), math.radians(destination_longitude)

            # Differences in coordinates
            dlat = lat2 - lat1
            dlon = lon2 - lon1

            # Haversine formula
            a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

            # Haversine distance in miles using the defined Earth radius
            dist_miles = EARTH_RADIUS_M * c

            # Convert distance units if needed
            if DISTANCE_UNITS == "metric":
                dist_km = dist_miles * 1.609  # Convert miles to kilometers
                return dist_km
            else:
                return dist_miles
        except Exception as e:
            print("Error:", e)
            return None
    else:
        print("Flight data is missing required attributes: latitude, longitude, or altitude")
        return None


class Overhead:
    def __init__(self):
        self._api = FlightRadar24API()
        self._lock = Lock()
        self._data = []
        self._new_data = False
        self._processing = False

    def grab_data(self):
        Thread(target=self._grab_data).start()

    def _grab_data(self):
        # Mark data as old
        with self._lock:
            self._new_data = False
            self._processing = True

        data = []

        # Grab flight details
        try:
            bounds = self._api.get_bounds(ZONE_DEFAULT)
            flights = self._api.get_flights(bounds=bounds)

            # Sort flights by closest first
            flights = [
                f
                for f in flights
                if f.altitude < MAX_ALTITUDE and f.altitude > MIN_ALTITUDE
            ]
            flights = sorted(flights, key=lambda f: distance_from_flight_to_home(f))

            for flight in flights[:MAX_FLIGHT_LOOKUP]:
                retries = RETRIES

                while retries:
                    # Rate limit protection
                    sleep(RATE_LIMIT_DELAY)

                    # Grab and store details
                    try:
                        details = self._api.get_flight_details(flight)

                        # Get plane type
                        try:
                            plane = details["aircraft"]["model"]["code"]
                        except (KeyError, TypeError):
                            plane = ""

                        # Tidy up what we pass along
                        plane = plane if not (plane.upper() in BLANK_FIELDS) else ""

                        origin = (
                            flight.origin_airport_iata
                            if not (flight.origin_airport_iata.upper() in BLANK_FIELDS)
                            else ""
                        )

                        destination = (
                            flight.destination_airport_iata
                            if not (flight.destination_airport_iata.upper() in BLANK_FIELDS)
                            else ""
                        )

                        callsign = (
                            flight.callsign
                            if not (flight.callsign.upper() in BLANK_FIELDS)
                            else ""
                        )

                        # Get airline type
                        try:
                            airline = details["airline"]["name"]
                        except (KeyError, TypeError):
                            airline = ""
                            
                        # Get departure and arrival times
                        try:
                            time_scheduled_departure = details["time"]["scheduled"]["departure"]
                            time_scheduled_arrival = details["time"]["scheduled"]["arrival"]
                            time_real_departure = details["time"]["real"]["departure"]
                            time_estimated_arrival = details["time"]["estimated"]["arrival"]
                        except (KeyError, TypeError):
                            time_scheduled_departure = None
                            time_scheduled_arrival = None
                            time_real_departure = None
                            time_estimated_arrival = None
                            
                        # Extract origin airport coordinates
                        origin_latitude = None
                        origin_longitude = None
                        origin_altitude = None
                        if details['airport']['origin'] is not None:
                            origin_latitude = details['airport']['origin']['position']['latitude']
                            origin_longitude = details['airport']['origin']['position']['longitude']
                            origin_altitude = details['airport']['origin']['position']['altitude']
                            #print("Origin Coordinates:", origin_latitude, origin_longitude, origin_altitude)

                        # Extract destination airport coordinates
                        destination_latitude = None
                        destination_longitude = None
                        destination_altitude = None
                        if details['airport']['destination'] is not None:
                            destination_latitude = details['airport']['destination']['position']['latitude']
                            destination_longitude = details['airport']['destination']['position']['longitude']
                            destination_altitude = details['airport']['destination']['position']['altitude']
                            #print("Destination Coordinates:", destination_latitude, destination_longitude, destination_altitude)

                        # Calculate distances using modified functions
                        distance_origin = 0
                        distance_destination = 0

                        if origin_latitude is not None:
                            distance_origin = distance_from_flight_to_origin(
                                flight,
                                origin_latitude,
                                origin_longitude,
                                origin_altitude
                            )

                        if destination_latitude is not None:
                            distance_destination = distance_from_flight_to_destination(
                                flight,
                                destination_latitude,
                                destination_longitude,
                                destination_altitude
                            )
                            

                        # Get owner icao
                        try:
                            owner_icao = details["owner"]["code"]["icao"]
                        except (KeyError, TypeError):
                            owner_icao = (
                                flight.airline_icao
                                if not (flight.airline_icao.upper() in BLANK_FIELDS)
                                else "")

                        owner_iata = flight.airline_iata or "N/A"
                            
                        entry = {
                            "airline": airline,
                            "plane": plane,
                            "origin": origin,
                            "owner_iata": owner_iata,
                            "owner_icao": owner_icao,
                            "destination": destination,
                            "time_scheduled_departure": time_scheduled_departure,
                            "time_scheduled_arrival": time_scheduled_arrival,
                            "time_real_departure": time_real_departure,
                            "time_estimated_arrival": time_estimated_arrival,
                            "vertical_speed": flight.vertical_speed,
                            "callsign": callsign,
                            "distance_origin": distance_origin,
                            "distance_destination": distance_destination,
                            "distance": distance_from_flight_to_home(flight),
                            "direction": degrees_to_cardinal(plane_bearing(flight)),
                        }

                        data.append(entry)
                        
                        # Log the closest flight
                        log_flight_data(entry)
                        # Log farthest flight (origin or destination)
                        log_farthest_flight(entry)
                        
                        break

                    except (KeyError, AttributeError):
                        retries -= 1

            with self._lock:
                self._new_data = True
                self._processing = False
                self._data = data

        except (ConnectionError, NewConnectionError, MaxRetryError):
            self._new_data = False
            self._processing = False

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


# Main function
if __name__ == "__main__":

    o = Overhead()
    o.grab_data()
    while not o.new_data:
        print("processing...")
        sleep(1)

    print(o.data)











