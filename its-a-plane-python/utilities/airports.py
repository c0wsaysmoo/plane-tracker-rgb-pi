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
CSV_URL     = "https://raw.githubusercontent.com/datasets/airport-codes/master/data/airport-codes.csv"

# Cache version — increment to force rebuild (e.g. when coordinate parsing changes)
# v2: confirmed coordinates field is "latitude, longitude" order
CACHE_VERSION = 2

# In-memory lookup: both IATA and ICAO -> {lat, lon}
_db = {}
_loaded = False
_load_lock = threading.Lock()
_not_found = set()          # codes confirmed missing; cleared on successful refresh
_refresh_pending = False    # True while a background refresh thread is running


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

            if iata and iata != "0":
                db[iata] = {"lat": lat, "lon": lon}

            # Index by ICAO code too
            if icao:
                db[icao] = {"lat": lat, "lon": lon}

        cache_data = {"_version": CACHE_VERSION, "airports": db}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f)
        print(f"[Airports] Database built — {len(db)} entries cached to airports.json (v{CACHE_VERSION})")
        return db

    except Exception as e:
        print(f"[Airports] Download failed: {e}")
        return {}


def _load():
    """Load from cache file or download if not present. Thread-safe."""
    global _db, _loaded
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and raw.get("_version") == CACHE_VERSION:
                    _db = raw.get("airports", {})
                    _loaded = True
                    return
                else:
                    version_found = raw.get("_version", "none") if isinstance(raw, dict) else "legacy"
                    print(f"[Airports] Cache version mismatch (found: {version_found}, need: {CACHE_VERSION}) — rebuilding")
            except Exception as e:
                print(f"[Airports] Cache load failed: {e} — re-downloading")
        _db = _download_and_build()
        _loaded = True


def _background_refresh():
    """Download a fresh airport database in a background thread (non-blocking)."""
    global _db, _loaded, _refresh_pending, _not_found
    with _load_lock:
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
            new_db = _download_and_build()
            if new_db:
                _db = new_db
                _loaded = True
                _not_found = set()  # reset so newly-added airports can be found
        finally:
            _refresh_pending = False


def get_airport_coords(code):
    """
    Look up airport coordinates by IATA or ICAO code.
    Returns {"lat": float, "lon": float} or empty dict if not found.
    On a cache miss, kicks off a non-blocking background refresh so the
    display never freezes.

    Examples:
        get_airport_coords("ORD")   -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("KORD")  -> {"lat": 41.978, "lon": -87.904}
        get_airport_coords("EGLL")  -> {"lat": 51.477, "lon": -0.461}
    """
    global _refresh_pending
    _load()
    if not code:
        return {}

    code = code.strip().upper()

    # Placeholder values — never valid airport codes
    if code in ("?", "???", "N/A", "UNK", "UNKN", "ZZZZ"):
        return {}

    # Skip codes already confirmed missing (cleared after a successful refresh)
    if code in _not_found:
        return {}

    def _lookup(c):
        if c in _db:
            return _db[c]
        # Try IATA from ICAO (strip leading K for US airports)
        if len(c) == 4 and c[0] == "K":
            iata = c[1:]
            if iata in _db:
                return _db[iata]
        # Try ICAO from IATA (prepend K for US 3-letter codes)
        if len(c) == 3:
            icao = "K" + c
            if icao in _db:
                return _db[icao]
        return None

    result = _lookup(code)
    if result:
        return result

    # Miss — schedule a non-blocking background refresh if none is running
    if not _refresh_pending:
        _refresh_pending = True
        print(f"[Airports] '{code}' not found — scheduling background refresh...")
        t = threading.Thread(target=_background_refresh, daemon=True)
        t.start()
    else:
        _not_found.add(code)
        print(f"[Airports] '{code}' not found in database")

    return {}


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
