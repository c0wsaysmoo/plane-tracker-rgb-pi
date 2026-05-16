"""
airports.py — Local airport coordinate lookup.
Downloads airport-codes.csv from GitHub on first run and caches
as airports.json in the project root. Subsequent lookups are instant.

Source: https://github.com/datasets/airport-codes
No API key required. Run once, works offline forever after.

Usage:
    from utilities.airports import get_airport_coords
    coords = get_airport_coords("ORD")  # {"lat": 41.978, "lon": -87.904}
    coords = get_airport_coords("KORD") # same result
"""

import csv
import json
import os
import requests
from io import StringIO

BASE_DIR    = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE  = os.path.join(BASE_DIR, "airports.json")
CSV_URL     = "https://raw.githubusercontent.com/datasets/airport-codes/master/data/airport-codes.csv"

# In-memory lookup: both IATA and ICAO -> {lat, lon, name}
_db               = {}
_loaded           = False
_last_refresh     = 0.0      # epoch seconds of last successful download
_REFRESH_COOLDOWN = 86400    # only re-download once per 24 hours on a miss


def _download_and_build():
    """Download CSV and build IATA/ICAO -> coords lookup."""
    print("[Airports] Downloading airport database...")
    try:
        r = requests.get(CSV_URL, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(StringIO(r.text))
        db = {}
        for row in reader:
            # Parse coordinates — stored as "lat,lon" in coordinates field
            coords_str = row.get("coordinates", "")
            if not coords_str:
                continue
            try:
                # Dataset "coordinates" field is "latitude,longitude"
                parts = coords_str.split(",")
                lat = float(parts[0].strip())
                lon = float(parts[1].strip())
            except (ValueError, AttributeError, IndexError):
                continue

            # Index by IATA code
            iata = row.get("iata_code", "").strip().upper()
            icao = row.get("ident", "").strip().upper()

            name = row.get("name", "").strip()
            entry = {"lat": lat, "lon": lon, "name": name}

            if iata and iata != "0":
                db[iata] = entry

            # Index by ICAO code too
            if icao:
                db[icao] = entry

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f)
        import time as _time
        global _last_refresh
        _last_refresh = _time.time()
        print(f"[Airports] Database built — {len(db)} entries cached to airports.json")
        return db

    except Exception as e:
        print(f"[Airports] Download failed: {e}")
        return {}


def _load():
    """Load from cache file or download if not present."""
    global _db, _loaded
    if _loaded:
        return

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _db = json.load(f)

            _loaded = True
            return
        except Exception as e:
            print(f"[Airports] Cache load failed: {e} — re-downloading")

    _db = _download_and_build()
    _loaded = True


def get_airport_coords(code):
    """
    Look up airport coordinates by IATA or ICAO code.
    Returns {"lat": float, "lon": float} or empty dict if not found.
    On a cache miss, re-downloads the database if cooldown has elapsed, then retries once.

    Examples:
        get_airport_coords("ORD")   -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("KORD")  -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("EGLL")  -> {"lat": 51.477, "lon": -0.461}
    """
    global _db, _loaded
    _load()
    if not code:
        return {}

    code = code.strip().upper()

    def _lookup(db, c):
        if c in db:
            return db[c]
        if len(c) == 4 and c[0] == "K":
            return db.get(c[1:], None)
        if len(c) == 3:
            return db.get("K" + c, None)
        return None

    result = _lookup(_db, code)
    if result:
        return result

    # Miss — try a refresh if cooldown has elapsed
    import time as _time
    if _time.time() - _last_refresh > _REFRESH_COOLDOWN:
        print(f"[Airports] '{code}' not found — refreshing database...")
        _loaded = False
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        _db = _download_and_build()
        _loaded = True
        result = _lookup(_db, code)
        if result:
            print(f"[Airports] '{code}' found after refresh")
        else:
            print(f"[Airports] '{code}' still not found after refresh")
        return result or {}

    return {}


def get_airport_name(code):
    """
    Look up airport name by IATA or ICAO code.
    Returns a string like "Chicago O'Hare International Airport" or "" if not found.
    Triggers a refresh on miss, same as get_airport_coords.
    """
    if not code:
        return ""
    entry = get_airport_coords(code)  # handles load, fallback, and refresh
    return entry.get("name", "") if entry else ""


def icao_to_iata(icao_code):
    """Convert 4-letter ICAO code to 3-letter IATA code using the airports database.
    Falls back to stripping leading K for US airports if not found."""
    if not icao_code or len(icao_code) != 4:
        return icao_code or "?"
    _load()
    # Search for a 3-letter key that maps to same coords as this ICAO
    icao_coords = _db.get(icao_code.upper())
    if icao_coords:
        for code, coords in _db.items():
            if (len(code) == 3 and
                abs(coords.get("lat", 0) - icao_coords.get("lat", 0)) < 0.01 and
                abs(coords.get("lon", 0) - icao_coords.get("lon", 0)) < 0.01):
                return code
    # Fall back to stripping K for US airports
    if icao_code[0] == "K":
        return icao_code[1:]
    return icao_code


def get_nearest_airport(lat, lon, max_dist_km=15):
    """
    Find the nearest airport IATA code to given coordinates.
    Returns IATA code string or None if nothing within max_dist_km.
    """
    _load()
    if not lat or not lon:
        return None

    best_code = None
    best_dist = float("inf")

    for code, entry in _db.items():
        if len(code) != 3:  # IATA codes only
            continue
        dlat = entry.get("lat", 0) - lat
        dlon = entry.get("lon", 0) - lon
        dist = (dlat**2 + dlon**2) ** 0.5  # degrees
        if dist < best_dist:
            best_dist = dist
            best_code = code

    # Convert rough degree distance to km (1 degree ~ 111km)
    if best_dist * 111 <= max_dist_km:
        return best_code
    return None


def refresh():
    """Force re-download of airport database."""
    global _db, _loaded
    _loaded = False
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    _load()


if __name__ == "__main__":
    # Test
    for code in ["ORD", "KORD", "JFK", "EGLL", "HND", "LAX", "CHS"]:
        coords = get_airport_coords(code)
        print(f"{code}: {coords}")
