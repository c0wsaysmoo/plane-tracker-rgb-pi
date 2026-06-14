#!/usr/bin/python3
from flask import Flask, render_template, jsonify, send_from_directory, request
import json
import os
import sys
import subprocess
import time
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

@app.get("/api_calls/log")
def api_calls_log():
    return send_from_directory(BASE_DIR, "api_calls.log", mimetype="text/plain")

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
    Call 1 of 2 for tracked flights.
    Queries the configured API cascade (AirLabs → FlightAware → FR24) for all
    flights matching the callsign, returning scheduled legs so the user can
    pick the right one (e.g. ORD→LAX vs LAX→JFK for the same flight number).
    No adsb.lol call is made here — position tracking only begins after the
    user selects a flight and it goes airborne.
    """
    data     = request.get_json(force=True)
    callsign = data.get("callsign", "").strip().upper()
    if not callsign:
        return jsonify({"found": False, "error": "No callsign provided"})

    try:
        from utilities.routelookup import RouteClient
        rc = RouteClient()
        flights = rc.get_tracked_flight(callsign)

        if not flights:
            return jsonify({
                "found":   False,
                "callsign": callsign,
                "summary": f"{callsign} not found — check the callsign or try again closer to departure.",
            })

        # Normalise to list — some sources return a single dict, others a list
        if isinstance(flights, dict):
            flights = [flights]

        # Shape each entry for the picker
        results = []
        for f in flights:
            sched_dep = f.get("time_scheduled_departure") or f.get("scheduled_departure")
            origin    = f.get("origin", "") or f.get("origin_iata", "")
            dest      = f.get("destination", "") or f.get("dest_iata", "")
            airline   = f.get("airline_name", "") or f.get("airline", "")
            aircraft  = f.get("aircraft_type", "") or f.get("plane", "")
            is_live   = bool(f.get("is_live"))
            status    = "Airborne" if is_live else ("Scheduled" if sched_dep else "Unknown")

            # cached_route is stored in tracked_flight.json so overhead.py has
            # coord data from the moment the flight goes airborne
            cached_route = {
                "origin":                   origin,
                "origin_latitude":          f.get("origin_latitude") or f.get("origin_lat"),
                "origin_longitude":         f.get("origin_longitude") or f.get("origin_lon"),
                "destination":              dest,
                "destination_latitude":     f.get("destination_latitude") or f.get("dest_lat"),
                "destination_longitude":    f.get("destination_longitude") or f.get("dest_lon"),
                "time_scheduled_departure": sched_dep,
                "time_scheduled_arrival":   f.get("time_scheduled_arrival"),
                "time_real_departure":      f.get("time_real_departure"),
                "time_estimated_arrival":   f.get("time_estimated_arrival"),
                "airline_name":             airline,
                "aircraft_type":            aircraft,
            }

            results.append({
                "callsign":            callsign,
                "flight_id":           f.get("flight_id", ""),
                "airline":             airline,
                "origin":              origin,
                "destination":         dest,
                "aircraft":            aircraft,
                "registration":        f.get("registration", ""),
                "is_live":             is_live,
                "status":              status,
                "scheduled_departure": sched_dep,
                "cached_route":        cached_route,
            })

        if len(results) == 1:
            r      = results[0]
            dep    = r["scheduled_departure"]
            dep_str = (f" DEP {datetime.fromtimestamp(dep, tz=timezone.utc).strftime('%H:%M UTC')}"
                       if dep else "")
            route  = f"{r['origin']}→{r['destination']}" if r["origin"] and r["destination"] else ""
            summary = "  ·  ".join(filter(None, [
                r["airline"] or callsign, route, r["aircraft"],
                dep_str.strip(), "AIRBORNE NOW" if r["is_live"] else "",
            ]))
            return jsonify({"found": True, "multiple": False, "callsign": callsign,
                            "flight": r, "summary": summary})

        # Multiple legs — show picker
        return jsonify({"found": True, "multiple": True, "callsign": callsign,
                        "flights": results,
                        "summary": f"{len(results)} flights found for {callsign} — select a leg"})

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

@app.get("/debug/flightaware/<callsign>")
def debug_flightaware(callsign):
    """Return raw FlightAware API response for a callsign."""
    try:
        from utilities.flightaware import FlightAwareClient
        fa = FlightAwareClient()
        result = fa.get_tracked_flight(callsign.strip().upper())
        return jsonify(result)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()})

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
    """Return current tracked flight data for slave trackers.
    If the flight is airborne (is_live=True), returns empty so slaves don't show it.
    If pre-departure, returns the saved forecast data from tracked_flight.json.
    """
    # Check if currently airborne via the live data written by overhead.py
    try:
        with open(os.path.join(BASE_DIR, "tracked_live.json"), "r") as f:
            live = json.load(f)
        if live and live.get("callsign") and live.get("is_live"):
            return jsonify({"callsign": ""})
    except Exception:
        pass
    # Not airborne — serve the forecast/pre-departure record
    try:
        with open(os.path.join(BASE_DIR, "tracked_flight.json"), "r") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"callsign": ""})

@app.get("/weather/json")
def weather_json():
    """Serve cached weather data (temp, humidity, weatherCode, forecast) to slave Pis."""
    _cache_dir = os.path.join(BASE_DIR, ".cache")
    temp_data = humidity_data = weather_code_data = None
    forecast_data = []
    try:
        with open(os.path.join(_cache_dir, "temperature.json"), "r") as f:
            obj = json.load(f)
            vals = obj.get("data", [])
            if isinstance(vals, list) and len(vals) >= 2:
                temp_data, humidity_data = vals[0], vals[1]
                weather_code_data = vals[2] if len(vals) >= 3 else None
    except Exception:
        pass
    try:
        with open(os.path.join(_cache_dir, "forecast.json"), "r") as f:
            obj = json.load(f)
            forecast_data = obj.get("data", [])
    except Exception:
        forecast_data = []
    return jsonify({
        "temperature": temp_data,
        "humidity":    humidity_data,
        "weatherCode": weather_code_data,
        "forecast":    forecast_data,
    })

@app.get("/clock/json")
def clock_json():
    """Serve astronomy data and pre-computed clock alerts to slave Pis."""
    result = {"sunrise": "", "sunset": "", "moonrise": "", "moonset": "", "illumination": "NA", "alerts": []}
    try:
        with open(os.path.join(BASE_DIR, ".cache", "astronomy.json"), "r") as f:
            obj = json.load(f)
        result.update({
            "sunrise":      obj.get("sunrise", ""),
            "sunset":       obj.get("sunset", ""),
            "moonrise":     obj.get("moonrise", ""),
            "moonset":      obj.get("moonset", ""),
            "illumination": obj.get("illumination", "NA"),
        })
    except Exception:
        pass

    alerts = []
    try:
        from utilities.airport_status import get_airport_alerts
        alerts.extend(get_airport_alerts())
    except Exception:
        pass
    try:
        from utilities.iss import get_iss_alert
        iss = get_iss_alert()
        if iss:
            alerts.append(iss)
    except Exception:
        pass
    try:
        from utilities.nws import get_nws_alerts
        alerts.extend(get_nws_alerts())
    except Exception:
        pass
    result["alerts"] = alerts

    return jsonify(result)

@app.get("/iss/json")
def iss_json():
    """Serve computed ISS alert and pass data to slave Pis."""
    try:
        from utilities.iss import get_iss_alert, get_iss_pass_data
        return jsonify({"alert": get_iss_alert(), "pass_data": get_iss_pass_data()})
    except Exception:
        return jsonify({"alert": None, "pass_data": None})

@app.get("/nws/json")
def nws_json():
    """Serve computed NWS weather alerts to slave Pis."""
    try:
        from utilities.nws import get_nws_alerts
        return jsonify(get_nws_alerts())
    except Exception:
        return jsonify([])

@app.get("/airport-status/json")
def airport_status_json():
    """Serve computed FAA airport alerts to slave Pis."""
    try:
        from utilities.airport_status import get_airport_alerts
        return jsonify(get_airport_alerts())
    except Exception:
        return jsonify([])

@app.get("/icons/<path:filename>")
def serve_icon(filename):
    """Serve weather icon PNGs upscaled to 64x64 for web display."""
    import io
    from PIL import Image
    for candidate in [
        os.path.join(BASE_DIR, "icons", filename),
        os.path.join(BASE_DIR, "..", "icons", filename),
        os.path.expanduser(f"~/icons/{filename}"),
    ]:
        if os.path.isfile(candidate):
            icon_path = candidate
            break
    else:
        return ("Not found", 404)
    try:
        img = Image.open(icon_path).convert("RGBA")
        img = img.resize((64, 64), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        from flask import Response
        return Response(buf.read(), mimetype="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception as e:
        return (str(e), 500)

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
                params={"lat": lat, "lon": lon, "format": "json", "zoom": 15},
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
    import os
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
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            result["load_avg"] = [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception: result["load_avg"] = None
    try:
        # Copy the existing environment and safely add/override TZ and LANG
        custom_env = os.environ.copy()
        custom_env["LANG"] = "C"
        custom_env["TZ"] = "UTC"
        
        r = subprocess.run(["systemctl", "show", "its-a-plane", "--property=ActiveEnterTimestamp"],
                           capture_output=True, text=True, timeout=3, env=custom_env)
        ts_str = r.stdout.strip().replace("ActiveEnterTimestamp=", "")
        if ts_str and ts_str != "n/a":
            from datetime import timezone as _tz
            dt = datetime.strptime(ts_str, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=_tz.utc)
            result["service_uptime_secs"] = int((datetime.now(_tz.utc) - dt).total_seconds())
        else: result["service_uptime_secs"] = None
    except Exception as e: 
        print(f"Uptime parse error: {e}")
        result["service_uptime_secs"] = None
    return jsonify(result)

@app.get("/api/usage")
def api_usage():
    try:
        with open(os.path.join(BASE_DIR, "api_usage.json"), "r") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception:
        return jsonify({"AirLabs": 0, "FlightAware": 0.0, "FR24": 0})

@app.get("/api/airport-coords")
def airport_coords_endpoint():
    """Resolve a comma-separated list of IATA codes to lat/lon."""
    codes = request.args.get("codes", "").split(",")
    try:
        from utilities.airports import get_airport_coords
    except Exception:
        return jsonify({})
    result = {}
    for raw in codes[:200]:
        code = raw.strip().upper()
        if not code:
            continue
        coords = get_airport_coords(code)
        if coords:
            result[code] = {"lat": coords["lat"], "lon": coords["lon"], "name": coords.get("name", "")}
    return jsonify(result)

@app.get("/api/aircraft-types")
def api_aircraft_types():
    """Serve aircraft type code → friendly name lookup to the frontend."""
    types_path = os.path.join(BASE_DIR, "aircraft_types.json")
    try:
        with open(types_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    result = {}
    for icao, entry in raw.items():
        name = entry.get("name", icao)
        result[icao] = name
        for short in entry.get("short_codes", []):
            result[short] = name
    return jsonify(result)

@app.get("/api/airlines")
def api_airlines():
    """Serve the full airlines.json lookup table to the frontend."""
    airlines_path = os.path.join(BASE_DIR, "airlines.json")
    try:
        with open(airlines_path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/wifi/status")
def wifi_status():
    """Return current WiFi connection info via nmcli."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY",
             "device", "wifi", "list"],
            capture_output=True, text=True, timeout=10
        )
        networks = []
        connected_ssid = None
        seen = {}  # ssid -> index in networks list
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 4:
                continue
            active   = parts[0]
            ssid     = parts[1]
            signal   = parts[2]
            security = ":".join(parts[3:])
            if not ssid:
                continue
            if active == "yes":
                connected_ssid = ssid
            # If already seen, upgrade to active if this BSSID is the connected one
            if ssid in seen:
                if active == "yes":
                    networks[seen[ssid]]["active"] = True
                    networks[seen[ssid]]["signal"] = int(signal) if signal.isdigit() else 0
                continue
            seen[ssid] = len(networks)
            networks.append({
                "ssid":     ssid,
                "signal":   int(signal) if signal.isdigit() else 0,
                "security": security.strip(),
                "active":   active == "yes",
            })
        networks.sort(key=lambda x: x["signal"], reverse=True)

        ip_result = subprocess.run(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"],
            capture_output=True, text=True, timeout=5
        )
        ip_addr = ""
        for line in ip_result.stdout.splitlines():
            if "IP4.ADDRESS" in line:
                ip_addr = line.split(":")[-1].split("/")[0].strip()
                break

        return jsonify({
            "connected_ssid": connected_ssid,
            "ip_address":     ip_addr,
            "networks":       networks,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/wifi/scan")
def wifi_scan():
    """Trigger a fresh nmcli scan and return updated network list."""
    try:
        subprocess.run(
            ["sudo", "nmcli", "device", "wifi", "rescan"],
            capture_output=True, text=True, timeout=15
        )
        time.sleep(2)
        result = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY",
             "device", "wifi", "list"],
            capture_output=True, text=True, timeout=10
        )
        networks = []
        seen = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 4:
                continue
            active   = parts[0]
            ssid     = parts[1]
            signal   = parts[2]
            security = ":".join(parts[3:])
            if not ssid:
                continue
            if ssid in seen:
                if active == "yes":
                    networks[seen[ssid]]["active"] = True
                    networks[seen[ssid]]["signal"] = int(signal) if signal.isdigit() else 0
                continue
            seen[ssid] = len(networks)
            networks.append({
                "ssid":     ssid,
                "signal":   int(signal) if signal.isdigit() else 0,
                "security": security.strip(),
                "active":   active == "yes",
            })
        networks.sort(key=lambda x: x["signal"], reverse=True)
        return jsonify({"networks": networks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/wifi/connect")
def wifi_connect():
    """Connect to a WiFi network. Body: { ssid, password }"""
    try:
        data     = request.get_json(force=True) or {}
        ssid     = (data.get("ssid")     or "").strip()
        password = (data.get("password") or "").strip()
        if not ssid:
            return jsonify({"success": False, "error": "SSID is required"}), 400
        cmd = ["sudo", "nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return jsonify({"success": True, "message": f"Connected to {ssid}"})
        else:
            err = (result.stderr or result.stdout).strip()
            return jsonify({"success": False, "error": err})
    except subprocess.TimeoutExpired:
        return jsonify({
            "success": True,
            "switched": True,
            "message": "Connection in progress. The Pi may have switched networks — "
                       "reconnect your device and navigate to the Pi's new IP.",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
