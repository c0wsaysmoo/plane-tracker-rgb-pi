"""
adsbdb.py — Aircraft registration lookup via api.adsbdb.com.
Free, no API key required.

Accepts ICAO hex (from OpenSky icao24) or N-number — both work identically.
Returns aircraft type, manufacturer, and registered operator/brand.

Cache TTL is 30 days — aircraft registrations almost never change.
"""

import requests
from time import time

BASE_URL  = "https://api.adsbdb.com/v0/aircraft"
CACHE_TTL = 60 * 60 * 24 * 30   # 30 days
TIMEOUT   = 8

# { identifier → {"data": dict|None, "ts": float} }
_cache: dict = {}


def get_aircraft_info(identifier: str) -> dict:
    """
    Look up an aircraft by ICAO hex (e.g. 'a9fb92') or registration (e.g. 'N742SK').
    Both formats are accepted by adsbdb identically.

    Returns a dict with:
        icao_type           — e.g. "CRJ7"
        type_full           — e.g. "CRJ 700 701"
        manufacturer        — e.g. "Bombardier"
        operator            — e.g. "American Eagle"  (marketing brand, not legal owner)
        operator_flag_code  — e.g. "SKW"             (actual ICAO operator)
        registration        — e.g. "N742SK"
        mode_s              — e.g. "A9FB92"
        url_photo           — full photo URL or ""
        url_photo_thumbnail — thumbnail URL or ""

    Returns {} if not found or on error.
    """
    if not identifier:
        return {}

    key = identifier.strip().upper()
    cached = _cache.get(key)
    if cached is not None and (time() - cached["ts"]) < CACHE_TTL:
        return cached["data"] or {}

    try:
        r = requests.get(f"{BASE_URL}/{key}", timeout=TIMEOUT)
        if r.status_code == 404:
            _cache[key] = {"data": None, "ts": time()}
            return {}
        r.raise_for_status()
        raw = r.json().get("response", {}).get("aircraft") or None
        if not raw:
            _cache[key] = {"data": None, "ts": time()}
            return {}

        data = {
            "icao_type":            raw.get("icao_type", ""),
            "type_full":            raw.get("type", ""),
            "manufacturer":         raw.get("manufacturer", ""),
            "operator":             raw.get("registered_owner", ""),
            "operator_flag_code":   raw.get("registered_owner_operator_flag_code", ""),
            "registration":         raw.get("registration", ""),
            "mode_s":               raw.get("mode_s", ""),
            "url_photo":            raw.get("url_photo") or "",
            "url_photo_thumbnail":  raw.get("url_photo_thumbnail") or "",
        }
        _cache[key] = {"data": data, "ts": time()}
        #print(f"[adsbdb] {key} → {data['registration']} {data['icao_type']} — {data['operator']}")
        return data

    except Exception as e:
        print(f"[adsbdb] lookup failed for {key}: {e}")
        return {}
