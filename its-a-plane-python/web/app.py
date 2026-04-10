#!/usr/bin/python3
from flask import Flask, render_template, jsonify, send_from_directory, request
import json
import os

from FlightRadar24.api import FlightRadar24API

# /web is the folder that this file lives in
WEB_DIR = os.path.dirname(__file__)
BASE_DIR = os.path.abspath(os.path.join(WEB_DIR, ".."))

app = Flask(
    __name__,
    template_folder=os.path.join(WEB_DIR, "templates"),
    static_folder=os.path.join(WEB_DIR, "static")
)

# JSON flight logs (stored outside /web)
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
    """
    Try to find a live flight by callsign or flight number.
    Returns a dict with found=True/False and flight info if found.
    """
    callsign = callsign.strip().upper()
    airline_icao = callsign[:3] if len(callsign) >= 3 and callsign[:3].isalpha() else None

    try:
        api = FlightRadar24API()
        match = None

        # Strategy 1: airline filter (fast, works for mainline)
        if airline_icao:
            try:
                flights = api.get_flights(airline=airline_icao)
                match = next(
                    (f for f in flights if (f.callsign or "").upper() == callsign),
                    None,
                )
            except Exception:
                pass

        # Strategy 2: global search matching on number or callsign
        if not match:
            flights = api.get_flights()
            match = next(
                (f for f in flights if
                 (f.number or "").upper() == callsign or
                 (f.callsign or "").upper() == callsign),
                None,
            )

        if not match:
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


@app.get("/")
def index():
    return render_template("index.html")


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
    """Live lookup — check if a flight is currently findable before saving."""
    data = request.get_json(force=True)
    callsign = data.get("callsign", "").strip().upper()
    if not callsign:
        return jsonify({"found": False, "error": "No callsign provided"})
    result = lookup_flight(callsign)
    return jsonify(result)


@app.post("/tracked/set")
def tracked_set():
    data = request.get_json(force=True)
    callsign = data.get("callsign", "").strip().upper()
    try:
        with open(TRACKED_FILE, "w", encoding="utf-8") as f:
            json.dump({"callsign": callsign}, f)
        msg = f"Now tracking {callsign}." if callsign else "Tracking cleared."
        return jsonify({"message": msg})
    except Exception as e:
        return jsonify({"message": f"Error saving: {e}"}), 500


# Serve PNG map snapshots from /web/static/maps/
@app.get("/maps/<path:filename>")
def maps(filename):
    maps_dir = os.path.join(WEB_DIR, "static/maps")
    return send_from_directory(maps_dir, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
