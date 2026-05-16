#!/usr/bin/python3
from flask import Flask, render_template, jsonify, send_from_directory, request
import json
import os
import sys
from datetime import datetime, timezone

# Suppress Flask request logging
import logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Ensure project root is in path for utilities imports
_WEB_DIR  = os.path.dirname(__file__)
_BASE_DIR = os.path.abspath(os.path.join(_WEB_DIR, ".."))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

try:
    from config import MASTER_TRACKER as _MASTER_TRACKER
except Exception:
    _MASTER_TRACKER = ""

WEB_DIR  = _WEB_DIR
BASE_DIR = _BASE_DIR
app = Flask(
    __name__,
    template_folder=os.path.join(WEB_DIR, "templates"),
    static_folder=os.path.join(WEB_DIR, "static")
)

CLOSEST_FILE  = os.path.join(BASE_DIR, "close.txt")
FARTHEST_FILE = os.path.join(BASE_DIR, "farthest.txt")
TRACKED_FILE  = os.path.join(BASE_DIR, "tracked_flight.json")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load {path}: {e}")
        return default


def lookup_flight(callsign):
    """Look up a flight using routelookup cascade (AirLabs → FlightAware → FR24)."""
    callsign = callsign.strip().upper()
    try:
        from utilities.routelookup import RouteClient
        from utilities.opensky import OpenSkyClient
        rc = RouteClient()
        os = OpenSkyClient()

        # Check if airborne via OpenSky first
        state = os.find_callsign(callsign)
        if not state:
            return {"found": False}

        # Get route info via cascade
        details = rc.get_flight_details(
            callsign,
            state["latitude"],
            state["longitude"],
        )

        origin = details.get("origin", "???") or "???"
        dest   = details.get("destination", "???") or "???"
        airline = details.get("airline", "")

        return {
            "found":       True,
            "callsign":    callsign,
            "number":      callsign,
            "airline":     airline,
            "origin":      origin,
            "destination": dest,
            "summary":     f"{airline} {callsign} {origin}→{dest}",
        }

    except Exception as e:
        print(f"Lookup error: {e}")
        return {"found": False, "error": str(e)}


def search_route(origin, destination):
    """Search for flights on a route using FR24 flight-summary/light."""
    origin      = origin.strip().upper()
    destination = destination.strip().upper()
    try:
        from utilities.flightradar import FR24Client
        fr24 = FR24Client()
        if not fr24.ok:
            return {"found": False, "error": "FR24 not configured"}

        from datetime import datetime, timezone, timedelta
        import requests as _req
        now_utc  = datetime.now(timezone.utc)
        from_utc = (now_utc - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
        to_utc   = (now_utc + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")

        from config import FLIGHTRADAR24_KEY as _KEY
        r = _req.get(
            "https://fr24api.flightradar24.com/api/flight-summary/light",
            headers={"Accept": "application/json", "Accept-Version": "v1",
                     "Authorization": f"Bearer {_KEY}"},
            params={"flight_datetime_from": from_utc, "flight_datetime_to": to_utc,
                    "airports": f"outbound:{origin},inbound:{destination}",
                    "limit": 20, "sort": "desc"},
            timeout=10,
        )
        if r.status_code != 200:
            return {"found": False, "error": f"FR24 HTTP {r.status_code}"}

        flights = r.json().get("data", [])
        results = []
        for f in flights:
            results.append({
                "callsign":    f.get("callsign", "").strip(),
                "number":      f.get("flight", ""),
                "airline":     f.get("painted_as", ""),
                "origin":      origin,
                "destination": destination,
                "dep_time":    f.get("datetime_takeoff", ""),
                "is_live":     not f.get("flight_ended", True),
                "status":      "En route" if not f.get("flight_ended", True) else "Landed",
            })
        return {"found": bool(results), "flights": results}
    except Exception as e:
        print(f"Route search error: {e}")
        return {"found": False, "error": str(e)}

def _fa_lookup_callsign(callsign):
    """
    Call FlightAware /flights/{ident} and return all today's flights as picker dicts.
    This is Call 1 when the flight isn't yet airborne.
    """
    from time import time as _time
    now = _time()
    results = []
    try:
        from utilities.flightaware import _get_active_key, _parse_flight, BASE_URL, _increment_usage, is_available as _fa_ok
        import requests as _req
        if not _fa_ok():
            return []
        key = _get_active_key()
        r = _req.get(
            f"{BASE_URL}/flights/{callsign}",
            headers={"x-apikey": key},
            params={"max_pages": 1},
            timeout=10,
        )
        _increment_usage(key)
        # Log the lookup call
        try:
            from utilities.routelookup import _log_usage
            _log_usage("FlightAware(lookup)", callsign, None, None)
        except Exception:
            pass
        if r.status_code != 200:
            print(f"[lookup] FlightAware HTTP {r.status_code} for {callsign}")
            return []
        flights = r.json().get("flights", [])
        print(f"[lookup] FlightAware returned {len(flights)} flights for {callsign}")
        for f in flights:
            parsed = _parse_flight(f)
            sched_dep = parsed.get("time_scheduled_departure")
            # Only within 24h window
            if sched_dep and abs(sched_dep - now) > 86400:
                continue
            origin = parsed.get("origin_iata", "")
            dest   = parsed.get("dest_iata", "")
            if not origin and not dest:
                continue
            status = f.get("status", "Scheduled")
            results.append({
                "flight_id":           f.get("fa_flight_id", ""),
                "callsign":            callsign,
                "registration":        f.get("registration", ""),
                "aircraft":            f.get("aircraft_type", ""),
                "airline":             parsed.get("airline_name", ""),
                "origin":              origin,
                "destination":         dest,
                "latitude":            0, "longitude": 0, "altitude": 0,
                "is_live":             status == "En Route",
                "status":              status,
                "scheduled_departure": sched_dep,
                "cached_route": {
                    "origin":               origin,
                    "origin_latitude":      parsed.get("origin_lat"),
                    "origin_longitude":     parsed.get("origin_lon"),
                    "destination":          dest,
                    "destination_latitude": parsed.get("dest_lat"),
                    "destination_longitude":parsed.get("dest_lon"),
                    "time_scheduled_departure": sched_dep,
                    "time_scheduled_arrival":   parsed.get("time_scheduled_arrival"),
                    "time_real_departure":      parsed.get("time_real_departure"),
                    "time_estimated_arrival":   parsed.get("time_estimated_arrival"),
                },
            })
    except Exception as e:
        print(f"[lookup] _fa_lookup_callsign error: {e}")
        import traceback; traceback.print_exc()
    return results


# --- Routes ---

@app.get("/")
def index():
    try:
        from config import JOURNEY_CODE_SELECTED
    except Exception:
        JOURNEY_CODE_SELECTED = "ORD"
    return render_template("index.html", airport_code=JOURNEY_CODE_SELECTED)

@app.get("/closest/json")
def closest_json():
    return jsonify(load_json(CLOSEST_FILE, {}))

@app.get("/farthest/json")
def farthest_json():
    return jsonify(load_json(FARTHEST_FILE, []))

@app.get("/closest")
def closest_page():
    return render_template("closest_map.html")

@app.get("/farthest")
def farthest_page():
    return render_template("farthest_map.html")

@app.get("/tracked/json")
def tracked_json():
    return jsonify(load_json(TRACKED_FILE, {"callsign": ""}))

@app.post("/tracked/lookup")
def tracked_lookup():
    """
    Call 1: Look up a callsign.
    - If airborne: FR24 gRPC live feed → returns position + cached route from FR24 details
    - If not airborne: FlightAware /flights/{ident} → returns scheduled instances to pick from
    - Fallback: adsb.lol position only
    """
    data     = request.get_json(force=True)
    callsign = data.get("callsign", "").strip().upper()
    if not callsign:
        return jsonify({"found": False, "error": "No callsign provided"})

    try:
        # ── Path 1: FR24 live feed (airborne flights) ──
        try:
            from utilities.fr24_unofficial import is_available as _fr24u_ok
            from utilities.fr24_client import FR24Client
            if _fr24u_ok():
                client = FR24Client()
                flights_raw = client._run_with_client(
                    lambda fr24: client._find_by_callsign_async(fr24, callsign)
                )
                print(f"[lookup] FR24 live: {len(flights_raw) if flights_raw else 0} results for {callsign}")
                if flights_raw:
                    from utilities.airports import get_airport_coords as _gac
                    from utilities.airlines import get_airline_name as _aln
                    results = []
                    for f in flights_raw:
                        airline_icao = f.airline_icao or callsign[:3]
                        origin_iata  = f.origin_airport_iata or ""
                        dest_iata    = f.destination_airport_iata or ""
                        oc = _gac(origin_iata) if origin_iata else {}
                        dc = _gac(dest_iata)   if dest_iata   else {}
                        sched_dep = sched_arr = actual_dep = eta = None
                        if f.flight_id:
                            try:
                                details  = client.get_flight_details(f)
                                schedule = details.get("schedule_info", {})
                                progress = details.get("flight_progress", {})
                                sched_dep  = schedule.get("scheduled_departure")
                                sched_arr  = schedule.get("scheduled_arrival")
                                actual_dep = schedule.get("actual_departure")
                                eta        = progress.get("eta") or (f.eta if f.eta else None)
                                f.set_flight_details(details)
                            except Exception:
                                pass
                        results.append({
                            "flight_id":           f.flight_id,
                            "callsign":            f.callsign,
                            "registration":        f.registration,
                            "aircraft":            f.aircraft_code,
                            "airline":             _aln(airline_icao) or f.airline_name or airline_icao,
                            "origin":              origin_iata,
                            "destination":         dest_iata,
                            "latitude":            f.latitude,
                            "longitude":           f.longitude,
                            "altitude":            f.altitude,
                            "is_live":             not f.on_ground and f.altitude > 0,
                            "status":              "Airborne" if (not f.on_ground and f.altitude > 0) else "Ground",
                            "scheduled_departure": float(sched_dep) if sched_dep else None,
                            "cached_route": {
                                "origin":               origin_iata,
                                "origin_latitude":      oc.get("lat"),
                                "origin_longitude":     oc.get("lon"),
                                "destination":          dest_iata,
                                "destination_latitude": dc.get("lat"),
                                "destination_longitude":dc.get("lon"),
                                "time_scheduled_departure": float(sched_dep)  if sched_dep  else None,
                                "time_scheduled_arrival":   float(sched_arr)  if sched_arr  else None,
                                "time_real_departure":      float(actual_dep) if actual_dep else None,
                                "time_estimated_arrival":   float(eta)        if eta        else None,
                            },
                        })
                    if len(results) == 1:
                        r = results[0]
                        return jsonify({"found": True, "multiple": False, "callsign": callsign, "flight": r,
                                        "summary": f"{r['airline'] or callsign} {r['origin']}→{r['destination']} ({r['status']})"})
                    elif len(results) > 1:
                        return jsonify({"found": True, "multiple": True, "callsign": callsign, "flights": results,
                                        "summary": f"{len(results)} flights found — please select one"})
        except Exception as e:
            print(f"[lookup] FR24 live error: {e}")
            import traceback; traceback.print_exc()

        # ── Path 2: FlightAware scheduled lookup (not yet airborne) ──
        results = _fa_lookup_callsign(callsign)
        if results:
            if len(results) == 1:
                r = results[0]
                dep = r.get("scheduled_departure")
                dep_str = f" DEP {datetime.fromtimestamp(dep, tz=timezone.utc).strftime('%H:%M UTC')}" if dep else ""
                return jsonify({"found": True, "multiple": False, "callsign": callsign, "flight": r,
                                "summary": f"{r['airline'] or callsign} {r['origin']}→{r['destination']} ({r['status']}){dep_str}"})
            else:
                return jsonify({"found": True, "multiple": True, "callsign": callsign, "flights": results,
                                "summary": f"{len(results)} scheduled flights found — please select one"})

        # ── Path 3: adsb.lol position-only fallback ──
        from utilities.opensky import OpenSkyClient
        state = OpenSkyClient().find_callsign(callsign)
        if state:
            return jsonify({
                "found": True, "multiple": False, "callsign": callsign,
                "flight": {
                    "flight_id": "", "callsign": callsign,
                    "registration": state.get("icao24", ""),
                    "aircraft": "", "airline": "", "origin": "", "destination": "",
                    "latitude": state.get("latitude"), "longitude": state.get("longitude"),
                    "altitude": state.get("altitude", 0), "is_live": True,
                    "status": "Airborne", "scheduled_departure": None, "cached_route": None,
                },
                "summary": f"{callsign} is airborne (position only — no route data)",
            })

        return jsonify({
            "found": False, "callsign": callsign,
            "summary": f"{callsign} not found — check the callsign or try again closer to departure.",
        })

    except Exception as e:
        print(f"[lookup] Error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"found": False, "error": str(e)})

@app.post("/tracked/set")
def tracked_set():
    data         = request.get_json(force=True)
    callsign     = data.get("callsign", "").strip().upper()
    cached_route = data.get("cached_route")
    flight_id    = data.get("flight_id", "")
    sched_dep    = data.get("scheduled_departure")
    try:
        payload = {"callsign": callsign}
        if cached_route:  payload["cached_route"]        = cached_route
        if flight_id:     payload["flight_id"]           = flight_id
        if sched_dep:     payload["scheduled_departure"] = sched_dep
        with open(TRACKED_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        msg = f"Now tracking {callsign}." if callsign else "Tracking cleared."
        return jsonify({"message": msg})
    except Exception as e:
        return jsonify({"message": f"Error saving: {e}"}), 500

@app.post("/search/route")
def search_route_endpoint():
    data        = request.get_json(force=True)
    origin      = data.get("origin", "").strip().upper()
    destination = data.get("destination", "").strip().upper()
    if not origin or not destination:
        return jsonify({"found": False, "error": "Origin and destination required"})
    return jsonify(search_route(origin, destination))

@app.get("/debug/route")
def debug_route():
    """Debug endpoint — not available without FR24 scraper."""
    return jsonify({"error": "Debug route endpoint deprecated"})

@app.get("/weather/json")
def weather_json():
    """Serve current temperature, humidity and forecast to slave trackers."""
    try:
        from utilities.temperature import _load_file_cache, _TEMP_CACHE_FILE, _FORECAST_CACHE_FILE
        temp_data, temp_ts   = _load_file_cache(_TEMP_CACHE_FILE)
        forecast_data, f_ts  = _load_file_cache(_FORECAST_CACHE_FILE)
        temp = hum = None
        if temp_data:
            d = temp_data if isinstance(temp_data, (list, tuple)) else [None, None]
            temp, hum = (d[0], d[1]) if len(d) >= 2 else (None, None)
        return jsonify({
            "temperature": temp,
            "humidity":    hum,
            "forecast":    forecast_data or [],
        })
    except Exception:
        pass
    return jsonify({"temperature": None, "humidity": None, "forecast": []})

@app.get("/forecast/json")
def forecast_json():
    """Serve forecast intervals to slave trackers."""
    try:
        from utilities.temperature import _load_file_cache, _FORECAST_CACHE_FILE
        cached, ts = _load_file_cache(_FORECAST_CACHE_FILE)
        if cached:
            return jsonify(cached)
    except Exception:
        pass
    return jsonify([])

@app.get("/overhead/json")
def overhead_json():
    """Return current overhead flight data for slave trackers."""
    try:
        with open(os.path.join(BASE_DIR, "current_overhead.json"), "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    return jsonify(data)

@app.get("/tracked/json/live")
def tracked_json_live():
    """Return current tracked flight data for slave trackers."""
    try:
        with open(os.path.join(BASE_DIR, "tracked_flight.json"), "r") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"callsign": ""})

@app.get("/stats")
def stats_page():
    return render_template("stats.html")

@app.get("/stats/<date>")
def stats_day_page(date):
    return render_template("stats_day.html")

@app.get("/airport-code")
def airport_code():
    try:
        import importlib
        import config as _config
        importlib.reload(_config)
        code = getattr(_config, "JOURNEY_CODE_SELECTED", "ORD")
        lat, lon = getattr(_config, "LOCATION_HOME", [None, None])
    except Exception:
        code = "ORD"
        lat, lon = None, None

    location_name = ""
    if lat is not None and lon is not None:
        try:
            import requests as _req
            r = _req.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 13},
                headers={"User-Agent": "plane-tracker-rgb-pi/1.0"},
                timeout=5,
            )
            if r.status_code == 200:
                addr = r.json().get("address", {})
                # Try neighbourhood/suburb first, then fall back to town/city
                neighbourhood = (
                    addr.get("neighbourhood")
                    or addr.get("suburb")
                    or addr.get("quarter")
                    or addr.get("village")
                )
                city = (
                    addr.get("city")
                    or addr.get("town")
                    or addr.get("county")
                )
                if neighbourhood and city:
                    location_name = f"{neighbourhood}, {city}"
                elif city:
                    location_name = city
        except Exception as e:
            print(f"Reverse geocode failed: {e}")

    return jsonify({"code": code, "name": location_name})

@app.get("/counter")
def flight_counter():
    """Return full flight counter log in new date-keyed format."""
    try:
        with open(os.path.join(BASE_DIR, "flight_counter.json"), "r") as f:
            log = json.load(f)
        # Handle old flat format
        if "date" in log and "callsigns" in log:
            log = {log["date"]: {
                "date": log["date"],
                "count": log["count"],
                "flights": [{"callsign": c, "time": "00:00:00", "hour": 0} for c in log["callsigns"]],
                "first_seen": "",
                "last_seen": "",
            }}
        return jsonify(log)
    except Exception:
        return jsonify({})

@app.get("/counter/summary")
def flight_counter_summary():
    """Return daily summary stats for graphing."""
    try:
        with open(os.path.join(BASE_DIR, "flight_counter.json"), "r") as f:
            log = json.load(f)

        # Handle both old format (flat) and new format (date-keyed)
        if "date" in log and "callsigns" in log:
            # Old format — single day flat object
            log = {log["date"]: {
                "date": log["date"],
                "count": log["count"],
                "flights": [{"callsign": c, "hour": 0} for c in log["callsigns"]],
                "first_seen": "",
                "last_seen": "",
            }}

        summary = []
        for day, data in sorted(log.items()):
            by_hour = [0] * 24
            for flight in data.get("flights", []):
                by_hour[flight.get("hour", 0)] += 1
            summary.append({
                "date":       day,
                "count":      data["count"],
                "by_hour":    by_hour,
                "first_seen": data.get("first_seen", ""),
                "last_seen":  data.get("last_seen", ""),
            })
        return jsonify(summary)
    except Exception as e:
        return jsonify([])

@app.get("/maps/<path:filename>")
def maps(filename):
    maps_dir = os.path.join(WEB_DIR, "static/maps")
    return send_from_directory(maps_dir, filename)

@app.get("/config")
def config_page():
    return render_template("config.html")

@app.get("/api/config")
def config_get():
    cfg_path = os.path.join(BASE_DIR, "config", "config.json")
    sec_path = os.path.join(BASE_DIR, "config", "secrets.json")
    os.makedirs(os.path.join(BASE_DIR, "config"), exist_ok=True)
    try:
        with open(cfg_path, "r") as f: cfg = json.load(f)
    except Exception: cfg = {}
    try:
        with open(sec_path, "r") as f: sec = json.load(f)
    except Exception: sec = {}
    def _mask(v):
        if isinstance(v, str) and len(v) > 8: return v[:4] + "****" + v[-4:]
        return v
    sec_masked = {}
    for k, v in sec.items():
        if isinstance(v, list): sec_masked[k] = [_mask(i) for i in v]
        else: sec_masked[k] = _mask(v)
    return jsonify({"config": cfg, "secrets": sec_masked})

@app.post("/api/config")
def config_save():
    data = request.get_json(force=True)
    errors = []
    if "config" in data:
        try:
            cfg_path = os.path.join(BASE_DIR, "config", "config.json")
            os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(data["config"], f, indent=2)
        except Exception as e: errors.append(f"Config save error: {e}")
    if "secrets" in data:
        try:
            sec_path = os.path.join(BASE_DIR, "config", "secrets.json")
            os.makedirs(os.path.dirname(sec_path), exist_ok=True)
            try:
                with open(sec_path, "r") as f: existing = json.load(f)
            except Exception: existing = {}
            for k, v in data["secrets"].items():
                if isinstance(v, list):
                    merged = []
                    for i, item in enumerate(v):
                        if isinstance(item, str) and "****" in item:
                            merged.append(existing.get(k, [None]*(i+1))[i] if i < len(existing.get(k,[])) else item)
                        else: merged.append(item)
                    existing[k] = merged
                elif isinstance(v, str) and "****" in v: pass
                else: existing[k] = v
            with open(sec_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except Exception as e: errors.append(f"Secrets save error: {e}")
    try:
        import config as _config; _config.reload()
    except Exception as e: errors.append(f"Reload error: {e}")
    if errors: return jsonify({"ok": False, "errors": errors}), 500
    return jsonify({"ok": True, "message": "Config saved and reloaded"})

@app.post("/api/restart")
def api_restart():
    import subprocess, threading
    def _do_restart():
        import time; time.sleep(0.5)
        result = subprocess.run(["sudo", "systemctl", "restart", "its-a-plane"], capture_output=True)
        if result.returncode != 0:
            open(os.path.join(BASE_DIR, ".restart_requested"), "w").close()
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"ok": True})

@app.get("/api/system")
def api_system():
    import subprocess
    result = {}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            result["cpu_temp_c"] = round(int(f.read().strip()) / 1000, 1)
    except Exception: result["cpu_temp_c"] = None
    try:
        with open("/proc/uptime") as f:
            result["uptime_secs"] = int(float(f.read().split()[0]))
    except Exception: result["uptime_secs"] = None
    try:
        r = subprocess.run(["systemctl", "show", "its-a-plane", "--property=ActiveEnterTimestamp"],
                           capture_output=True, text=True, timeout=3)
        ts_str = r.stdout.strip().replace("ActiveEnterTimestamp=", "")
        if ts_str and ts_str != "n/a":
            from datetime import timezone as _tz
            dt = datetime.strptime(ts_str, "%a %Y-%m-%d %H:%M:%S %Z").replace(tzinfo=_tz.utc)
            result["service_uptime_secs"] = int((datetime.now(_tz.utc) - dt).total_seconds())
        else: result["service_uptime_secs"] = None
    except Exception: result["service_uptime_secs"] = None
    return jsonify(result)

@app.get("/api/usage")
def api_usage():
    try:
        with open(os.path.join(BASE_DIR, "api_usage.json"), "r") as f:
            data = json.load(f)
        data.setdefault("FR24Unofficial", 0)
        return jsonify(data)
    except Exception:
        return jsonify({"FlightStats": 0, "AirLabs": 0, "FlightAware": 0.0, "FR24Unofficial": 0})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
