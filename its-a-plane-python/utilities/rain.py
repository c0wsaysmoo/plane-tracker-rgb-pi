"""
rain.py — Minutely precipitation alerts via OpenWeatherMap One Call 3.0.

Polls every 5 minutes, caches to disk. Returns rain/snow alerts in the style
of Apple Weather: "Rain in 8m", "Snow in 5m", "Stop in 20m".

Usage:
    from utilities.rain import get_rain_alert
    alert = get_rain_alert()
    # {"type": "rain", "action": "starting", "minutes": 8}
    # {"type": "snow", "action": "stopping", "minutes": 20}
    # {"type": "rain", "action": "now"}   (raining, no stop within 60m)
    # None                                (no precip expected)
"""

import json
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.openweathermap.org/data/3.0/onecall"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "rain.json")
_POLL_INTERVAL = 300  # 5 minutes
_PRECIP_THRESHOLD = 0.1  # mm/h — below this is "dry"

# In-memory cache
_cached_data = None  # raw API response dict
_cached_ts = 0.0     # epoch of last successful fetch


def _fetch(lat, lon, api_key):
    """Fetch current + minutely data from OWM One Call 3.0."""
    try:
        r = requests.get(_BASE_URL, params={
            "lat": lat,
            "lon": lon,
            "exclude": "hourly,daily,alerts",
            "appid": api_key,
        }, timeout=(5, 15))
        r.raise_for_status()
        data = r.json()

        # Cache to disk
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)

        logger.info(f"[Rain] Fetched {len(data.get('minutely', []))} minutely points")
        return data

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.warning("[Rain] Rate limited (429) — using cache")
        else:
            logger.error(f"[Rain] HTTP error: {e}")
        return None
    except Exception as e:
        logger.error(f"[Rain] Fetch failed: {e}")
        return None


def _load_cache():
    """Load from disk cache if recent enough. Returns (data, timestamp) or (None, 0)."""
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

    # Read config at call time so web UI reload takes effect
    import config as cfg
    api_key = cfg.OWM_API_KEY
    location = cfg.LOCATION_HOME

    if not api_key or location == [0.0, 0.0]:
        return None

    now = time.time()
    if _cached_data and (now - _cached_ts) < _POLL_INTERVAL:
        return _cached_data

    # Try disk cache first (survives reboot)
    if _cached_data is None:
        disk, disk_ts = _load_cache()
        if disk:
            _cached_data = disk
            _cached_ts = disk_ts
            logger.info("[Rain] Loaded from disk cache")

    # Fetch from API if interval elapsed
    if (now - _cached_ts) >= _POLL_INTERVAL:
        data = _fetch(location[0], location[1], api_key)
        if data:
            _cached_data = data
            _cached_ts = now

    return _cached_data


def _precip_type(data):
    """Determine precipitation type from current weather condition code.
    OWM codes: 2xx=thunderstorm, 3xx=drizzle, 5xx=rain, 6xx=snow, 7xx=atmosphere.
    Within 6xx: 600-602=snow, 611-616=sleet/freezing rain, 620-622=shower snow.
    """
    try:
        weather_id = data["current"]["weather"][0]["id"]
        if 611 <= weather_id <= 616:
            return "sleet"
        if 600 <= weather_id < 700:
            return "snow"
    except (KeyError, IndexError, TypeError):
        pass
    return "rain"


def get_rain_alert():
    """
    Check minutely precipitation forecast and return an alert dict or None.

    Returns:
        {"type": "rain"|"snow", "action": "starting"|"stopping"|"now", "minutes": int}
        or None if no precipitation expected.
    """
    data = _refresh()
    if not data:
        return None

    minutely = data.get("minutely")
    if not minutely or len(minutely) < 2:
        return None

    precip_type = _precip_type(data)
    current_precip = minutely[0].get("precipitation", 0)
    is_raining = current_precip >= _PRECIP_THRESHOLD

    if not is_raining:
        # Dry now — scan for when precip starts
        for i, m in enumerate(minutely[1:], start=1):
            if m.get("precipitation", 0) >= _PRECIP_THRESHOLD:
                return {"type": precip_type, "action": "starting", "minutes": i}
        return None  # Dry for next 60 minutes
    else:
        # Raining now — scan for when it stops
        for i, m in enumerate(minutely[1:], start=1):
            if m.get("precipitation", 0) < _PRECIP_THRESHOLD:
                return {"type": precip_type, "action": "stopping", "minutes": i}
        # No stop in sight
        return {"type": precip_type, "action": "now"}
