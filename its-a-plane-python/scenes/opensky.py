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
        """
        Fetch state vectors for the configured zone.
        Returns a list of dicts with standardised field names,
        filtered to airborne flights within altitude limits.
        """
        lat_min = ZONE_DEFAULT["br_y"]
        lat_max = ZONE_DEFAULT["tl_y"]
        lon_min = ZONE_DEFAULT["tl_x"]
        lon_max = ZONE_DEFAULT["br_x"]

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
            return []

        raw_states = data.get("states") or []
        results    = []

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

    # ------------------------------------------------------------------
    # Global callsign search (for tracked flight)
    # ------------------------------------------------------------------

    def find_callsign(self, callsign):
        """
        Search globally for a specific callsign using adsb.lol (free, no auth).
        Returns a state dict if found airborne, or None.
        """
        state = self._fetch_callsign(callsign)
        if state is None:
            return None
        if state.get("on_ground"):
            return None
        return state

    def find_callsign_any(self, callsign):
        """
        Like find_callsign but also returns ground/taxiing aircraft.
        Used by the pre-departure tracking logic to confirm a flight exists
        before it becomes airborne.
        Returns a state dict (with on_ground=True possible), or None if not found.
        """
        return self._fetch_callsign(callsign)

    def _fetch_callsign(self, callsign):
        """
        Internal: hit adsb.lol and return the raw state dict regardless of
        ground status, or None if the callsign isn't found / doesn't match.
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

            # Must match callsign exactly
            returned = (ac.get("flight") or "").strip().upper()
            if returned != callsign_clean.upper():
                return None

            on_ground = ac.get("alt_baro") == "ground" or ac.get("gs", 0) < 50

            lat = ac.get("lat")
            lon = ac.get("lon")
            if lat is None or lon is None:
                return None

            alt_baro = ac.get("alt_baro", 0)
            alt_ft   = alt_baro if isinstance(alt_baro, (int, float)) else 0
            gs       = ac.get("gs", 0) or 0
            vs       = ac.get("baro_rate", 0) or 0

            return {
                "icao24":         ac.get("hex", ""),
                "callsign":       callsign_clean,
                "latitude":       lat,
                "longitude":      lon,
                "altitude":       int(alt_ft),
                "ground_speed":   int(gs),  # adsb.lol reports in knots
                "heading":        ac.get("track", 0) or 0,
                "vertical_speed": int(vs),
                "on_ground":      on_ground,
            }
        except Exception as e:
            print(f"[adsb.lol] Callsign search failed for {callsign}: {e}")
            return None

    # ------------------------------------------------------------------
    # Flight trail (for farthest flights)
    # ------------------------------------------------------------------

    def get_flight_trail(self, icao24):
        """
        Fetch the actual flown trail for a live aircraft by icao24 hex code.
        Returns a list of {"lat": lat, "lon": lon} dicts or empty list.
        """
        if not icao24:
            return []
        try:
            resp = requests.get(
                f"{BASE_URL}/api/tracks/all",
                headers=self._auth_headers(),
                params={"icao24": icao24.lower(), "time": 0},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            data   = resp.json()
            points = data.get("path", [])
            # Each point: [time, lat, lon, baro_alt, true_track, on_ground]
            # Filter out ground points — keep airborne ones (on_ground=false)
            trail = []
            for p in points:
                if len(p) >= 6 and p[1] is not None and p[2] is not None:
                    on_ground = p[5]
                    if not on_ground:
                        trail.append([p[1], p[2]])
            return trail
        except Exception as e:
            print(f"[OpenSky] Trail fetch failed for {icao24}: {e}")
            return []
