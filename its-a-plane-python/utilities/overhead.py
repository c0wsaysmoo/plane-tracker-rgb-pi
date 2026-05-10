"""
overhead.py — Auto-selects master or slave mode based on config.
If MASTER_TRACKER = "" this Pi runs the full OpenSky + FR24 stack.
If MASTER_TRACKER = "hostname" this Pi polls the master for data.
"""

try:
    from config import MASTER_TRACKER
except (ImportError, ModuleNotFoundError, NameError):
    MASTER_TRACKER = ""

if MASTER_TRACKER:
    import os, json, math as _math
    from threading import Thread, Lock
    import requests
    from requests.exceptions import ConnectionError
    from urllib3.exceptions import NewConnectionError, MaxRetryError

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))

    try:
        from config import LOCATION_HOME as _SLAVE_HOME
    except Exception:
        _SLAVE_HOME = [41.882724, -87.623350]

    try:
        from config import DISTANCE_UNITS as _DISTANCE_UNITS
    except Exception:
        _DISTANCE_UNITS = "imperial"

    _R = 3958.8

    def _hav(lat1, lon1, lat2, lon2):
        import math as m
        lat1,lon1,lat2,lon2 = map(m.radians,(lat1,lon1,lat2,lon2))
        dlat,dlon = lat2-lat1, lon2-lon1
        a = m.sin(dlat/2)**2 + m.cos(lat1)*m.cos(lat2)*m.sin(dlon/2)**2
        miles = _R * 2 * m.atan2(m.sqrt(a), m.sqrt(1-a))
        return miles * 1.609 if _DISTANCE_UNITS == "metric" else miles

    def _bear(lat, lon):
        import math as m
        la1,lo1 = map(m.radians,_SLAVE_HOME)
        la2,lo2 = map(m.radians,(lat,lon))
        b = m.atan2(m.sin(lo2-lo1)*m.cos(la2), m.cos(la1)*m.sin(la2)-m.sin(la1)*m.cos(la2)*m.cos(lo2-lo1))
        return (m.degrees(b)+360)%360

    def _card(d):
        return ["N","NE","E","SE","S","SW","W","NW"][int((d+22.5)/45)%8]

    def _recalc(flights):
        for f in flights:
            lat,lon = f.get("plane_latitude"), f.get("plane_longitude")
            if lat and lon:
                f["distance"]  = _hav(lat, lon, _SLAVE_HOME[0], _SLAVE_HOME[1])
                f["direction"] = _card(_bear(lat, lon))
        return flights

    def _url(path):
        host = MASTER_TRACKER.rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}.local:8080"
        return f"{host}{path}"

    class Overhead:
        def __init__(self):
            self._lock = Lock()
            self._data, self._tracked_data = [], None
            self._new_data = self._processing = False
            self._fr24_ok = True
            print(f"[Overhead] Slave mode — polling master at {_url('')}")

        def grab_data(self):
            Thread(target=self._grab, daemon=True).start()

        def _grab(self):
            with self._lock:
                self._new_data = False
                self._processing = True
            try:
                r = requests.get(_url("/overhead/json"), timeout=10)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, list): data = []
                data = _recalc(data)
                tracked = None
                try:
                    tr = requests.get(_url("/tracked/json/live"), timeout=10)
                    tr.raise_for_status()
                    td = tr.json()
                    tracked = td if td.get("callsign") else None
                except Exception as e:
                    print(f"[Slave] Tracked poll failed: {e}")
                with self._lock:
                    self._data, self._tracked_data = data, tracked
                    self._new_data = True
                    self._processing = False
            except (ConnectionError, NewConnectionError, MaxRetryError) as e:
                print(f"[Slave] Cannot reach master: {e}")
                with self._lock:
                    self._new_data = self._processing = False
            except Exception as e:
                print(f"[Slave] Error: {e}")
                with self._lock:
                    self._new_data = self._processing = False

        @property
        def new_data(self):
            with self._lock: return self._new_data
        @property
        def processing(self):
            with self._lock: return self._processing
        @property
        def data(self):
            with self._lock:
                self._new_data = False
                return self._data
        @property
        def tracked_data(self):
            with self._lock: return self._tracked_data
        @property
        def data_is_empty(self): return len(self._data) == 0
        @property
        def fr24_ok(self): return self._fr24_ok

else:
        # ---------------------------------------------------------------
        # MASTER MODE — full OpenSky + FR24 stack
        # ---------------------------------------------------------------
        print("[Overhead] Master mode — running full OpenSky + FR24 stack")

        import os
        import json
        import math
        import requests as _requests
        from datetime import date
        from time import sleep, time
        from threading import Thread, Lock

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
        from utilities.opensky import OpenSkyClient
        from utilities.routelookup import RouteClient as FR24Client

        try:
            from config import ZONE_HOME, LOCATION_HOME
            ZONE_DEFAULT     = ZONE_HOME
            LOCATION_DEFAULT = LOCATION_HOME
        except (ImportError, ModuleNotFoundError, NameError):
            ZONE_DEFAULT     = {"tl_y": 41.904318, "tl_x": -87.647367, "br_y": 41.851654, "br_x": -87.573027}
            LOCATION_DEFAULT = [41.882724, -87.623350]

        try:
            from config import MIN_ALTITUDE
        except (ImportError, ModuleNotFoundError, NameError):
            MIN_ALTITUDE = 0

        MAX_FLIGHT_LOOKUP = 5
        EARTH_RADIUS_M    = 3958.8
        BASE_DIR          = os.path.dirname(os.path.dirname(__file__))
        LOG_FILE          = os.path.join(BASE_DIR, "close.txt")
        LOG_FILE_FARTHEST = os.path.join(BASE_DIR, "farthest.txt")
        TRACKED_FILE      = os.path.join(BASE_DIR, "tracked_flight.json")
        COUNTER_FILE      = os.path.join(BASE_DIR, "flight_counter.json")

        def safe_load_json(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except (FileNotFoundError, json.JSONDecodeError):
                return []

        def safe_write_json(path, data):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

        def ordinal(n):
            return f"{n}{'tsnrhtdd'[(n//10 % 10 != 1) * (n % 10 < 4) * n % 10::4]}"

        def haversine(lat1, lon1, lat2, lon2):
            lat1, lon1 = map(math.radians, (lat1, lon1))
            lat2, lon2 = map(math.radians, (lat2, lon2))
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            miles = EARTH_RADIUS_M * c
            return miles * 1.609 if DISTANCE_UNITS == "metric" else miles

        def degrees_to_cardinal(deg):
            dirs = ["N","NE","E","SE","S","SW","W","NW"]
            return dirs[int((deg+22.5)/45)%8]

        def plane_bearing(lat, lon):
            lat1, lon1 = map(math.radians, LOCATION_DEFAULT)
            lat2, lon2 = map(math.radians, (lat, lon))
            b = math.atan2(math.sin(lon2-lon1)*math.cos(lat2), math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(lon2-lon1))
            return (math.degrees(b)+360)%360

        def distance_from_home(lat, lon):
            return haversine(lat, lon, LOCATION_DEFAULT[0], LOCATION_DEFAULT[1])

        def estimate_stale_data(last_data):
            data = dict(last_data)
            data["is_live"] = False
            speed_kts = data.get("ground_speed", 0)
            last_ts   = data.get("last_seen_ts")
            if not last_ts:
                return data
            elapsed_hrs  = (time() - last_ts) / 3600
            elapsed_mins = elapsed_hrs * 60
            last_time_str = data.get("time_remaining", "")
            if last_time_str:
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
            last_dist = data.get("dist_remaining")
            if last_dist is not None and speed_kts > 0:
                speed_display = speed_kts * (1.852 if DISTANCE_UNITS == "metric" else 1.15078)
                data["dist_remaining"] = max(0, last_dist - speed_display * elapsed_hrs)
            return data

        def _load_counter_log():
            try:
                with open(COUNTER_FILE, "r") as f:
                    return json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                return {}

        def _save_counter_log(data):
            with open(COUNTER_FILE, "w") as f:
                json.dump(data, f, indent=2)

        def calculate_eta(dist_nm, speed_kts, altitude_ft, vertical_speed=0):
            """
            Three-phase ETA calculation accounting for climb, cruise, and descent.
            - Cruise/climb: splits remaining distance into cruise segment + future descent
            - Descent:      uses 75% of current speed for all remaining distance
            - Vector buffer: adds distance for approach maneuvering near airport
            """
            import math as _m

            # Approach vector buffer
            if dist_nm < 15:
                dist_nm += 6
            elif dist_nm < 50:
                dist_nm *= 1.15

            TOD_dist   = altitude_ft / 1000 * 3   # 3:1 rule — feet to nm
            desc_speed = speed_kts * 0.75          # average speed through descent

            if vertical_speed < -200:
                # Already descending — use descent speed for everything remaining
                hours = dist_nm / desc_speed
            else:
                # Climbing or cruising — split at TOD
                # If climbing, current speed is low and self-corrects each OpenSky cycle
                descent_dist = min(TOD_dist, dist_nm)
                cruise_dist  = max(0, dist_nm - descent_dist)
                hours = (cruise_dist / speed_kts) + (descent_dist / desc_speed)

            return hours

        def log_flight_count(callsign, entry=None):
            if entry is None: entry = {}
            from datetime import datetime
            now     = datetime.now()
            today   = str(now.date())
            now_str = now.strftime("%H:%M:%S")
            log     = _load_counter_log()
            if today not in log:
                log[today] = {"date": today, "count": 0, "flights": [], "first_seen": now_str, "last_seen": now_str}
            seen = [e["callsign"] for e in log[today]["flights"]]
            if callsign and callsign not in seen:
                log[today]["flights"].append({
                    "callsign": callsign,
                    "time":     now_str,
                    "hour":     now.hour,
                    "origin":   entry.get("origin", ""),
                    "dest":     entry.get("destination", ""),
                })
                log[today]["count"]     = len(log[today]["flights"])
                log[today]["last_seen"] = now_str
                _save_counter_log(log)

        def load_tracked_callsign():
            try:
                with open(TRACKED_FILE, "r", encoding="utf-8") as f:
                    return json.load(f).get("callsign", "").strip().upper()
            except (FileNotFoundError, json.JSONDecodeError):
                return ""

        def log_flight_data(entry):
            try:
                entry["timestamp"] = email_alerts.get_timestamp()
                lst = safe_load_json(LOG_FILE)
                callsigns = {f.get("callsign"): f for f in lst}
                new_call  = entry.get("callsign")
                new_dist  = entry.get("distance", float("inf"))
                notify    = False
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
                rank = next(i+1 for i,f in enumerate(top_n) if f["callsign"] == new_call)
                if new_call not in callsigns:
                    notify = True
                safe_write_json(LOG_FILE, top_n)
                if notify:
                    html = map_generator.generate_closest_map(top_n, filename="closest.html")
                    url  = upload_helper.upload_map_to_server(html)
                    subject = f"New {ordinal(rank)} Closest Flight - {entry.get('callsign','Unknown')}"
                    email_alerts.send_flight_summary(subject, entry, map_url=url)
            except Exception as e:
                print("Failed to log closest flight:", e)

        def log_farthest_flight(entry, opensky=None):
            try:
                d_o = entry.get("distance_origin") or -1
                d_d = entry.get("distance_destination") or -1
                if d_o < 0 and d_d < 0:
                    return
                reason  = "origin" if d_o >= d_d else "destination"
                far     = d_o if reason == "origin" else d_d
                airport = entry.get(reason)
                if not airport or airport in ("?", "???", ""):
                    # Use callsign as unique key for unknown airports
                    airport = f"_{entry.get('callsign', 'UNKNOWN')}"
                    return

                # Fetch actual flown trail from OpenSky using icao24
                icao24 = entry.get("icao24", "")
                if icao24 and not entry.get("trail") and opensky:
                    try:
                        trail = opensky.get_flight_trail(icao24)
                        if trail:
                            entry["trail"] = trail
                    except Exception:
                        pass
                entry["timestamp"]      = email_alerts.get_timestamp()
                entry["reason"]         = reason
                entry["farthest_value"] = far
                entry["_airport"]       = airport
                lst         = safe_load_json(LOG_FILE_FARTHEST)
                # Ensure all farthest_values are floats
                for f in lst:
                    f["farthest_value"] = float(f.get("farthest_value", 0))
                airport_map = {f["_airport"]: f for f in lst}
                existing    = airport_map.get(airport)
                notify      = False
                if existing:
                    if far > existing.get("farthest_value", 0):
                        lst = [entry if f["_airport"] == airport else f for f in lst]
                        # No email for updates to existing airports
                    else:
                        return
                else:
                    if len(lst) >= MAX_FARTHEST:
                        if far <= min(f["farthest_value"] for f in lst):
                            return
                        lst.sort(key=lambda x: x["farthest_value"])
                        lst.pop(0)
                    lst.append(entry)
                    notify = True
                lst.sort(key=lambda x: float(x.get("farthest_value", 0)), reverse=True)
                lst = lst[:MAX_FARTHEST]
                safe_write_json(LOG_FILE_FARTHEST, lst)
                if notify:
                    html = map_generator.generate_farthest_map(lst, filename="farthest.html")
                    url  = upload_helper.upload_map_to_server(html)
                    rank = next(i for i,f in enumerate(lst) if f["_airport"] == airport) + 1
                    cs   = entry.get("callsign", "UNKNOWN")
                    subject = (f"New Farthest Flight ({reason}) - {cs}" if rank == 1 else f"{ordinal(rank)}-Farthest Flight ({reason}) - {cs}")
                    email_alerts.send_flight_summary(subject, entry, reason, map_url=url)
            except Exception as e:
                print("Failed to log farthest flight:", e)
        class Overhead:
            def __init__(self):
                self._opensky  = OpenSkyClient()
                self._fr24     = FR24Client()  # RouteClient from routelookup.py
                self._lock     = Lock()
                self._data         = []
                self._tracked_data = None
                self._new_data     = False
                self._processing   = False
                self._flight_cache = {}
                self._tracked_was_live        = False
                self._tracked_miss_count      = 0
                self._TRACKED_MISS_THRESHOLD  = 3
                self._tracked_last_callsign   = ""
                self._tracked_last_eta        = None
                self._tracked_last_data       = None
                self._tracked_route_cached    = None  # cached route info from API
                self._tracked_route_callsign  = ""    # callsign the cache is for

            def grab_data(self):
                Thread(target=self._grab, daemon=True).start()

            def _grab(self):
                with self._lock:
                    self._new_data   = False
                    self._processing = True

                overhead_data = []
                tracked_data  = None

                try:
                    zone_states = self._opensky.get_zone_states()
                    zone_states.sort(key=lambda s: distance_from_home(s["latitude"], s["longitude"]))
                    zone_states = zone_states[:MAX_FLIGHT_LOOKUP]

                    current_callsigns = {s["callsign"] for s in zone_states}
                    self._flight_cache = {k: v for k, v in self._flight_cache.items() if k in current_callsigns}

                    for state in zone_states:
                        callsign  = state["callsign"]
                        plane_lat = state["latitude"]
                        plane_lon = state["longitude"]

                        if callsign in self._flight_cache:
                            details = self._flight_cache[callsign]
                        else:
                            details = self._fr24.get_flight_details(callsign, plane_lat, plane_lon)
                            if not details:
                                continue
                            self._flight_cache[callsign] = details

                        dist_home  = distance_from_home(plane_lat, plane_lon)
                        bearing    = plane_bearing(plane_lat, plane_lon)
                        origin_lat = details.get("origin_latitude")
                        origin_lon = details.get("origin_longitude")
                        dest_lat   = details.get("destination_latitude")
                        dest_lon   = details.get("destination_longitude")
                        dist_o = haversine(plane_lat, plane_lon, origin_lat, origin_lon) if origin_lat else 0
                        dist_d = haversine(plane_lat, plane_lon, dest_lat, dest_lon) if dest_lat else 0

                        entry = {
                            **details,
                            "plane_latitude":       plane_lat,
                            "plane_longitude":      plane_lon,
                            "vertical_speed":       state["vertical_speed"],
                            "callsign":             callsign,
                            "icao24":               state.get("icao24", ""),
                            "distance":             dist_home,
                            "direction":            degrees_to_cardinal(bearing),
                            "distance_origin":      dist_o,
                            "distance_destination": dist_d,
                        }

                        overhead_data.append(entry)
                        log_flight_data(entry)
                        log_farthest_flight(entry, opensky=self._opensky)
                        log_flight_count(callsign, entry)

                    tracked_callsign = load_tracked_callsign()
                    if tracked_callsign:
                        # Reset state if callsign changed
                        if tracked_callsign != self._tracked_last_callsign:
                            self._tracked_last_callsign  = tracked_callsign
                            self._tracked_was_live       = False
                            self._tracked_miss_count     = 0
                            self._tracked_last_eta       = None
                            self._tracked_last_data      = None
                            self._tracked_route_cached   = None
                            self._tracked_route_callsign = ""

                        # Step 1: Poll OpenSky for live position (free)
                        os_state = self._opensky.find_callsign(tracked_callsign)

                        if os_state:
                            self._tracked_was_live   = True
                            self._tracked_miss_count = 0

                            # Step 2: Get route info once via RouteClient cascade, then cache it
                            if self._tracked_route_callsign != tracked_callsign or not self._tracked_route_cached:
                                route = self._fr24.get_tracked_flight(tracked_callsign)
                                if not route:
                                    # Fall back to full RouteClient cascade
                                    from utilities.routelookup import RouteClient as _RC
                                    _rc = _RC()
                                    details = _rc.get_flight_details(
                                        tracked_callsign,
                                        os_state["latitude"],
                                        os_state["longitude"],
                                    )
                                    if details and details.get("origin") not in ("?", "", None):
                                        route = {
                                            "airline_name":  details.get("airline", ""),
                                            "origin":        details.get("origin", ""),
                                            "destination":   details.get("destination", ""),
                                            "dest_lat":      details.get("destination_latitude"),
                                            "dest_lon":      details.get("destination_longitude"),
                                            "aircraft_type": details.get("plane", ""),
                                            "time_scheduled_departure": details.get("time_scheduled_departure"),
                                            "time_scheduled_arrival":   details.get("time_scheduled_arrival"),
                                            "time_real_departure":      details.get("time_real_departure"),
                                            "time_estimated_arrival":   details.get("time_estimated_arrival"),
                                        }
                                if route:
                                    # Sanity check: plane position should lie roughly
                                    # between origin and destination. Rejects codeshare
                                    # mismatches where the same flight number is used
                                    # on a completely different route by another airline.
                                    _r_dest_lat = route.get("dest_lat") or route.get("destination_latitude")
                                    _r_dest_lon = route.get("dest_lon") or route.get("destination_longitude")
                                    _r_orig_lat = route.get("origin_latitude") or route.get("orig_lat")
                                    _r_orig_lon = route.get("origin_longitude") or route.get("orig_lon")
                                    _plane_lat  = os_state["latitude"]
                                    _plane_lon  = os_state["longitude"]
                                    _plausible  = True
                                    if not (_r_orig_lat and _r_orig_lon):
                                        print(f"[Tracked] No origin coords for plausibility check on "
                                              f"{route.get('origin')}-{route.get('destination')} — accepting")
                                    if _r_orig_lat and _r_orig_lon and _r_dest_lat and _r_dest_lon:
                                        def _nm(la1, lo1, la2, lo2):
                                            import math as _m
                                            la1,lo1,la2,lo2 = map(_m.radians,(la1,lo1,la2,lo2))
                                            a = _m.sin((la2-la1)/2)**2 + _m.cos(la1)*_m.cos(la2)*_m.sin((lo2-lo1)/2)**2
                                            return 3440.07 * 2 * _m.atan2(_m.sqrt(a), _m.sqrt(1-a))
                                        _total = _nm(_r_orig_lat, _r_orig_lon, _r_dest_lat, _r_dest_lon)
                                        _to_o  = _nm(_plane_lat, _plane_lon, _r_orig_lat, _r_orig_lon)
                                        _to_d  = _nm(_plane_lat, _plane_lon, _r_dest_lat, _r_dest_lon)
                                        if (_to_o + _to_d) > _total * 1.25:
                                            _plausible = False
                                            print(f"[Tracked] Route {route.get('origin')}-{route.get('destination')} "
                                                  f"rejected — plane at ({_plane_lat:.2f},{_plane_lon:.2f}) "
                                                  f"doesn't fit route (triangle={_to_o+_to_d:.0f}nm vs total={_total:.0f}nm)")
                                    if _plausible:
                                        self._tracked_route_cached   = route
                                        self._tracked_route_callsign = tracked_callsign

                            # Step 3: Build tracked_data from OpenSky position + cached route
                            route_info = self._tracked_route_cached or {}
                            tracked_data = {
                                "callsign":      tracked_callsign,
                                "number":        tracked_callsign,
                                "airline_name":  route_info.get("airline_name", "") or route_info.get("airline", ""),
                                "is_live":       True,
                                "origin":        route_info.get("origin", ""),
                                "destination":   route_info.get("destination", ""),
                                "dest_lat":      route_info.get("destination_latitude") or route_info.get("dest_lat"),
                                "dest_lon":      route_info.get("destination_longitude") or route_info.get("dest_lon"),
                                "aircraft_type": route_info.get("aircraft_type", "") or route_info.get("plane", ""),
                                # Live position from OpenSky (free, updates every 30s)
                                "altitude":      os_state["altitude"],
                                "ground_speed":  os_state["ground_speed"],
                                "heading":       os_state["heading"],
                                "vertical_speed": os_state.get("vertical_speed", 0),
                                "latitude":      os_state["latitude"],
                                "longitude":     os_state["longitude"],
                                "time_scheduled_departure": route_info.get("time_scheduled_departure"),
                                "time_scheduled_arrival":   route_info.get("time_scheduled_arrival"),
                                "time_real_departure":      route_info.get("time_real_departure"),
                                "time_estimated_arrival":   route_info.get("time_estimated_arrival"),
                            }
                            # Calculate distance remaining and ETA from live position
                            dest_lat = route_info.get("destination_latitude") or route_info.get("dest_lat")
                            dest_lon = route_info.get("destination_longitude") or route_info.get("dest_lon")
                            origin_lat = route_info.get("origin_latitude") or route_info.get("origin_lat")
                            origin_lon = route_info.get("origin_longitude") or route_info.get("origin_lon")
                            speed_kts = os_state.get("ground_speed", 0)
                            if dest_lat and dest_lon and speed_kts > 50:
                                import math as _math
                                lat1,lon1 = _math.radians(os_state["latitude"]), _math.radians(os_state["longitude"])
                                lat2,lon2 = _math.radians(dest_lat), _math.radians(dest_lon)
                                dlat,dlon = lat2-lat1, lon2-lon1
                                a = _math.sin(dlat/2)**2 + _math.cos(lat1)*_math.cos(lat2)*_math.sin(dlon/2)**2
                                dist_nm = 3440.07 * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1-a))
                                hours_remaining = calculate_eta(
                                    dist_nm,
                                    speed_kts,
                                    os_state.get("altitude", 0),
                                    os_state.get("vertical_speed", 0),
                                )
                                from time import time as _time
                                eta_ts = _time() + hours_remaining * 3600
                                tracked_data["time_estimated_arrival"]   = eta_ts
                                tracked_data["time_scheduled_arrival"]   = eta_ts
                                try:
                                    from config import DISTANCE_UNITS as _DU
                                except Exception:
                                    _DU = "imperial"
                                dist_display = dist_nm * 1.15078 if _DU == "imperial" else dist_nm * 1.852
                                tracked_data["distance_destination"] = dist_display
                                # Fields for tracked scenes
                                tracked_data["dist_remaining"] = dist_display

                                # Format time remaining as "H:MM"
                                total_mins = int(hours_remaining * 60)
                                hrs  = total_mins // 60
                                mins = total_mins % 60
                                tracked_data["time_remaining"] = f"{hrs}:{mins:02d}" if hrs else f"{mins}m"

                                # total_distance — origin to dest great circle
                                if origin_lat and origin_lon:
                                    lat1o,lon1o = _math.radians(origin_lat), _math.radians(origin_lon)
                                    ao = _math.sin((lat2-lat1o)/2)**2 + _math.cos(lat1o)*_math.cos(lat2)*_math.sin((lon2-lon1o)/2)**2
                                    total_nm = 3440.07 * 2 * _math.atan2(_math.sqrt(ao), _math.sqrt(1-ao))
                                    tracked_data["total_distance"] = total_nm * 1.15078 if _DU == "imperial" else total_nm * 1.852
                                else:
                                    # Look up origin coords from airports database
                                    origin_code = route_info.get("origin", "")
                                    if origin_code:
                                        try:
                                            from utilities.airports import get_airport_coords as _gac
                                            oc = _gac(origin_code)
                                            if oc:
                                                lat1o,lon1o = _math.radians(oc["lat"]), _math.radians(oc["lon"])
                                                ao = _math.sin((lat2-lat1o)/2)**2 + _math.cos(lat1o)*_math.cos(lat2)*_math.sin((lon2-lon1o)/2)**2
                                                total_nm = 3440.07 * 2 * _math.atan2(_math.sqrt(ao), _math.sqrt(1-ao))
                                                tracked_data["total_distance"] = total_nm * 1.15078 if _DU == "imperial" else total_nm * 1.852
                                        except Exception:
                                            pass
                            self._tracked_last_eta  = tracked_data.get("time_estimated_arrival")
                            self._tracked_last_data = tracked_data

                        else:
                            # OpenSky didn't find it
                            if self._tracked_was_live:
                                now_ts = time()
                                eta    = self._tracked_last_eta
                                if eta is not None:
                                    mins_since_eta = (now_ts - eta) / 60
                                    if mins_since_eta > 0:
                                        self._tracked_miss_count += 1
                                        if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                            self._do_auto_wipe()
                                        elif self._tracked_last_data:
                                            tracked_data = estimate_stale_data(self._tracked_last_data)
                                    else:
                                        self._tracked_miss_count = 0
                                        if self._tracked_last_data:
                                            tracked_data = estimate_stale_data(self._tracked_last_data)
                                else:
                                    self._tracked_miss_count += 1
                                    if self._tracked_miss_count >= self._TRACKED_MISS_THRESHOLD:
                                        self._do_auto_wipe()
                                    elif self._tracked_last_data:
                                        tracked_data = estimate_stale_data(self._tracked_last_data)

                    # Write current overhead for slave trackers
                    try:
                        current_file = os.path.join(BASE_DIR, "current_overhead.json")
                        with open(current_file, "w", encoding="utf-8") as f:
                            json.dump(overhead_data, f)
                    except Exception as e:
                        print(f"Failed to write current_overhead.json: {e}")

                    # Print API usage tally once per cycle
                    try:
                        from utilities.routelookup import _print_tally
                        _print_tally()
                    except Exception:
                        pass

                    with self._lock:
                        self._data         = overhead_data
                        self._tracked_data = tracked_data
                        self._new_data     = True
                        self._processing   = False

                except (ConnectionError, NewConnectionError, MaxRetryError):
                    with self._lock:
                        self._fr24_ok    = False
                        self._new_data   = False
                        self._processing = False

            def _do_auto_wipe(self):
                try:
                    with open(TRACKED_FILE, "w", encoding="utf-8") as f:
                        json.dump({"callsign": ""}, f)
                    print("Tracked flight ended — auto-cleared.")
                except Exception as e:
                    print(f"Failed to auto-clear tracked flight: {e}")
                self._tracked_was_live      = False
                self._tracked_miss_count    = 0
                self._tracked_last_eta      = None
                self._tracked_last_data     = None
                self._tracked_last_callsign = ""

            @property
            def new_data(self):
                with self._lock: return self._new_data

            @property
            def processing(self):
                with self._lock: return self._processing

            @property
            def data(self):
                with self._lock:
                    self._new_data = False
                    return self._data

            @property
            def tracked_data(self):
                with self._lock: return self._tracked_data

            @property
            def data_is_empty(self):
                return len(self._data) == 0

            @property
            def fr24_ok(self):
                return self._fr24.ok
