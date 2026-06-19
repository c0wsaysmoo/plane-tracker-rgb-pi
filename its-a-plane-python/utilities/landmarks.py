"""
landmarks.py — Nearest landmark lookup combining NPS parks, Nominatim, and cities.

Priority chain:
  1. NPS parks within 30km — named national/state parks from NPS API.
  2. Nominatim reverse geocode — background thread, re-queried every 15km of movement.
  3. Local cities.json nearest-neighbour — via utilities/cities.py.
  4. Country name from country_code — for remote land areas.
  5. Ocean/sea name from coordinates — bounding-box lookup over water.

NPS parks downloaded from https://developer.nps.gov/api/v1/parks (paginated).
Cached as nationalparks.json. Requires NPS_API_KEY env var.

GeoNames cities via existing utilities/cities.py.
Nominatim queried in the background every REQUERY_AFTER_KM of movement.
"""

import json
import logging
import os
import threading

import requests

try:
    from utilities.api_usage import log_call as _log_api
except ImportError:
    _log_api = lambda source: None

from utilities.cities import get_nearest_city, _haversine_km

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
PARKS_CACHE = os.path.join(BASE_DIR, "nationalparks.json")

NPS_API_URL = "https://developer.nps.gov/api/v1/parks"
PARKS_RADIUS_KM = 30  # Only show park name if plane is within this distance
REQUERY_AFTER_KM = 15  # Re-query Nominatim after this much movement
MAX_NAME_LEN = 24  # Truncate display names to fit LED matrix

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_NOM_HEADERS = {"User-Agent": "plane-tracker-rgb-pi/1.0"}

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

# ---------------------------------------------------------------------------
# Country code -> English name lookup
# ---------------------------------------------------------------------------

_COUNTRY_NAMES = {
    "af": "Afghanistan", "al": "Albania", "dz": "Algeria", "ad": "Andorra",
    "ao": "Angola", "ar": "Argentina", "am": "Armenia", "au": "Australia",
    "at": "Austria", "az": "Azerbaijan", "bs": "Bahamas", "bh": "Bahrain",
    "bd": "Bangladesh", "by": "Belarus", "be": "Belgium", "bz": "Belize",
    "bj": "Benin", "bt": "Bhutan", "bo": "Bolivia", "ba": "Bosnia",
    "bw": "Botswana", "br": "Brazil", "bn": "Brunei", "bg": "Bulgaria",
    "bf": "Burkina Faso", "bi": "Burundi", "kh": "Cambodia", "cm": "Cameroon",
    "ca": "Canada", "cf": "C. African Rep.", "td": "Chad", "cl": "Chile",
    "cn": "China", "co": "Colombia", "cg": "Congo", "cd": "DR Congo",
    "cr": "Costa Rica", "hr": "Croatia", "cu": "Cuba", "cy": "Cyprus",
    "cz": "Czech Republic", "dk": "Denmark", "dj": "Djibouti", "do": "Dominican Rep.",
    "ec": "Ecuador", "eg": "Egypt", "sv": "El Salvador", "er": "Eritrea",
    "ee": "Estonia", "et": "Ethiopia", "fj": "Fiji", "fi": "Finland",
    "fr": "France", "ga": "Gabon", "gm": "Gambia", "ge": "Georgia",
    "de": "Germany", "gh": "Ghana", "gr": "Greece", "gl": "Greenland",
    "gt": "Guatemala", "gn": "Guinea", "gy": "Guyana", "ht": "Haiti",
    "hn": "Honduras", "hu": "Hungary", "is": "Iceland", "in": "India",
    "id": "Indonesia", "ir": "Iran", "iq": "Iraq", "ie": "Ireland",
    "il": "Israel", "it": "Italy", "jm": "Jamaica", "jp": "Japan",
    "jo": "Jordan", "kz": "Kazakhstan", "ke": "Kenya", "kp": "North Korea",
    "kr": "South Korea", "kw": "Kuwait", "kg": "Kyrgyzstan", "la": "Laos",
    "lv": "Latvia", "lb": "Lebanon", "ls": "Lesotho", "lr": "Liberia",
    "ly": "Libya", "lt": "Lithuania", "lu": "Luxembourg", "mg": "Madagascar",
    "mw": "Malawi", "my": "Malaysia", "mv": "Maldives", "ml": "Mali",
    "mt": "Malta", "mr": "Mauritania", "mx": "Mexico", "md": "Moldova",
    "mn": "Mongolia", "me": "Montenegro", "ma": "Morocco", "mz": "Mozambique",
    "mm": "Myanmar", "na": "Namibia", "np": "Nepal", "nl": "Netherlands",
    "nz": "New Zealand", "ni": "Nicaragua", "ne": "Niger", "ng": "Nigeria",
    "no": "Norway", "om": "Oman", "pk": "Pakistan", "pa": "Panama",
    "pg": "Papua New Guinea", "py": "Paraguay", "pe": "Peru",
    "ph": "Philippines", "pl": "Poland", "pt": "Portugal", "qa": "Qatar",
    "ro": "Romania", "ru": "Russia", "rw": "Rwanda", "sa": "Saudi Arabia",
    "sn": "Senegal", "rs": "Serbia", "sl": "Sierra Leone", "sg": "Singapore",
    "sk": "Slovakia", "si": "Slovenia", "so": "Somalia", "za": "South Africa",
    "ss": "South Sudan", "es": "Spain", "lk": "Sri Lanka", "sd": "Sudan",
    "sr": "Suriname", "se": "Sweden", "ch": "Switzerland", "sy": "Syria",
    "tw": "Taiwan", "tj": "Tajikistan", "tz": "Tanzania", "th": "Thailand",
    "tl": "Timor-Leste", "tg": "Togo", "to": "Tonga", "tt": "Trinidad",
    "tn": "Tunisia", "tr": "Turkey", "tm": "Turkmenistan", "ug": "Uganda",
    "ua": "Ukraine", "ae": "UAE", "gb": "United Kingdom", "us": "United States",
    "uy": "Uruguay", "uz": "Uzbekistan", "ve": "Venezuela", "vn": "Vietnam",
    "ye": "Yemen", "zm": "Zambia", "zw": "Zimbabwe",
    "aq": "Antarctica", "eh": "Western Sahara", "ps": "Palestine",
    "xk": "Kosovo", "cv": "Cape Verde", "km": "Comoros",
}


def _country_name(country_code):
    """Look up English country name from ISO 3166-1 alpha-2 code."""
    if not country_code:
        return None
    name = _COUNTRY_NAMES.get(country_code.lower())
    if not name:
        return None
    if len(name) <= MAX_NAME_LEN:
        return name
    return name[:MAX_NAME_LEN].rstrip()


# ---------------------------------------------------------------------------
# Ocean/sea detection — coordinate-based bounding boxes
# Ordered most-specific first (seas before oceans).
# ---------------------------------------------------------------------------

_OCEAN_REGIONS = [
    # Seas and gulfs (more specific, checked first)
    ("Caribbean Sea",       (8,  26, -87, -59)),
    ("Gulf of Mexico",      (18, 31, -98, -80)),
    ("Mediterranean Sea",   (30, 47,  -6,  37)),
    ("North Sea",           (51, 62,  -4,  13)),
    ("Baltic Sea",          (53, 66,  10,  30)),
    ("Black Sea",           (41, 47,  28,  42)),
    ("Red Sea",             (12, 30,  32,  44)),
    ("Persian Gulf",        (22, 30,  47,  57)),
    ("Arabian Sea",         (5,  26,  52,  78)),
    ("Bay of Bengal",       (5,  23,  78, 100)),
    ("South China Sea",     (0,  25, 100, 122)),
    ("East China Sea",      (24, 34, 120, 132)),
    ("Sea of Japan",        (34, 52, 128, 142)),
    ("Bering Sea",          (52, 66, 163, 180)),
    ("Gulf of Alaska",      (52, 62,-152,-130)),
    ("Hudson Bay",          (51, 66, -95, -65)),
    ("Coral Sea",           (-25, -8, 142, 160)),
    ("Tasman Sea",          (-48,-28, 150, 175)),
    ("Norwegian Sea",       (62, 78, -15,  30)),
    ("Barents Sea",         (68, 82,  15,  60)),
    ("Labrador Sea",        (50, 68, -65, -42)),
    # Oceans (broader, checked after seas)
    ("Arctic Ocean",        (70, 90, -180, 180)),
    ("Southern Ocean",      (-90,-55, -180, 180)),
    ("North Atlantic",      (0,  66, -80,   0)),
    ("South Atlantic",      (-55,  0, -70,  20)),
    ("North Pacific",       (0,  66,-180, -80)),
    ("South Pacific",       (-55,  0,-180,-100)),
    ("Indian Ocean",        (-55, 30,  20, 120)),
]


def _get_ocean_name(lat, lon):
    """Return ocean/sea name for coordinates, or None if over land."""
    for name, (lat_min, lat_max, lon_min, lon_max) in _OCEAN_REGIONS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return None


# ---------------------------------------------------------------------------
# US/CA state abbreviation from Nominatim address
# ---------------------------------------------------------------------------

_STATE_ABBR = {
    "California": "CA", "Oregon": "OR", "Washington": "WA",
    "Nevada": "NV", "Arizona": "AZ", "Utah": "UT", "Idaho": "ID",
    "Montana": "MT", "Wyoming": "WY", "Colorado": "CO",
    "New Mexico": "NM", "Texas": "TX", "Florida": "FL",
    "Georgia": "GA", "North Carolina": "NC", "South Carolina": "SC",
    "Virginia": "VA", "West Virginia": "WV", "Tennessee": "TN",
    "Kentucky": "KY", "Ohio": "OH", "Indiana": "IN", "Illinois": "IL",
    "Michigan": "MI", "Wisconsin": "WI", "Minnesota": "MN",
    "Iowa": "IA", "Missouri": "MO", "Arkansas": "AR",
    "Louisiana": "LA", "Mississippi": "MS", "Alabama": "AL",
    "Pennsylvania": "PA", "New York": "NY", "New Jersey": "NJ",
    "Connecticut": "CT", "Massachusetts": "MA", "Vermont": "VT",
    "New Hampshire": "NH", "Maine": "ME", "Rhode Island": "RI",
    "Delaware": "DE", "Maryland": "MD", "Alaska": "AK", "Hawaii": "HI",
    "Kansas": "KS", "Nebraska": "NE", "South Dakota": "SD",
    "North Dakota": "ND", "Oklahoma": "OK",
}


def _get_state_abbr(address):
    """Extract 2-letter state/province code from Nominatim address dict."""
    state = address.get("ISO3166-2-lvl4", "")
    if state and "-" in state:
        return state.split("-")[-1]
    return _STATE_ABBR.get(address.get("state", ""), "")


def _format_city_name(name, state, country_code=""):
    """Format city name with state (US/CA) or country code, respecting MAX_NAME_LEN."""
    if country_code.lower() in ("us", "ca") and state:
        candidate = f"{name}, {state}"
        if len(candidate) <= MAX_NAME_LEN:
            return candidate
        return name if len(name) <= MAX_NAME_LEN else name[:MAX_NAME_LEN].rstrip()
    # All other countries: append 2-letter country code
    suffix = country_code.upper()
    candidate = f"{name}, {suffix}" if suffix else name
    if len(candidate) <= MAX_NAME_LEN:
        return candidate
    return name if len(name) <= MAX_NAME_LEN else name[:MAX_NAME_LEN].rstrip()


# ---------------------------------------------------------------------------
# NPS parks
# ---------------------------------------------------------------------------

# In-memory list: [[name, lat, lon], ...]
_parks_db = []
_parks_loaded = False
_parks_lock = threading.Lock()


def _strip_park_name(full_name):
    """Remove common NPS suffixes for shorter display."""
    for suffix in _STRIP_SUFFIXES:
        if full_name.endswith(suffix):
            return full_name[: -len(suffix)]
    return full_name


def _download_parks():
    """Download all parks from NPS API (paginated, 50 per page)."""
    try:
        from config import NPS_API_KEY
    except (ImportError, ModuleNotFoundError, NameError):
        NPS_API_KEY = os.environ.get("NPS_API_KEY", "")

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
            _log_api("nps")
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
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.error(f"[Landmarks] Cache corrupted, re-downloading: {e}")
            except Exception as e:
                logger.error(f"[Landmarks] Cache load failed: {e}")

        _parks_db = _download_parks()
        _parks_loaded = True


# ---------------------------------------------------------------------------
# Nominatim reverse geocoding (background thread)
# ---------------------------------------------------------------------------

_nom_result = None  # Last resolved name string
_nom_query_lat = None  # Lat of the query that produced _nom_result
_nom_query_lon = None  # Lon of the query that produced _nom_result
_nom_fetching = False  # True while a background fetch is in flight
_nom_lock = threading.Lock()


def _nominatim_fetch(lat, lon):
    """Reverse-geocode via Nominatim. Runs in a background daemon thread."""
    global _nom_result, _nom_query_lat, _nom_query_lon, _nom_fetching
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json",
                    "zoom": 10, "addressdetails": 1},
            headers=_NOM_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        _log_api("nominatim")
        data = r.json()
        address = data.get("address", {})
        state = _get_state_abbr(address)
        country_code = address.get("country_code", "")

        # Try named settlement from address hierarchy (ASCII only)
        cleaned = None
        for key in ("city", "town", "village", "municipality", "suburb"):
            candidate = address.get(key)
            if not candidate:
                continue
            # Reject non-ASCII names (unreadable on LED matrix)
            try:
                candidate.encode("ascii")
            except UnicodeEncodeError:
                continue
            if len(candidate) > MAX_NAME_LEN:
                continue
            cleaned = _format_city_name(candidate, state, country_code)
            break

        # Fall back to country name
        if not cleaned and country_code:
            cleaned = _country_name(country_code)

        # Fall back to ocean/sea
        if not cleaned:
            cleaned = _get_ocean_name(lat, lon)

        with _nom_lock:
            _nom_result = cleaned
            _nom_query_lat = lat
            _nom_query_lon = lon

    except Exception:
        # On any error, record the query coords so we don't retry immediately
        with _nom_lock:
            _nom_query_lat = lat
            _nom_query_lon = lon
    finally:
        with _nom_lock:
            _nom_fetching = False


def _ensure_nominatim(lat, lon):
    """
    Kick off a background Nominatim fetch if the plane has moved far enough.

    Returns the most recent Nominatim city name (may be from previous position),
    or None if no result is available yet.
    """
    global _nom_fetching
    with _nom_lock:
        stale = (_nom_query_lat is None or
                 _haversine_km(lat, lon, _nom_query_lat, _nom_query_lon)
                 > REQUERY_AFTER_KM)
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

def get_nearest_landmark(latitude, longitude):
    """
    Find the nearest landmark (park, city, country, or ocean) to the given
    coordinates.

    Priority chain:
      1. NPS parks within 30km
      2. Nominatim city (background, re-queried every 15km of movement)
      3. Local cities.json nearest-neighbour
      4. Country name from country_code (via Nominatim response)
      5. Ocean/sea name from coordinates

    Returns {"name": str, "distance_km": float, "type": "park"|"city"} or None.
    """
    _load_parks()

    # 1. Check parks first (within radius)
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

    # 2. Nominatim city (non-blocking background fetch)
    nom_city = _ensure_nominatim(latitude, longitude)
    if nom_city:
        return {"name": nom_city, "distance_km": 0.0, "type": "city"}

    # 3. Fall back to local cities.json nearest-neighbour
    city = get_nearest_city(latitude, longitude)
    if city:
        return {"name": city["name"], "distance_km": city["distance_km"], "type": "city"}

    # 4-5. Country and ocean are handled inside _nominatim_fetch as fallbacks.
    #       If Nominatim hasn't returned yet, try ocean lookup directly.
    ocean = _get_ocean_name(latitude, longitude)
    if ocean:
        return {"name": ocean, "distance_km": 0.0, "type": "ocean"}

    return None


def clear_cache():
    """Clear Nominatim cache — forces re-query on next call."""
    global _nom_result, _nom_query_lat, _nom_query_lon, _nom_fetching
    with _nom_lock:
        _nom_result = None
        _nom_query_lat = None
        _nom_query_lon = None
        _nom_fetching = False


def preload():
    """Background preload — call from main thread at startup."""
    t = threading.Thread(target=_load_parks, daemon=True)
    t.start()
