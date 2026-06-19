#!/usr/bin/python3
from flask import Flask, render_template, jsonify, send_from_directory, request
import json
import os
import subprocess
import sys
import time as _time

# Ensure the parent directory is on sys.path so `config` and `utilities` resolve
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from utilities.fr24_client import FR24Client

# Singleton FR24Client shared across all web requests (shares cache + rate limiter)
_fr24_client = FR24Client()

# /web is the folder that this file lives in
WEB_DIR = os.path.dirname(__file__)

app = Flask(
    __name__,
    template_folder=os.path.join(WEB_DIR, "templates"),
    static_folder=os.path.join(WEB_DIR, "static")
)

# Writable data directory (same as overhead.py uses)
DATA_DIR = os.environ.get("PLANE_TRACKER_DATA_DIR", "/var/lib/plane-tracker")
CLOSEST_FILE  = os.path.join(DATA_DIR, "close.txt")
FARTHEST_FILE = os.path.join(DATA_DIR, "farthest.txt")
TRACKED_FILE  = os.path.join(DATA_DIR, "tracked_flight.json")
MAPS_DIR      = os.path.join(DATA_DIR, "maps")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not load {path}: {e}")
        return default


def _build_cached_route(sched):
    """Build cached_route dict from AirLabs schedule data.
    Concept from c0wsaysmoo/plane-tracker-rgb-pi."""
    from utilities.overhead import _airport_coords
    origin = sched.get("origin", "")
    dest = sched.get("destination", "")
    o_coords = _airport_coords(origin)
    d_coords = _airport_coords(dest)
    # Compute arrival timestamp from dep + duration if available
    dep_ts = sched.get("dep_time_ts")
    duration = sched.get("duration")
    arr_ts = (dep_ts + duration * 60) if dep_ts and duration else None
    # Try to get airline name from local DB
    airline_name = ""
    try:
        from utilities.overhead import _airline_name_lookup
        airline_icao = sched.get("airline_icao", "")
        if airline_icao:
            airline_name = _airline_name_lookup(airline_icao) or ""
    except (ImportError, Exception):
        pass
    return {
        "origin": origin,
        "destination": dest,
        "origin_lat": o_coords.get("lat"),
        "origin_lon": o_coords.get("lon"),
        "dest_lat": d_coords.get("lat"),
        "dest_lon": d_coords.get("lon"),
        "airline_name": airline_name,
        "aircraft_type": "",
        "time_scheduled_departure": dep_ts,
        "time_scheduled_arrival": arr_ts,
        "cs_airline_iata": sched.get("cs_airline_iata", ""),
        "dep_time": sched.get("dep_time", ""),
        "arr_time": sched.get("arr_time", ""),
    }


def lookup_flight(callsign):
    """
    Try to find a live flight by callsign or flight number.
    Returns a dict with found=True/False and flight info if found.
    """
    callsign = callsign.strip().upper()
    original_callsign = callsign  # preserve for AirLabs (IATA works better)

    # Convert IATA (UA353) to ICAO (UAL353)
    from utilities.overhead import IATA_TO_ICAO
    if len(callsign) >= 3 and callsign[:2] in IATA_TO_ICAO and callsign[2:3].isdigit():
        icao_prefix = IATA_TO_ICAO.get(callsign[:2])
        if icao_prefix:
            callsign = icao_prefix + callsign[2:]

    try:
        api = _fr24_client

        # Server-side callsign filter (searches FR24's full worldwide feed)
        match = api.find_by_callsign(callsign)

        if not match:
            # Not airborne — try AirLabs for scheduled flight (use original IATA format)
            from utilities.airlabs import get_flight_schedule, get_flight_legs
            sched = get_flight_schedule(original_callsign)
            if sched:
                # Try operating carrier callsign from AirLabs
                op_callsign = (sched.get("flight_icao") or "").upper()
                if op_callsign and op_callsign != callsign:
                    match = api.find_by_callsign(op_callsign)

                # Try regional operator callsigns as fallback
                if not match:
                    from utilities.overhead import REGIONAL_OPERATORS
                    icao_prefix = callsign.rstrip("0123456789")
                    flight_num = callsign[len(icao_prefix):]
                    if icao_prefix in REGIONAL_OPERATORS:
                        for alt_prefix in REGIONAL_OPERATORS[icao_prefix]:
                            match = api.find_by_callsign(alt_prefix + flight_num)
                            if match:
                                break

                if match:
                    # Found via operating carrier — fall through to details below
                    pass
                else:
                    # Check for multiple legs (e.g., AA100 does JFK→LHR then LHR→JFK)
                    # Concept from c0wsaysmoo/plane-tracker-rgb-pi.
                    legs = get_flight_legs(original_callsign)
                    if len(legs) > 1:
                        results = []
                        for leg in legs:
                            cr = _build_cached_route(leg)
                            results.append({
                                "callsign": callsign,
                                "origin": leg.get("origin", ""),
                                "destination": leg.get("destination", ""),
                                "dep_time": leg.get("dep_time", ""),
                                "status": leg.get("status", ""),
                                "scheduled_departure": leg.get("dep_time_ts"),
                                "cached_route": cr,
                            })
                        return {
                            "found": True,
                            "multiple": True,
                            "callsign": callsign,
                            "flights": results,
                            "summary": f"{len(results)} legs found for {original_callsign} — select one",
                        }

                    # Single leg — schedule only, may not be trackable
                    trackable = not bool(REGIONAL_OPERATORS.get(
                        callsign.rstrip("0123456789"), []))
                    cr = _build_cached_route(sched)
                    result = {
                        "found": True,
                        "scheduled": True,
                        "trackable": trackable,
                        "callsign": callsign,
                        "number": sched.get("flight_number", callsign),
                        "airline": "",
                        "origin": sched.get("origin", "???"),
                        "destination": sched.get("destination", "???"),
                        "dep_time": sched.get("dep_time", ""),
                        "status": sched.get("status", ""),
                        "scheduled_departure": sched.get("dep_time_ts"),
                        "cached_route": cr,
                        "summary": f"Scheduled: {sched.get('flight_number', callsign)} {sched.get('origin', '?')}→{sched.get('destination', '?')} Dep {sched.get('dep_time', '?')}",
                    }
                    if not trackable:
                        result["warning"] = (
                            "This flight may use a regional operator callsign — "
                            "live tracking will be attempted but may not work"
                        )
                    return result
            if not match:
                return {"found": False}

        # Get full details for airline name and route
        details = api.get_flight_details(match)
        match.set_flight_details(details)

        airline = match.airline_name or ""
        origin = match.origin_airport_iata or "???"
        destination = match.destination_airport_iata or "???"
        number = match.number or callsign

        # Build cached route from live FR24 data
        from utilities.overhead import _airport_coords
        fp = details.get("flight_progress") or {} if details else {}
        time_info = details.get("time") or {} if details else {}
        sched = (time_info.get("scheduled") or {})
        real = (time_info.get("real") or {})
        est = (time_info.get("estimated") or {})
        o_coords = _airport_coords(origin)
        d_coords = _airport_coords(destination)
        cr = {
            "origin": origin, "destination": destination,
            "origin_lat": o_coords.get("lat"), "origin_lon": o_coords.get("lon"),
            "dest_lat": d_coords.get("lat"), "dest_lon": d_coords.get("lon"),
            "airline_name": airline, "aircraft_type": match.aircraft_code or "",
            "time_scheduled_departure": sched.get("departure"),
            "time_scheduled_arrival": sched.get("arrival"),
            "time_real_departure": real.get("departure"),
            "time_estimated_arrival": est.get("arrival"),
            "cs_airline_iata": "",
        }

        return {
            "found": True,
            "callsign": match.callsign,
            "number": number,
            "airline": airline,
            "origin": origin,
            "destination": destination,
            "scheduled_departure": sched.get("departure"),
            "cached_route": cr,
            "summary": f"{airline} {number} {origin}→{destination}",
        }

    except Exception as e:
        print(f"Lookup error: {e}")
        return {"found": False, "error": str(e)}


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(WEB_DIR, "static"), "favicon.ico", mimetype="image/x-icon")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/closest/json")
def closest_json():
    return jsonify(load_json(CLOSEST_FILE, []))


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
    """Live lookup — check if a flight is currently findable before saving."""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"found": False, "error": "Invalid request"}), 400
    callsign = data.get("callsign", "").strip().upper()
    if not callsign:
        return jsonify({"found": False, "error": "No callsign provided"})
    result = lookup_flight(callsign)
    return jsonify(result)


@app.post("/tracked/set")
def tracked_set():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"message": "Invalid request"}), 400
    callsign = data.get("callsign", "").strip().upper()[:10]
    cached_route = data.get("cached_route")        # dict from lookup, or None
    sched_dep = data.get("scheduled_departure")     # unix timestamp, or None
    try:
        payload = {"callsign": callsign, "set_ts": int(_time.time()) if callsign else 0}
        if cached_route:
            payload["cached_route"] = cached_route
        if sched_dep:
            payload["scheduled_departure"] = sched_dep
        with open(TRACKED_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        try:
            os.chmod(TRACKED_FILE, 0o666)
        except OSError:
            pass
        msg = f"Now tracking {callsign}." if callsign else "Tracking cleared."
        return jsonify({"message": msg})
    except Exception as e:
        return jsonify({"message": f"Error saving: {e}"}), 500


@app.post("/route/search")
def route_search():
    """Search for live flights by origin→destination using gRPC server-side filter."""
    import re
    data = request.get_json(force=True)
    if not data:
        return jsonify({"flights": [], "error": "Invalid request"}), 400
    origin = data.get("origin", "").strip().upper()
    destination = data.get("destination", "").strip().upper()
    if not origin or not destination:
        return jsonify({"flights": [], "error": "Origin and destination required"}), 400
    if not re.match(r'^[A-Z]{3,4}$', origin) or not re.match(r'^[A-Z]{3,4}$', destination):
        return jsonify({"flights": [], "error": "Airport codes must be 3-4 letters"}), 400
    try:
        matches = _fr24_client.find_by_route(origin, destination)
        flights = []
        for m in matches:
            flights.append({
                "callsign": m.callsign,
                "origin": m.origin_airport_iata or origin,
                "destination": m.destination_airport_iata or destination,
                "aircraft": m.aircraft_code or "",
                "altitude": m.altitude,
                "speed": m.ground_speed,
                "latitude": m.latitude,
                "longitude": m.longitude,
            })
        return jsonify({"flights": flights, "count": len(flights)})
    except Exception as e:
        return jsonify({"flights": [], "error": str(e)}), 500


# Location name (reverse geocode via Nominatim).
# Concept from c0wsaysmoo/plane-tracker-rgb-pi.
_location_cache = {}

@app.get("/airport-code")
def airport_code():
    """Return home airport code and reverse-geocoded location name."""
    if _location_cache:
        return jsonify(_location_cache)

    try:
        from config import JOURNEY_CODE_SELECTED, LOCATION_HOME
        code = JOURNEY_CODE_SELECTED or "???"
        lat, lon = LOCATION_HOME[0], LOCATION_HOME[1]
    except Exception:
        return jsonify({"code": "???", "name": ""})

    import requests as http_req
    location_name = ""
    try:
        r = http_req.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 13},
            headers={"User-Agent": "plane-tracker-rgb-pi/1.0"},
            timeout=5,
        )
        if r.status_code == 200:
            addr = r.json().get("address", {})
            neighbourhood = (
                addr.get("neighbourhood")
                or addr.get("suburb")
                or addr.get("quarter")
                or addr.get("village")
            )
            city = addr.get("city") or addr.get("town") or addr.get("county")
            if neighbourhood and city:
                location_name = f"{neighbourhood}, {city}"
            elif city:
                location_name = city
    except Exception:
        pass

    result = {"code": code, "name": location_name}
    _location_cache.update(result)
    return jsonify(result)


@app.post("/api/airlines")
def api_airlines():
    """Batch-resolve ICAO airline prefixes to names.

    POST JSON: {"codes": ["UAL", "AAL", "DAL"]}
    Returns: {"UAL": "United Airlines", "AAL": "American Airlines", ...}
    """
    try:
        from utilities.airlines import get_airline_name
    except ImportError:
        return jsonify({})
    data = request.get_json(force=True) or {}
    codes = data.get("codes", [])
    result = {}
    for code in codes[:100]:  # cap at 100
        name = get_airline_name(code)
        if name:
            result[code] = name
    return jsonify(result)


@app.post("/api/airport-coords")
def api_airport_coords():
    """Batch-resolve airport codes to coordinates.

    POST JSON: {"codes": ["JFK", "LAX", "CDG"]}
    Returns: {"JFK": {"lat": 40.64, "lon": -73.78}, ...}
    """
    try:
        from utilities.airports import get_airport_coords
    except ImportError:
        return jsonify({})
    data = request.get_json(force=True) or {}
    codes = data.get("codes", [])
    result = {}
    for code in codes[:200]:  # cap at 200
        coords = get_airport_coords(code)
        if coords:
            result[code] = coords
    return jsonify(result)


@app.get("/api/aircraft-types")
def api_aircraft_types():
    """Return aircraft type code -> name mapping (from aircraft_types.json)."""
    path = os.path.join(BASE_DIR, "aircraft_types.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Build flat lookup: both primary code and short_codes -> name
        result = {}
        for code, info in data.items():
            name = info.get("name", code)
            result[code] = name
            for sc in info.get("short_codes", []):
                result[sc] = name
        return jsonify(result)
    except Exception:
        return jsonify({})


# Flight counter and stats (concept from c0wsaysmoo/plane-tracker-rgb-pi)
from utilities.overhead import COUNTER_FILE


@app.get("/counter")
def flight_counter():
    """Return full flight counter log (date-keyed dict)."""
    try:
        with open(COUNTER_FILE, "r", encoding="utf-8") as f:
            log = json.load(f)
        if not isinstance(log, dict):
            return jsonify({})
        return jsonify(log)
    except Exception:
        return jsonify({})


@app.get("/counter/summary")
def flight_counter_summary():
    """Return daily summary stats for graphing."""
    try:
        with open(COUNTER_FILE, "r", encoding="utf-8") as f:
            log = json.load(f)
        if not isinstance(log, dict):
            return jsonify([])
        summary = []
        for day, data in sorted(log.items()):
            by_hour = [0] * 24
            for flight in data.get("flights", []):
                h = int(flight.get("hour") or 0)
                if 0 <= h <= 23:
                    by_hour[h] += 1
            summary.append({
                "date": day,
                "count": data.get("count", 0),
                "by_hour": by_hour,
                "first_seen": data.get("first_seen", ""),
                "last_seen": data.get("last_seen", ""),
            })
        return jsonify(summary)
    except Exception:
        return jsonify([])


@app.get("/stats")
def stats_page():
    return render_template("stats.html")


@app.get("/stats/<date>")
def stats_day_page(date):
    return render_template("stats_day.html")


# Serve map files from the data directory
@app.get("/maps/<path:filename>")
def maps(filename):
    return send_from_directory(MAPS_DIR, filename)


# ---- Config UI ----

@app.get("/config")
def config_page():
    return render_template("config.html")


@app.get("/api/config")
def api_config_get():
    """Return current config as JSON. Masks secret values."""
    import config as cfg

    SECRET_KEYS = {"FR24_API_KEY", "TOMORROW_API_KEY", "AIRLABS_API_KEY", "NPS_API_KEY", "OWM_API_KEY"}

    result = {}
    # Flat env-style keys the UI expects
    for key in [
        "HOME_LAT", "HOME_LON",
        "ZONE_TL_LAT", "ZONE_TL_LON", "ZONE_BR_LAT", "ZONE_BR_LON",
        "JOURNEY_CODE_SELECTED", "TEMPERATURE_LOCATION", "TIDE_STATION",
        "WATER_TEMP_STATION", "AIRPORT_STATUS_LIST",
        "DISTANCE_UNITS", "SPEED_UNITS", "TEMPERATURE_UNITS", "CLOCK_FORMAT",
        "BRIGHTNESS", "BRIGHTNESS_NIGHT", "GPIO_SLOWDOWN", "LED_RGB_SEQUENCE",
        "NIGHT_BRIGHTNESS", "NIGHT_START", "NIGHT_END", "HAT_PWM_ENABLED",
        "MIN_ALTITUDE", "JOURNEY_BLANK_FILLER", "FORECAST_DAYS", "BLOCKED_CALLSIGNS",
        "NWS_ALERTS_ENABLED", "ISS_ALERTS_ENABLED",
        "MAX_CLOSEST", "MAX_FARTHEST", "STATS_LOG_DAYS",
        "FR24_API_KEY", "TOMORROW_API_KEY", "AIRLABS_API_KEY", "NPS_API_KEY", "OWM_API_KEY",
        "EMAIL",
    ]:
        # Return resolved booleans for checkbox fields
        if key in {"NIGHT_BRIGHTNESS", "HAT_PWM_ENABLED", "NWS_ALERTS_ENABLED", "ISS_ALERTS_ENABLED"}:
            result[key] = getattr(cfg, key, False)
            continue
        val = cfg._get(key)
        if key in SECRET_KEYS and val:
            # Mask: show first 4 and last 4 chars
            if len(val) > 10:
                val = val[:4] + "*" * (len(val) - 8) + val[-4:]
            else:
                val = "****"
        result[key] = val

    # Populate computed zone/location fields if not already set
    for key, fallback in [
        ("HOME_LAT", str(cfg.LOCATION_HOME[0])),
        ("HOME_LON", str(cfg.LOCATION_HOME[1])),
        ("ZONE_TL_LAT", str(cfg.ZONE_HOME["tl_y"])),
        ("ZONE_TL_LON", str(cfg.ZONE_HOME["tl_x"])),
        ("ZONE_BR_LAT", str(cfg.ZONE_HOME["br_y"])),
        ("ZONE_BR_LON", str(cfg.ZONE_HOME["br_x"])),
    ]:
        if not result.get(key):
            result[key] = fallback

    return jsonify(result)


_VALID_CONFIG_KEYS = {
    "HOME_LAT", "HOME_LON",
    "ZONE_TL_LAT", "ZONE_TL_LON", "ZONE_BR_LAT", "ZONE_BR_LON",
    "JOURNEY_CODE_SELECTED", "TEMPERATURE_LOCATION", "TIDE_STATION",
    "WATER_TEMP_STATION", "AIRPORT_STATUS_LIST",
    "DISTANCE_UNITS", "SPEED_UNITS", "TEMPERATURE_UNITS", "CLOCK_FORMAT",
    "BRIGHTNESS", "BRIGHTNESS_NIGHT", "GPIO_SLOWDOWN", "LED_RGB_SEQUENCE",
    "NIGHT_BRIGHTNESS", "NIGHT_START", "NIGHT_END", "HAT_PWM_ENABLED",
    "MIN_ALTITUDE", "JOURNEY_BLANK_FILLER", "FORECAST_DAYS", "BLOCKED_CALLSIGNS",
    "NWS_ALERTS_ENABLED", "ISS_ALERTS_ENABLED",
    "MAX_CLOSEST", "MAX_FARTHEST", "STATS_LOG_DAYS",
    "FR24_API_KEY", "TOMORROW_API_KEY", "AIRLABS_API_KEY", "NPS_API_KEY", "OWM_API_KEY",
    "EMAIL",
}


@app.post("/api/config")
def api_config_post():
    """Save config to config/config.json and reload."""
    import config as cfg

    data = request.get_json(force=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload"}), 400

    # Allowlist: only accept known config keys
    data = {k: v for k, v in data.items() if k in _VALID_CONFIG_KEYS}
    if not data:
        return jsonify({"error": "No valid config keys provided"}), 400

    # Ensure config directory exists
    config_dir = os.path.join(BASE_DIR, "config")
    os.makedirs(config_dir, exist_ok=True)

    config_path = os.path.join(config_dir, "config.json")

    # Load existing JSON config to merge (preserve keys not sent)
    existing = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    # Merge new values
    existing.update(data)

    # Atomic write: write to tmp then rename
    tmp_path = config_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        os.replace(tmp_path, config_path)
        try:
            os.chmod(config_path, 0o666)
        except OSError:
            pass
    except Exception as e:
        return jsonify({"error": f"Write failed: {e}"}), 500

    # Reload config module
    try:
        cfg.reload()
    except Exception as e:
        return jsonify({"error": f"Saved but reload failed: {e}"}), 500

    return jsonify({"status": "ok", "source": cfg.config_source()})


@app.get("/api/system")
def api_system():
    """System status: uptime, CPU temp."""
    info = {"uptime": "", "cpu_temp": "", "config_source": "env"}

    try:
        import config as cfg
        info["config_source"] = cfg.config_source()
    except Exception:
        pass

    # Uptime (Linux)
    try:
        with open("/proc/uptime", "r") as f:
            secs = float(f.read().split()[0])
            days = int(secs // 86400)
            hours = int((secs % 86400) // 3600)
            mins = int((secs % 3600) // 60)
            if days > 0:
                info["uptime"] = f"{days}d {hours}h {mins}m"
            elif hours > 0:
                info["uptime"] = f"{hours}h {mins}m"
            else:
                info["uptime"] = f"{mins}m"
    except Exception:
        info["uptime"] = "N/A"

    # CPU temp (Raspberry Pi)
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_mc = int(f.read().strip())
            info["cpu_temp"] = f"{temp_mc / 1000:.1f}°C"
    except Exception:
        info["cpu_temp"] = "N/A"

    # Load average
    try:
        load1, load5, load15 = os.getloadavg()
        info["load_avg"] = f"{load1:.2f} / {load5:.2f} / {load15:.2f}"
    except Exception:
        info["load_avg"] = "N/A"

    # Service uptime
    try:
        service = os.environ.get("SERVICE_NAME", "flight-tracker")
        result = subprocess.run(
            ["systemctl", "show", service, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5
        )
        ts_line = result.stdout.strip()  # ActiveEnterTimestamp=Sat 2026-06-14 00:15:32 EDT
        if "=" in ts_line:
            ts_str = ts_line.split("=", 1)[1].strip()
            if ts_str:
                from datetime import datetime as _dt
                # Parse systemctl timestamp
                start = subprocess.run(
                    ["date", "-d", ts_str, "+%s"],
                    capture_output=True, text=True, timeout=5
                )
                start_epoch = float(start.stdout.strip())
                svc_secs = _time.time() - start_epoch
                days = int(svc_secs // 86400)
                hours = int((svc_secs % 86400) // 3600)
                mins = int((svc_secs % 3600) // 60)
                if days > 0:
                    info["service_uptime"] = f"{days}d {hours}h {mins}m"
                elif hours > 0:
                    info["service_uptime"] = f"{hours}h {mins}m"
                else:
                    info["service_uptime"] = f"{mins}m"
    except Exception:
        info["service_uptime"] = "N/A"

    return jsonify(info)


# ---- API Usage ----

from utilities.api_usage import get_usage as _api_get_usage

@app.get("/api/usage")
def api_usage():
    """Return API usage data as JSON."""
    return jsonify(_api_get_usage())


@app.get("/usage")
def usage_page():
    return render_template("usage.html")


@app.post("/api/restart")
def api_restart():
    """Restart the flight-tracker service via systemctl."""
    service = os.environ.get("SERVICE_NAME", "flight-tracker")
    try:
        subprocess.Popen(["sudo", "systemctl", "restart", service])
        return jsonify({"status": "restarting", "service": service})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---- WiFi Management (concept from c0wsaysmoo/plane-tracker-rgb-pi) ----

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
            active = parts[0]
            ssid = parts[1]
            signal = parts[2]
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
            entry = {
                "ssid": ssid,
                "signal": int(signal) if signal.isdigit() else 0,
                "security": security.strip(),
                "active": active == "yes",
            }
            seen[ssid] = len(networks)
            networks.append(entry)
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
            "ip_address": ip_addr,
            "networks": networks,
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
        _time.sleep(2)
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
            active = parts[0]
            ssid = parts[1]
            signal = parts[2]
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
                "ssid": ssid,
                "signal": int(signal) if signal.isdigit() else 0,
                "security": security.strip(),
                "active": active == "yes",
            })
        networks.sort(key=lambda x: x["signal"], reverse=True)
        return jsonify({"networks": networks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/wifi/connect")
def wifi_connect():
    """Connect to a WiFi network. Body: { ssid, password }"""
    try:
        data = request.get_json(force=True) or {}
        ssid = (data.get("ssid") or "").strip()
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
        # Timeout usually means Pi switched networks — connection dropped, not failed
        return jsonify({
            "success": True,
            "switched": True,
            "message": "Connection in progress — the Pi may have switched networks. "
                       "Reconnect your device and navigate to the Pi's new IP.",
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
