"""
nationalparks.py — US National Park unit lookup using the NPS API.

Downloads all NPS park units on first run and caches as nationalparks.json
in the project root. Subsequent lookups are instant local distance math.

Requires NPS_API_KEY in config.py.
Free API key: https://developer.nps.gov/signup

Usage:
    from utilities.nationalparks import get_nearest_park
    park = get_nearest_park(36.1, -112.1)
    # {"name": "Grand Canyon", "distance_km": 4.2}
"""

import json
import math
import os
import threading

import requests

BASE_DIR    = os.path.dirname(os.path.dirname(__file__))
CACHE_FILE  = os.path.join(BASE_DIR, "nationalparks.json")
NPS_API_URL = "https://developer.nps.gov/api/v1/parks"

try:
    from config import NPS_API_KEY
except (ImportError, ModuleNotFoundError, NameError):
    NPS_API_KEY = ""

_db     = []
_loaded = False
_lock   = threading.Lock()


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _download_parks():
    """Download all NPS park units via paginated API calls."""
    if not NPS_API_KEY:
        print("[NPS] No API key — set NPS_API_KEY in config.py")
        return []

    print("[NPS] Downloading park units...")
    parks  = []
    start  = 0
    limit  = 100

    try:
        while True:
            r = requests.get(
                NPS_API_URL,
                params={
                    "api_key": NPS_API_KEY,
                    "limit":   limit,
                    "start":   start,
                    "fields":  "id",   # minimal fields — we only need name + coords
                },
                timeout=15,
            )
            r.raise_for_status()
            data  = r.json()
            units = data.get("data", [])
            total = int(data.get("total", 0))

            for unit in units:
                name      = unit.get("fullName", "").strip()
                lat_str   = unit.get("latitude", "")
                lon_str   = unit.get("longitude", "")
                if not name or not lat_str or not lon_str:
                    continue
                try:
                    lat = float(lat_str)
                    lon = float(lon_str)
                except ValueError:
                    continue
                parks.append([name, lat, lon])

            start += len(units)
            if start >= total or not units:
                break

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(parks, f)
        print(f"[NPS] {len(parks)} park units cached to nationalparks.json")
        return parks

    except Exception as e:
        print(f"[NPS] Download failed: {e}")
        return []


def _load():
    """Load from cache or download. Thread-safe, runs once."""
    global _db, _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    _db = json.load(f)
                print(f"[NPS] Loaded {len(_db)} parks from cache")
                _loaded = True
                return
            except Exception as e:
                print(f"[NPS] Cache load failed: {e} — re-downloading")
        _db     = _download_parks()
        _loaded = True


def get_nearest_park(latitude, longitude):
    """
    Find the nearest NPS park unit to the given coordinates.
    Returns {"name": str, "distance_km": float} or None.
    """
    _load()
    if not _db:
        return None

    best_name = None
    best_dist = float("inf")

    for name, plat, plon in _db:
        dist = _haversine_km(latitude, longitude, plat, plon)
        if dist < best_dist:
            best_dist = dist
            best_name = name

    if best_name is None:
        return None
    return {"name": best_name, "distance_km": best_dist}


def refresh():
    """Force re-download of park data."""
    global _db, _loaded
    with _lock:
        _loaded = False
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
    _load()
