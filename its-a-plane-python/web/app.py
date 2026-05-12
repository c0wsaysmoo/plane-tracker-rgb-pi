#!/usr/bin/python3
from flask import Flask, render_template, jsonify, send_from_directory, request
import json
import os
import sys

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
            from utilities.airlabs import get_flight_schedule
            sched = get_flight_schedule(original_callsign)
            if sched:
                return {
                    "found": True,
                    "scheduled": True,
                    "callsign": callsign,
                    "number": sched.get("flight_number", callsign),
                    "airline": "",
                    "origin": sched.get("origin", "???"),
                    "destination": sched.get("destination", "???"),
                    "dep_time": sched.get("dep_time", ""),
                    "status": sched.get("status", ""),
                    "summary": f"Scheduled: {sched.get('flight_number', callsign)} {sched.get('origin', '?')}→{sched.get('destination', '?')} Dep {sched.get('dep_time', '?')}",
                }
            return {"found": False}

        # Get full details for airline name and route
        details = api.get_flight_details(match)
        match.set_flight_details(details)

        airline = match.airline_name or ""
        origin = match.origin_airport_iata or "???"
        destination = match.destination_airport_iata or "???"
        number = match.number or callsign

        return {
            "found": True,
            "callsign": match.callsign,
            "number": number,
            "airline": airline,
            "origin": origin,
            "destination": destination,
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
    try:
        with open(TRACKED_FILE, "w", encoding="utf-8") as f:
            json.dump({"callsign": callsign}, f)
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


# Flight counter and stats (concept from c0wsaysmoo/plane-tracker-rgb-pi)
COUNTER_FILE = os.path.join(DATA_DIR, "flight_counter.json")


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
                by_hour[flight.get("hour", 0)] += 1
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
