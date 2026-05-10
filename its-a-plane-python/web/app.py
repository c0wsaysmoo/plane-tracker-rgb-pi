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
    """Check if a callsign is currently airborne via OpenSky (free, no credits)."""
    data     = request.get_json(force=True)
    callsign = data.get("callsign", "").strip().upper()
    if not callsign:
        return jsonify({"found": False, "error": "No callsign provided"})
    try:
        from utilities.opensky import OpenSkyClient
        os_client = OpenSkyClient()
        state = os_client.find_callsign(callsign)
        if state:
            return jsonify({
                "found":    True,
                "callsign": callsign,
                "summary":  f"{callsign} is airborne",
            })
        else:
            return jsonify({
                "found":   False,
                "callsign": callsign,
                "summary":  f"{callsign} not found — not airborne? Will track when live.",
            })
    except Exception as e:
        return jsonify({"found": False, "error": str(e)})

@app.post("/tracked/set")
def tracked_set():
    data     = request.get_json(force=True)
    callsign = data.get("callsign", "").strip().upper()
    try:
        with open(TRACKED_FILE, "w", encoding="utf-8") as f:
            json.dump({"callsign": callsign}, f)
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
