"""
landmarks.py — Nearest landmark or city lookup.

Strategy:
  1. Check NPS parks dataset — if a park is within PARK_RADIUS_KM, show it.
  2. Nominatim reverse geocode — if coordinate is inside a city/town polygon, use it.
  3. Local cities.json nearest-neighbour search — for open land with no city polygon.
  4. Country name from country_code — for remote land (deserts, ice, wilderness).
  5. Ocean/sea name — coordinate-based lookup for water.

cities.json is built once by running build_cities.py.
Nominatim is queried in the background every REQUERY_AFTER_KM of movement.
"""

import json
import math
import os
import threading

import requests

NOMINATIM_URL    = "https://nominatim.openstreetmap.org/reverse"
PARK_RADIUS_KM   = 30
REQUERY_AFTER_KM = 15
CITIES_MAX_KM    = 200
MAX_NAME_LEN     = 24

_HEADERS = {"User-Agent": "plane-tracker-rgb-pi/1.0"}

_STRIP_SUFFIXES = [
    "National Monument and Preserve", "National Monument & Preserve",
    "National Recreation Area", "National Historical Park",
    "National Historic Site", "National Memorial", "National Monument",
    "National Seashore", "National Lakeshore", "National Parkway",
    "National Reserve", "National Forest", "National Refuge",
    "National Park", "State Historic Park", "State Recreation Area",
    "State Forest", "State Park", "Provincial Park", "Regional Park",
    "Country Park", "Nature Reserve", "Wildlife Refuge",
    "Wilderness Area", "Historic Site", "Heritage Site",
]

_nom_result    = None
_nom_query_lat = None
_nom_query_lon = None
_nom_fetching  = False
_nom_lock      = threading.Lock()

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
    if not country_code:
        return None
    name = _COUNTRY_NAMES.get(country_code.lower())
    if not name:
        return None
    if len(name) <= MAX_NAME_LEN:
        return name
    return name[:MAX_NAME_LEN].rstrip()


# ---------------------------------------------------------------------------
# Ocean/sea detection — coordinate based
# These are rough bounding boxes, ordered most-specific first.
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
    for name, (lat_min, lat_max, lon_min, lon_max) in _OCEAN_REGIONS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return None


# ---------------------------------------------------------------------------
# Local cities dataset
# ---------------------------------------------------------------------------

_cities = None


def _load_cities():
    global _cities
    if _cities is not None:
        return _cities
    for path in (
        os.path.join(os.path.dirname(__file__), "cities.json"),
        os.path.join(os.path.dirname(__file__), "..", "cities.json"),
        "utilities/cities.json",
        "cities.json",
    ):
        if os.path.exists(path):
            with open(path) as f:
                _cities = json.load(f)
            return _cities
    _cities = []
    return _cities


def _format_city(name, state, country):
    if country == "US" and state:
        candidate = f"{name}, {state}"
        if len(candidate) <= MAX_NAME_LEN:
            return candidate
        return name if len(name) <= MAX_NAME_LEN else name[:MAX_NAME_LEN].rstrip()
    candidate = f"{name}, {country}" if country else name
    if len(candidate) <= MAX_NAME_LEN:
        return candidate
    return name if len(name) <= MAX_NAME_LEN else name[:MAX_NAME_LEN].rstrip()


def _nearest_city_local(lat, lon):
    cities = _load_cities()
    if not cities:
        return None, None
    best_name = None
    best_dist = float("inf")
    for entry in cities:
        if len(entry) == 5:
            name, c_lat, c_lon, state, country = entry
        else:
            name, c_lat, c_lon, state = entry
            country = "US"
        dist = _haversine_km(lat, lon, c_lat, c_lon)
        if dist >= best_dist or dist > CITIES_MAX_KM:
            continue
        c = _clean_name(name)
        if c:
            best_dist = dist
            best_name = _format_city(c, state, country)
    if best_name:
        return best_name, best_dist
    return None, None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _clean_name(name):
    if not name:
        return name
    stripped = name.strip()
    for suffix in _STRIP_SUFFIXES:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].rstrip(" ,\u2013-")
            break
    if len(stripped) > MAX_NAME_LEN:
        return None
    return stripped or None


def _get_state_abbr(address):
    state = address.get("ISO3166-2-lvl4", "")
    if state and "-" in state:
        return state.split("-")[-1]
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
    return _STATE_ABBR.get(address.get("state", ""), "")


def _format_with_state(name, state):
    with_state = f"{name}, {state}" if state else name
    if len(with_state) <= MAX_NAME_LEN:
        return with_state
    return name if len(name) <= MAX_NAME_LEN else name[:MAX_NAME_LEN].rstrip()


# ---------------------------------------------------------------------------
# NPS park lookup
# ---------------------------------------------------------------------------

def _nearest_park(lat, lon):
    try:
        from utilities.nationalparks import get_nearby_parks
    except ModuleNotFoundError:
        from nationalparks import get_nearby_parks
    candidates = get_nearby_parks(lat, lon, PARK_RADIUS_KM)
    for result in candidates:
        name = _clean_name(result["name"])
        if name:
            return name, result["distance_km"]
    return None, None


# ---------------------------------------------------------------------------
# Nominatim city lookup
# ---------------------------------------------------------------------------

def _nominatim_fetch(lat, lon):
    """Fetch nearest city/country/ocean. Runs in background thread."""
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
        data         = r.json()
        address      = data.get("address", {})
        state        = _get_state_abbr(address)
        country_code = address.get("country_code", "")

        # Step 1: named settlement in address hierarchy (ASCII only)
        cleaned = None
        for key in ("city", "town", "village", "municipality", "suburb"):
            candidate = address.get(key)
            if not candidate:
                continue
            try:
                candidate.encode("ascii")
            except UnicodeEncodeError:
                continue
            c = _clean_name(candidate)
            if c is None:
                continue
            cleaned = _format_with_state(c, state)
            break

        # Step 2: local cities.json nearest-neighbour
        if not cleaned:
            city, dist = _nearest_city_local(lat, lon)
            if city:
                cleaned = city

        # Step 3: country name from country_code
        if not cleaned and country_code:
            country = _country_name(country_code)
            if country:
                cleaned = country

        # Step 4: ocean/sea name from coordinates
        if not cleaned:
            ocean = _get_ocean_name(lat, lon)
            if ocean:
                cleaned = ocean

        if not cleaned:
            with _nom_lock:
                _nom_query_lat = lat
                _nom_query_lon = lon
            return

        with _nom_lock:
            _nom_result    = cleaned
            _nom_query_lat = lat
            _nom_query_lon = lon

    except Exception as e:
        with _nom_lock:
            _nom_query_lat = lat
            _nom_query_lon = lon

    with _nom_lock:
        _nom_fetching = False


def _ensure_nominatim(lat, lon):
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
    park_name, _ = _nearest_park(latitude, longitude)
    if park_name:
        return {"name": park_name}
    city = _ensure_nominatim(latitude, longitude)
    if city:
        return {"name": city}
    return None


def clear_cache():
    global _nom_result, _nom_query_lat, _nom_query_lon, _nom_fetching
    with _nom_lock:
        _nom_result    = None
        _nom_query_lat = None
        _nom_query_lon = None
        _nom_fetching  = False
