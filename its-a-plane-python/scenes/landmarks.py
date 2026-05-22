"""
landmarks.py — Nearest landmark or city lookup.

Strategy:
  1. Check NPS parks dataset — if a park is within PARK_RADIUS_KM, show it.
  2. Fall back to Nominatim reverse geocoding for nearest city/town.

NPS park data is loaded once at startup (local cache after first download).
Nominatim is queried in the background every REQUERY_AFTER_KM of movement.

Usage:
    from utilities.landmarks import get_nearest_landmark
    nearest = get_nearest_landmark(36.1, -112.1)
    # {"name": "Grand Canyon"}
"""

import math
import threading

import requests

NOMINATIM_URL    = "https://nominatim.openstreetmap.org/reverse"
PARK_RADIUS_KM   = 30    # show park name only if within this distance
REQUERY_AFTER_KM = 50    # re-query Nominatim when plane moves this far
MAX_NAME_LEN     = 20

_HEADERS = {"User-Agent": "plane-tracker-rgb-pi/1.0"}

# Suffixes to strip from park names before display
_STRIP_SUFFIXES = [
    "National Monument and Preserve",
    "National Monument & Preserve",
    "National Recreation Area",
    "National Historical Park",
    "National Historic Site",
    "National Memorial",
    "National Monument",
    "National Seashore",
    "National Lakeshore",
    "National Parkway",
    "National Reserve",
    "National Forest",
    "National Refuge",
    "National Park",
    "State Historic Park",
    "State Recreation Area",
    "State Forest",
    "State Park",
    "Provincial Park",
    "Regional Park",
    "Country Park",
    "Nature Reserve",
    "Wildlife Refuge",
    "Wilderness Area",
    "Historic Site",
    "Heritage Site",
]

# Nominatim cache state
_nom_result    = None
_nom_query_lat = None
_nom_query_lon = None
_nom_fetching  = False
_nom_lock      = threading.Lock()


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _clean_name(name):
    """Strip common suffixes then truncate to MAX_NAME_LEN."""
    if not name:
        return name
    stripped = name.strip()
    for suffix in _STRIP_SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].rstrip(" ,–-")
            break
    if len(stripped) > MAX_NAME_LEN:
        stripped = stripped[:MAX_NAME_LEN].rstrip()
    return stripped or name[:MAX_NAME_LEN]


# ---------------------------------------------------------------------------
# NPS park lookup
# ---------------------------------------------------------------------------

def _nearest_park(lat, lon):
    """Return (cleaned_name, distance_km) of nearest NPS park, or (None, None)."""
    try:
        from utilities.nationalparks import get_nearest_park
    except ModuleNotFoundError:
        from nationalparks import get_nearest_park

    result = get_nearest_park(lat, lon)
    if result and result["distance_km"] <= PARK_RADIUS_KM:
        return _clean_name(result["name"]), result["distance_km"]
    return None, None


# ---------------------------------------------------------------------------
# Nominatim city fallback
# ---------------------------------------------------------------------------

def _nominatim_fetch(lat, lon):
    """Fetch nearest city from Nominatim. Runs in background thread."""
    global _nom_result, _nom_query_lat, _nom_query_lon, _nom_fetching
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json",
                    "zoom": 10, "addressdetails": 1},
            headers=_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        data    = r.json()
        address = data.get("address", {})

        name = None
        for key in ("city", "town", "village", "municipality", "suburb"):
            name = address.get(key)
            if name:
                break
        if not name:
            # last resort — county is better than nothing
            name = address.get("county") or data.get("display_name", "").split(",")[0]

        cleaned = _clean_name(name)
        state   = address.get("ISO3166-2-lvl4", "")
        if state and "-" in state:
            state = state.split("-")[-1]
        if state and len(cleaned) + len(state) + 2 <= MAX_NAME_LEN:
            cleaned = f"{cleaned}, {state}"

        #print(f"[Landmarks] Nominatim: {cleaned}")
        with _nom_lock:
            _nom_result    = cleaned
            _nom_query_lat = lat
            _nom_query_lon = lon

    except Exception as e:
        print(f"[Landmarks] Nominatim failed: {e}")

    with _nom_lock:
        _nom_fetching = False


def _ensure_nominatim(lat, lon):
    """Trigger background Nominatim fetch if plane has moved enough."""
    global _nom_fetching
    with _nom_lock:
        stale = (_nom_query_lat is None or
                 _haversine_km(lat, lon, _nom_query_lat, _nom_query_lon) > REQUERY_AFTER_KM)
        if stale and not _nom_fetching:
            _nom_fetching = True
            threading.Thread(
                target=_nominatim_fetch,
                args=(lat, lon),
                daemon=True,
            ).start()
        return _nom_result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_nearest_landmark(latitude, longitude, **kwargs):
    """
    Return nearest NPS park (if within PARK_RADIUS_KM) or nearest city.

    Returns {"name": str} or None.
    Extra kwargs accepted but ignored for call-site compatibility.
    """
    park_name, park_dist = _nearest_park(latitude, longitude)
    if park_name:
        return {"name": park_name}

    city = _ensure_nominatim(latitude, longitude)
    if city:
        return {"name": city}

    return None


def clear_cache():
    """Reset Nominatim cache (call when tracked flight changes)."""
    global _nom_result, _nom_query_lat, _nom_query_lon, _nom_fetching
    with _nom_lock:
        _nom_result    = None
        _nom_query_lat = None
        _nom_query_lon = None
        _nom_fetching  = False
