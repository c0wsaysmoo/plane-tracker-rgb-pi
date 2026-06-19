"""
adsbdb.py — Aircraft registration lookup via api.adsbdb.com.
Free, no API key required.

Accepts ICAO hex (from OpenSky icao24) or N-number — both work identically.
Returns aircraft type, manufacturer, and registered operator/brand.

Cache TTL is 30 days — aircraft registrations almost never change.
Thread-safe with LRU eviction at 500 entries.
"""

import logging
import requests
import threading
from time import time

try:
    from utilities.api_usage import log_call as _log_api
except ImportError:
    _log_api = lambda source: None

logger = logging.getLogger(__name__)

BASE_URL  = "https://api.adsbdb.com/v0/aircraft"
CACHE_TTL = 60 * 60 * 24 * 30   # 30 days
TIMEOUT   = 8
MAX_CACHE_SIZE = 500

# { identifier → {"data": dict|None, "ts": float, "last_access": float} }
_cache: dict = {}
_cache_lock = threading.Lock()


def _evict_oldest():
    """Remove the 100 least-recently-accessed entries when cache exceeds MAX_CACHE_SIZE.
    Caller MUST hold _cache_lock."""
    if len(_cache) <= MAX_CACHE_SIZE:
        return
    sorted_keys = sorted(_cache.keys(), key=lambda k: _cache[k]["last_access"])
    for k in sorted_keys[:100]:
        del _cache[k]
    logger.debug("Cache eviction: removed 100 oldest entries, %d remaining", len(_cache))


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
    now = time()

    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and (now - cached["ts"]) < CACHE_TTL:
            cached["last_access"] = now  # update for LRU
            return cached["data"] or {}

    try:
        r = requests.get(f"{BASE_URL}/{key}", timeout=TIMEOUT)
        if r.status_code == 404:
            with _cache_lock:
                _cache[key] = {"data": None, "ts": now, "last_access": now}
                _evict_oldest()
            return {}
        r.raise_for_status()
        _log_api("adsbdb")
        raw = r.json().get("response", {}).get("aircraft") or None
        if not raw:
            with _cache_lock:
                _cache[key] = {"data": None, "ts": now, "last_access": now}
                _evict_oldest()
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
        with _cache_lock:
            _cache[key] = {"data": data, "ts": now, "last_access": now}
            _evict_oldest()
        logger.debug(
            "%s -> %s %s -- %s", key, data["registration"], data["icao_type"], data["operator"]
        )
        return data

    except Exception as e:
        logger.warning("lookup failed for %s: %s", key, e)
        return {}
