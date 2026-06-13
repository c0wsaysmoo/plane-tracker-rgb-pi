"""
airlines.py — Airline name lookup from local database.
Downloads Airlines.csv from GitHub on first run, caches as airlines.json.
Run this file directly to download: python3 airlines.py

Source: https://github.com/rikgale/ICAOList
"""

import json
import os
import requests

BASE_DIR   = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "airlines.json")
CSV_URL    = "https://raw.githubusercontent.com/npow/airline-codes/master/airlines.json"

# Pretty display names that override the database for common regionals
_OVERRIDES = {
    # American Eagle operators
    "ENY": "American Eagle",   "JIA": "American Eagle",
    "PDT": "American Eagle",   "PSA": "American Eagle",
    # United Express operators
    "GJS": "United Express",   "CPZ": "United Express",
    "ASH": "United Express",   "G7":  "United Express",
    # Delta Connection operators
    "EDV": "Delta Connection", "ASQ": "Delta Connection",
    # Dual-brand (brand resolved by FlightStats/AirLabs per flight)
    "RPA": "Republic Airways",
    "SKW": "SkyWest Airlines",
    # Alaska
    "QXE": "Horizon Air",
    # Other
    "TIV": "Thrive Aviation",
}

_db               = {}
_icao_to_iata_map = {}   # 3-letter ICAO → 2-letter IATA, built lazily
_loaded           = False
_last_refresh     = 0.0      # epoch seconds of last successful download
_REFRESH_COOLDOWN = 86400    # only re-download once per 24 hours on a miss


def _download_and_build():
    print("[Airlines] Downloading airline database...")
    try:
        r = requests.get(CSV_URL, timeout=30)
        r.raise_for_status()
        airlines = r.json()
        db = {}
        for a in airlines:
            name = a.get("name", "").strip()
            icao = a.get("icao", "").strip().upper()
            iata = a.get("iata", "").strip().upper()
            if not name or name == "Private flight":
                continue
            if icao and icao != "N/A" and len(icao) == 3:
                db[icao] = _OVERRIDES.get(icao, name)
            if iata and iata != "-" and len(iata) == 2:
                db[iata] = _OVERRIDES.get(icao, name)
        # Apply overrides
        db.update(_OVERRIDES)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f)
        import time as _time
        global _last_refresh
        _last_refresh = _time.time()
        print(f"[Airlines] Database built — {len(db)} entries cached")
        return db
    except Exception as e:
        print(f"[Airlines] Download failed: {e} — using built-in list")
        return dict(_OVERRIDES)


def _build_icao_iata_map():
    global _icao_to_iata_map
    name_to_iata = {name: code for code, name in _db.items() if len(code) == 2}
    _icao_to_iata_map = {
        code: name_to_iata[name]
        for code, name in _db.items()
        if len(code) == 3 and name in name_to_iata
    }


def _load():
    global _db, _loaded
    if _loaded:
        return
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _db = json.load(f)
            _loaded = True
            _build_icao_iata_map()
            return
        except Exception:
            pass
    _db = _download_and_build()
    _loaded = True
    _build_icao_iata_map()


def airline_icao_to_iata(icao3):
    """Convert 3-letter airline ICAO code to 2-letter IATA code (e.g. DAL → DL)."""
    if not _icao_to_iata_map:
        _load()
    return _icao_to_iata_map.get(icao3.upper(), "")


def get_airline_name(icao):
    """Look up airline display name by ICAO code.
    On a cache miss, re-downloads the database if it hasn't been refreshed
    in the last 24 hours, then retries once."""
    global _db, _loaded
    if not icao:
        return ""
    _load()
    name = _db.get(icao.upper(), "")
    if name:
        return name

    # Miss — try a refresh if cooldown has elapsed
    import time as _time
    if _time.time() - _last_refresh > _REFRESH_COOLDOWN:
        print(f"[Airlines] '{icao}' not found — refreshing database...")
        _loaded = False
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        _db = _download_and_build()
        _loaded = True
        name = _db.get(icao.upper(), "")
        if name:
            print(f"[Airlines] '{icao}' found after refresh: {name}")
        else:
            print(f"[Airlines] '{icao}' still not found after refresh")
    return name


def refresh():
    """Force re-download of airline database."""
    global _db, _loaded
    _loaded = False
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
    _load()


if __name__ == "__main__":
    refresh()
    for code in ["UAL","AAL","DAL","SKW","GJS","RPA","ENY","QTR","ETD","UAE","DLH","KAL"]:
        print(f"{code}: {get_airline_name(code)}")
