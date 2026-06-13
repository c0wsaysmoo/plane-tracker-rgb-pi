"""
opensky.py — Zone position data from OpenSky Network.
Responsibilities:
- OAuth token management (auto-refresh)
- Bounding box zone search returning StateVector list
- Global callsign search for tracked flight (free)
- Flight trail fetch by icao24 hex (for farthest flights)
- Unit conversions (m/s → knots, metres → feet)
- Filters out ground traffic and below MIN_ALTITUDE
"""

import math
from time import time

import requests
from requests.exceptions import ConnectionError
from urllib3.exceptions import NewConnectionError, MaxRetryError

try:
    from config import OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET
except (ImportError, ModuleNotFoundError, NameError):
    OPENSKY_CLIENT_ID = ""
    OPENSKY_CLIENT_SECRET = ""

try:
    from config import MIN_ALTITUDE
except (ImportError, ModuleNotFoundError, NameError):
    MIN_ALTITUDE = 0

try:
    from config import ZONE_HOME, LOCATION_HOME
    ZONE_DEFAULT     = ZONE_HOME
    LOCATION_DEFAULT = LOCATION_HOME
except (ImportError, ModuleNotFoundError, NameError):
    ZONE_DEFAULT     = {"tl_y": 41.904318, "tl_x": -87.647367,
                        "br_y": 41.851654, "br_x": -87.573027}
    LOCATION_DEFAULT = [41.882724, -87.623350]

MAX_ALTITUDE = 100000

TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)
BASE_URL = "https://opensky-network.org"


def _parse_state(s):
    """Parse a raw OpenSky state vector into a standardised dict."""
    if len(s) < 17:
        return None
    icao24       = s[0] or ""
    callsign     = (s[1] or "").strip()
    on_ground    = s[8] or False
    latitude     = s[6]
    longitude    = s[5]
    baro_alt_m   = s[7]
    velocity_ms  = s[9]
    true_track   = s[10]
    vert_rate_ms = s[11]

    if on_ground or latitude is None or longitude is None:
        return None

    alt_ft = (baro_alt_m or 0) * 3.28084

    return {
        "icao24":         icao24,
        "callsign":       callsign,
        "latitude":       latitude,
        "longitude":      longitude,
        "altitude":       int(alt_ft),
        "ground_speed":   int((velocity_ms or 0) * 1.94384),
        "heading":        true_track or 0,
        "vertical_speed": int((vert_rate_ms or 0) * 196.85),
        "on_ground":      on_ground,
    }


class OpenSkyClient:
    """Thin OpenSky REST client with automatic OAuth token refresh."""

    def __init__(self):
        self._token        = None
        self._token_expiry = 0

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _refresh_token(self):
        if not OPENSKY_CLIENT_ID or not OPENSKY_CLIENT_SECRET:
            return
        try:
            resp = requests.post(
                TOKEN_URL,
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     OPENSKY_CLIENT_ID,
                    "client_secret": OPENSKY_CLIENT_SECRET,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token        = data["access_token"]
            self._token_expiry = time() + data.get("expires_in", 1800) - 60
        except Exception as e:
            print(f"OpenSky token refresh failed: {e}")
            self._token = None

    def _ensure_token(self):
        if OPENSKY_CLIENT_ID and (not self._token or time() >= self._token_expiry):
            self._refresh_token()

    def _auth_headers(self):
        self._ensure_token()
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    # ------------------------------------------------------------------
    # Zone search
    # ------------------------------------------------------------------

    def get_zone_states(self):
        lat_min = ZONE_DEFAULT["br_y"]
        lat_max = ZONE_DEFAULT["tl_y"]
        lon_min = ZONE_DEFAULT["tl_x"]
        lon_max = ZONE_DEFAULT["br_x"]

        # 1. Try adsb.lol first — more reliable uptime
        results = self._fetch_adsblo_zone(lat_min, lat_max, lon_min, lon_max)

        # 2. FALLBACK: Only fall back to OpenSky on failure (None), not empty zone ([])
        if results is None:
            print("[adsb.lol] Request failed — falling back to OpenSky...")
            results = self._fetch_opensky_zone(lat_min, lat_max, lon_min, lon_max)

        return results or []

    def _fetch_opensky_zone(self, lat_min, lat_max, lon_min, lon_max):
        """
        Fetch zone states from OpenSky.
        Returns None if the request fails or data is stale (>2 minutes old).
        Returns [] if request succeeded but zone is genuinely empty.
        """
        import time as _time
        params = {
            "lamin": lat_min,
            "lamax": lat_max,
            "lomin": lon_min,
            "lomax": lon_max,
        }
        try:
            resp = requests.get(
                f"{BASE_URL}/api/states/all",
                headers=self._auth_headers(),
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"OpenSky zone fetch failed: {e}")
            return None

        raw_states = data.get("states") or []

        # Staleness check — if any state is >2 minutes old, treat as failed
        now = _time.time()
        for s in raw_states:
            if len(s) > 4 and s[4] is not None:
                age = now - s[4]  # s[4] is last_contact
                if age > 120:
                    print(f"[OpenSky] Stale data detected ({int(age)}s old) — falling back")
                    return None
                break  # only need to check first state

        results = []
        for s in raw_states:
            state = _parse_state(s)
            if not state:
                continue
            if not state["callsign"]:
                continue
            alt_ft = state["altitude"]
            if alt_ft < MIN_ALTITUDE or alt_ft > MAX_ALTITUDE:
                continue
            results.append(state)

        return results

    def _fetch_adsblo_zone(self, lat_min, lat_max, lon_min, lon_max):
        """
        Fetch zone states from adsb.lol using a circle that fits inside the bounding box.
        Results are filtered back to the exact bounding box after fetching.
        """
        import math as _math

        # Centre of zone
        lat_c = (lat_min + lat_max) / 2
        lon_c = (lon_min + lon_max) / 2

        # Half-width and half-height in nautical miles
        def _nm(la1, lo1, la2, lo2):
            la1, lo1, la2, lo2 = map(_math.radians, (la1, lo1, la2, lo2))
            a = (_math.sin((la2-la1)/2)**2
                 + _math.cos(la1)*_math.cos(la2)*_math.sin((lo2-lo1)/2)**2)
            return 3440.07 * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1-a))

        half_w = _nm(lat_c, lon_min, lat_c, lon_max) / 2
        half_h = _nm(lat_min, lon_c, lat_max, lon_c) / 2
        # Use half-diagonal so the circle covers the full bounding box including corners
        radius_nm = max(1, int(_math.ceil(_math.sqrt(half_w**2 + half_h**2))))

        try:
            resp = requests.get(
                f"https://api.adsb.lol/v2/lat/{lat_c}/lon/{lon_c}/dist/{radius_nm}",
                timeout=10,
            )
            if resp.status_code != 200:
                print(f"[adsb.lol] Zone fetch failed: HTTP {resp.status_code}")
                return None
            ac_list = resp.json().get("ac", [])
        except Exception as e:
            print(f"[adsb.lol] Zone fetch failed: {e}")
            return None

        results = []
        for ac in ac_list:
            lat = ac.get("lat")
            lon = ac.get("lon")
            if lat is None or lon is None:
                continue
            # Filter to exact bounding box
            if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                continue
            callsign = (ac.get("flight") or "").strip()
            if not callsign:
                continue
            on_ground = ac.get("alt_baro") == "ground" or ac.get("gs", 0) < 50
            if on_ground:
                continue
            alt_baro = ac.get("alt_baro", 0)
            alt_ft   = alt_baro if isinstance(alt_baro, (int, float)) else 0
            if alt_ft < MIN_ALTITUDE or alt_ft > MAX_ALTITUDE:
                continue
            gs = ac.get("gs", 0) or 0
            vs = ac.get("baro_rate", 0) or 0
            results.append({
                "icao24":         ac.get("hex", ""),
                "callsign":       callsign,
                "latitude":       lat,
                "longitude":      lon,
                "altitude":       int(alt_ft),
                "ground_speed":   int(gs),
                "heading":        ac.get("track", 0) or 0,
                "vertical_speed": int(vs),
                "on_ground":      False,
            })

        return results

    # ------------------------------------------------------------------
    # Global callsign search (for tracked flight)
    # ------------------------------------------------------------------

    def find_callsign(self, callsign):
        """
        Search globally for a specific callsign using adsb.lol (free, no auth).
        Returns a state dict if found airborne, or None.
        """
        callsign_clean = callsign.strip()
        try:
            resp = requests.get(
                f"https://api.adsb.lol/v2/callsign/{callsign_clean}",
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            ac_list = data.get("ac", [])
            if not ac_list:
                return None

            ac = ac_list[0]

            # Must match callsign exactly and be airborne
            returned = (ac.get("flight") or "").strip().upper()
            if returned != callsign_clean.upper():
                return None

            on_ground = ac.get("alt_baro") == "ground" or ac.get("gs", 0) < 50
            if on_ground:
                return None

            lat = ac.get("lat")
            lon = ac.get("lon")
            if lat is None or lon is None:
                return None

            alt_baro = ac.get("alt_baro", 0)
            alt_ft   = alt_baro if isinstance(alt_baro, (int, float)) else 0
            gs       = ac.get("gs", 0) or 0
            vs       = ac.get("baro_rate", 0) or 0

            return {
                "icao24":        ac.get("hex", ""),
                "callsign":      callsign_clean,
                "latitude":      lat,
                "longitude":     lon,
                "altitude":      int(alt_ft),
                "ground_speed":  int(gs),  # already in knots from adsb.lol
                "heading":       ac.get("track", 0) or 0,
                "vertical_speed": int(vs),
                "on_ground":     False,
            }
        except Exception as e:
            print(f"[adsb.lol] Callsign search failed for {callsign}: {e}")
            return None

    # ------------------------------------------------------------------
    # Flight trail (for farthest flights)
    # ------------------------------------------------------------------

    def get_flight_trail(self, icao24):
        if not icao24:
            return []
        try:
            self._ensure_token()
            if not self._token:
                return []

            resp = requests.get(
                f"{BASE_URL}/api/tracks/all",
                headers={"Authorization": f"Bearer {self._token}"},
                params={"icao24": icao24.lower(), "time": 0},
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"[OpenSky] Trail fetch HTTP {resp.status_code} for {icao24}")
                return []
            data   = resp.json()
            points = data.get("path", [])
            trail = []
            for p in points:
                if len(p) >= 6 and p[1] is not None and p[2] is not None:
                    if not p[5]:  # not on_ground
                        trail.append([p[1], p[2]])
            return trail
        except Exception as e:
            print(f"[OpenSky] Trail fetch failed for {icao24}: {e}")
            return []