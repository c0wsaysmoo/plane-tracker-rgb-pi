"""
landmarks.py — Nearest landmark lookup combining NPS parks and GeoNames cities.

Priority: National parks within 30km, then fall back to nearest city.

NPS parks downloaded from https://developer.nps.gov/api/v1/parks (paginated).
Cached as nationalparks.json. Requires NPS_API_KEY env var.

GeoNames cities via existing utilities/cities.py.
"""

import json
import logging
import math
import os
import threading

import requests

from utilities.cities import get_nearest_city

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PARKS_CACHE = os.path.join(BASE_DIR, "nationalparks.json")

NPS_API_KEY = os.environ.get("NPS_API_KEY", "")
NPS_API_URL = "https://developer.nps.gov/api/v1/parks"
PARKS_RADIUS_KM = 30  # Only show park name if plane is within this distance

# Suffixes to strip from park full names for display
_STRIP_SUFFIXES = [
    " National Park and Preserve",
    " National Park & Preserve",
    " National Historical Park",
    " National Historic Site",
    " National Monument and Preserve",
    " National Monument & Preserve",
    " National Monument",
    " National Seashore",
    " National Lakeshore",
    " National Recreation Area",
    " National Preserve",
    " National Park",
    " National Battlefield",
    " National Memorial",
    " National Scenic River",
    " National River",
    " National Wild and Scenic River",
]

# In-memory list: [[name, lat, lon], ...]
_parks_db = []
_parks_loaded = False
_parks_lock = threading.Lock()


def _haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two points."""
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _strip_park_name(full_name):
    """Remove common NPS suffixes for shorter display."""
    for suffix in _STRIP_SUFFIXES:
        if full_name.endswith(suffix):
            return full_name[: -len(suffix)]
    return full_name


def _download_parks():
    """Download all parks from NPS API (paginated, 50 per page)."""
    if not NPS_API_KEY:
        logger.warning("[Landmarks] NPS_API_KEY not set — skipping parks download")
        return []

    logger.info("[Landmarks] Downloading NPS parks database...")
    parks = []
    start = 0
    limit = 50

    while True:
        try:
            r = requests.get(
                NPS_API_URL,
                params={"start": start, "limit": limit, "api_key": NPS_API_KEY},
                headers={"Accept": "application/json"},
                timeout=(10, 30),
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error(f"[Landmarks] NPS API error at offset {start}: {e}")
            break

        page_parks = data.get("data", [])
        if not page_parks:
            break

        for p in page_parks:
            try:
                lat_str = p.get("latitude", "")
                lon_str = p.get("longitude", "")
                if not lat_str or not lon_str:
                    continue
                lat = float(lat_str)
                lon = float(lon_str)
                name = _strip_park_name(p.get("fullName", p.get("name", "Unknown")))
                parks.append([name, lat, lon])
            except (ValueError, TypeError):
                continue

        total = int(data.get("total", "0"))
        start += limit
        if start >= total:
            break

    # Cache to disk
    try:
        with open(PARKS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"parks": parks}, f)
        logger.info(f"[Landmarks] Cached {len(parks)} parks to nationalparks.json")
    except Exception as e:
        logger.error(f"[Landmarks] Cache write failed: {e}")

    return parks


def _load_parks():
    """Load parks from cache or download. Thread-safe."""
    global _parks_db, _parks_loaded
    if _parks_loaded:
        return

    with _parks_lock:
        if _parks_loaded:
            return

        if os.path.exists(PARKS_CACHE):
            try:
                with open(PARKS_CACHE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                _parks_db = raw.get("parks", [])
                _parks_loaded = True
                logger.info(f"[Landmarks] Loaded {len(_parks_db)} parks from cache")
                return
            except Exception as e:
                logger.error(f"[Landmarks] Cache load failed: {e}")

        _parks_db = _download_parks()
        _parks_loaded = True


def get_nearest_landmark(latitude, longitude):
    """
    Find the nearest landmark (park or city) to the given coordinates.

    Checks NPS parks within 30km first, then falls back to GeoNames city.

    Returns {"name": str, "distance_km": float, "type": "park"|"city"} or None.
    """
    _load_parks()

    # Check parks first (within radius)
    if _parks_db:
        best_park = None
        best_dist = float("inf")
        for name, plat, plon in _parks_db:
            dist = _haversine_km(latitude, longitude, plat, plon)
            if dist < best_dist:
                best_dist = dist
                best_park = name
        if best_park and best_dist <= PARKS_RADIUS_KM:
            return {"name": best_park, "distance_km": best_dist, "type": "park"}

    # Fall back to nearest city
    city = get_nearest_city(latitude, longitude)
    if city:
        return {"name": city["name"], "distance_km": city["distance_km"], "type": "city"}

    return None


def preload():
    """Background preload — call from main thread at startup."""
    t = threading.Thread(target=_load_parks, daemon=True)
    t.start()
