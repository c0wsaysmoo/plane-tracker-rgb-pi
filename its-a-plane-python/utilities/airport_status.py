"""
airport_status.py — FAA NAS airport delay/ground stop alerts.

Free, no API key. Polls every 5 minutes. Only major airports appear
in the FAA feed (JFK, LGA, EWR, etc. — not GA fields like TEB/MMU).

Usage:
    from utilities.airport_status import get_airport_alerts
    alerts = get_airport_alerts()
    # [{"text": "JFK Delay", "color": "orange"}, {"text": "EWR GStop", "color": "red"}]
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://nasstatus.faa.gov/api/airport-status-information"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "airport_status.json")
_POLL_INTERVAL = 300  # 5 minutes

# In-memory cache
_cached_data = None
_cached_ts = 0.0


def _fetch():
    """Fetch current airport status from FAA NAS."""
    try:
        r = requests.get(_API_URL, timeout=(5, 15))
        r.raise_for_status()
        data = r.json()

        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)

        logger.info(f"[AirportStatus] Fetched FAA status")
        return data

    except Exception as e:
        logger.error(f"[AirportStatus] Fetch failed: {e}")
        return None


def _load_cache():
    """Load from disk cache if recent enough. Returns (data, ts) or (None, 0)."""
    try:
        with open(_CACHE_FILE, "r") as f:
            obj = json.load(f)
        ts = obj.get("ts", 0)
        if time.time() - ts < _POLL_INTERVAL * 2:
            return obj.get("data"), ts
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None, 0


def _refresh():
    """Refresh data if poll interval has elapsed."""
    global _cached_data, _cached_ts

    now = time.time()
    if _cached_data is not None and (now - _cached_ts) < _POLL_INTERVAL:
        return _cached_data

    if _cached_data is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_data = disk
            _cached_ts = disk_ts
            logger.info("[AirportStatus] Loaded from disk cache")

    if (now - _cached_ts) >= _POLL_INTERVAL:
        data = _fetch()
        if data is not None:
            _cached_data = data
            _cached_ts = now

    return _cached_data


def _ensure_list(val):
    """Normalize single-object API responses to a list."""
    if val is None:
        return []
    if isinstance(val, dict):
        return [val]
    return val


def _parse_alerts(data, watch_airports):
    """Parse FAA response into alert dicts for watched airports."""
    if not data or not watch_airports:
        return []

    watch_set = {a.strip().upper() for a in watch_airports}
    alerts = []

    # Ground stops
    for gs in _ensure_list(data.get("Ground_Stop_List", {}).get("Ground_Stop")):
        arpt = gs.get("ARPT", "").strip().upper()
        if arpt in watch_set:
            alerts.append({"text": f"{arpt} GStop", "color": "red"})

    # Ground delays
    for gd in _ensure_list(data.get("Ground_Delay_List", {}).get("Ground_Delay")):
        arpt = gd.get("ARPT", "").strip().upper()
        if arpt in watch_set:
            alerts.append({"text": f"{arpt} Delay", "color": "orange"})

    # Arrival/departure delays
    for ad in _ensure_list(data.get("Arrival_Departure_Delay_List", {}).get("Arrival_Departure_Delay")):
        arpt = ad.get("ARPT", "").strip().upper()
        if arpt in watch_set and arpt not in {a["text"][:3] for a in alerts}:
            alerts.append({"text": f"{arpt} Delay", "color": "orange"})

    # Airport closures
    for cl in _ensure_list(data.get("Airport_Closure_List", {}).get("Airport_Closure")):
        arpt = cl.get("ARPT", "").strip().upper()
        if arpt in watch_set:
            alerts.append({"text": f"{arpt} Closd", "color": "red"})

    return alerts


def get_airport_alerts():
    """Return list of alert dicts for configured airports.

    Each dict: {"text": "JFK Delay", "color": "orange"}
    Returns [] if no delays or no airports configured.
    """
    import config as cfg
    airports_str = getattr(cfg, "AIRPORT_STATUS_LIST", "")
    if not airports_str:
        return []

    watch_airports = [a.strip() for a in airports_str.split(",") if a.strip()]
    if not watch_airports:
        return []

    data = _refresh()
    if not data:
        return []

    return _parse_alerts(data, watch_airports)
