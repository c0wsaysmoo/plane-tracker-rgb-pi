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
import threading
import requests
from io import StringIO

BASE_DIR    = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE  = os.path.join(BASE_DIR, "airports.json")
CUSTOM_FILE = os.path.join(BASE_DIR, "airports_custom.json")
CSV_URL     = "https://raw.githubusercontent.com/datasets/airport-codes/master/data/airport-codes.csv"

# In-memory lookup: both IATA and ICAO -> {lat, lon, name}
_db               = {}
_loaded           = False
_last_refresh     = 0.0      # epoch seconds of last successful download
_REFRESH_COOLDOWN = 86400    # only re-download once per 24 hours on a miss
_not_found        = set()    # codes confirmed missing even after a fresh download
_refresh_lock     = threading.Lock()
_refresh_pending  = False    # True while a background refresh thread is running


def _apply_custom(db):
    """Merge airports_custom.json entries on top of db in-place."""
    if not os.path.exists(CUSTOM_FILE):
        return
    try:
        with open(CUSTOM_FILE, "r", encoding="utf-8") as f:
            custom = json.load(f)
        for code, entry in custom.items():
            db[code.upper().strip()] = entry
        print(f"[Airports] Applied {len(custom)} custom entries from airports_custom.json")
    except Exception as e:
        print(f"[Airports] Could not load airports_custom.json: {e}")


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

        _apply_custom(db)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f)
        import time as _time
        global _last_refresh, _not_found
        _last_refresh = _time.time()
        _not_found = set()  # reset so newly-added airports can be found
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
            _apply_custom(_db)
            _loaded = True
            return
        except Exception as e:
            print(f"[Airports] Cache load failed: {e} — re-downloading")

    _db = _download_and_build()
    _loaded = True


def _background_refresh():
    """Download a fresh airport database in a background thread (non-blocking)."""
    global _db, _loaded, _refresh_pending
    with _refresh_lock:
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
            new_db = _download_and_build()
            if new_db:
                _db = new_db
                _loaded = True
        finally:
            _refresh_pending = False


def get_airport_coords(code):
    """
    Look up airport coordinates by IATA or ICAO code.
    Returns {"lat": float, "lon": float} or empty dict if not found.
    On a cache miss, kicks off a background refresh (non-blocking) if cooldown has
    elapsed; returns {} immediately so the display never freezes.

    Examples:
        get_airport_coords("ORD")   -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("KORD")  -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("EGLL")  -> {"lat": 51.477, "lon": -0.461}
    """
    global _db, _loaded, _refresh_pending
    _load()
    if not code:
        return {}

    code = code.strip().upper()

    # Placeholder values used when route lookup fails — never valid airport codes
    if code in ("?", "???", "N/A", "UNK", "UNKN", "ZZZZ"):
        return {}

    # Skip codes already confirmed missing (cleared after a successful refresh)
    if code in _not_found:
        return {}

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

    # Miss — schedule a background refresh if cooldown has elapsed and none is running
    import time as _time
    if _time.time() - _last_refresh > _REFRESH_COOLDOWN and not _refresh_pending:
        _refresh_pending = True
        print(f"[Airports] '{code}' not found — scheduling background refresh...")
        t = threading.Thread(target=_background_refresh, daemon=True)
        t.start()
    else:
        # Cooldown active or refresh already in progress — mark as not found for now
        _not_found.add(code)
        print(f"[Airports] '{code}' not found in database")

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
