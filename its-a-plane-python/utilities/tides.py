"""
tides.py — Tide predictions via NOAA CO-OPS API.

Free, no API key required. Returns next high and low tide times
for a configured station. Queries once per day, caches to disk.

Usage:
    from utilities.tides import get_next_tides
    tides = get_next_tides()  # {"high": "4:52p", "low": "11:07p"} or None
"""

import json
import logging
import os
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "tides.json")

# Read station from config (empty = no tides)
try:
    from config import TIDE_STATION
except (ImportError, ModuleNotFoundError, NameError):
    TIDE_STATION = ""

try:
    from config import WATER_TEMP_STATION
except (ImportError, ModuleNotFoundError, NameError):
    WATER_TEMP_STATION = ""

try:
    from config import CLOCK_FORMAT
except (ImportError, ModuleNotFoundError, NameError):
    CLOCK_FORMAT = "24hr"

try:
    from config import TEMPERATURE_UNITS
except (ImportError, ModuleNotFoundError, NameError):
    TEMPERATURE_UNITS = "imperial"

# In-memory cache
_cached_tides = None  # list of {"t": "...", "type": "H"/"L"}
_cached_date = None   # date string "2026-05-23"


def _format_time(time_str):
    """Format '2026-05-23 16:52' to '4:52p' (12hr) or '16:52' (24hr)."""
    try:
        parts = time_str.split(" ")
        if len(parts) < 2:
            return time_str
        hm = parts[1].split(":")
        hour = int(hm[0])
        minute = int(hm[1]) if len(hm) > 1 else 0

        if CLOCK_FORMAT == "12hr":
            ampm = "a" if hour < 12 else "p"
            display_hour = hour % 12 or 12
            return f"{display_hour}:{minute:02d}{ampm}"
        else:
            return f"{hour}:{minute:02d}"
    except (ValueError, IndexError):
        return time_str


def _fetch_predictions(station):
    """Fetch today's high/low tide predictions from NOAA."""
    try:
        r = requests.get(_BASE_URL, params={
            "station": station,
            "product": "predictions",
            "datum": "MLLW",
            "interval": "hilo",
            "time_zone": "lst_ldt",
            "units": "english",
            "format": "json",
            "date": "today",
        }, timeout=(5, 15))
        r.raise_for_status()
        data = r.json()
        predictions = data.get("predictions", [])
        if not predictions:
            logger.warning("[Tides] No predictions returned")
            return None

        # Cache to disk
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"date": str(datetime.now().date()), "predictions": predictions}, f)

        logger.info(f"[Tides] Fetched {len(predictions)} predictions for station {station}")
        return predictions

    except Exception as e:
        logger.error(f"[Tides] Fetch failed: {e}")
        return None


def _load_cache():
    """Load predictions from disk cache if today's data exists."""
    try:
        with open(_CACHE_FILE, "r") as f:
            obj = json.load(f)
        if obj.get("date") == str(datetime.now().date()):
            return obj.get("predictions", [])
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None


def get_next_tides():
    """
    Return next high and low tide times.

    Returns {"high": "4:52p", "low": "11:07p"} or None if no station configured.
    """
    global _cached_tides, _cached_date

    if not TIDE_STATION:
        return None

    today = str(datetime.now().date())

    # Refresh once per day
    if _cached_date != today or _cached_tides is None:
        _cached_tides = _load_cache()
        if _cached_tides:
            _cached_date = today
        else:
            _cached_tides = _fetch_predictions(TIDE_STATION)
            if _cached_tides:
                _cached_date = today
            else:
                return None

    # Find next H and L from current time
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    next_high = None
    next_low = None

    for pred in _cached_tides:
        t = pred.get("t", "")
        if t <= now_str:
            continue
        if pred.get("type") == "H" and next_high is None:
            next_high = _format_time(t)
        elif pred.get("type") == "L" and next_low is None:
            next_low = _format_time(t)
        if next_high and next_low:
            break

    # If we passed all today's tides, show the last ones
    if not next_high:
        for pred in reversed(_cached_tides):
            if pred.get("type") == "H":
                next_high = _format_time(pred["t"])
                break
    if not next_low:
        for pred in reversed(_cached_tides):
            if pred.get("type") == "L":
                next_low = _format_time(pred["t"])
                break

    if not next_high and not next_low:
        return None

    return {"high": next_high or "--", "low": next_low or "--"}


# --- Water Temperature ---

_water_temp = None       # cached value (string like "62")
_water_temp_ts = 0.0     # last fetch timestamp
_WATER_TEMP_POLL = 1800  # refresh every 30 minutes


def get_water_temp():
    """
    Return current ocean water temperature as a string (e.g. "62") or None.

    Uses NOAA CO-OPS water_temperature product. Station is configured via
    WATER_TEMP_STATION in .env (e.g. "8510560" for Montauk).
    """
    global _water_temp, _water_temp_ts
    from time import time

    if not WATER_TEMP_STATION:
        return None

    now = time()
    if _water_temp is not None and (now - _water_temp_ts) < _WATER_TEMP_POLL:
        return _water_temp

    try:
        r = requests.get(_BASE_URL, params={
            "station": WATER_TEMP_STATION,
            "product": "water_temperature",
            "date": "latest",
            "time_zone": "lst_ldt",
            "units": "metric" if TEMPERATURE_UNITS == "metric" else "english",
            "format": "json",
        }, timeout=(5, 15))
        r.raise_for_status()
        data = r.json()
        readings = data.get("data", [])
        if readings:
            val = float(readings[0]["v"])
            _water_temp = str(round(val))
            _water_temp_ts = now
            logger.info(f"[WaterTemp] {_water_temp}° from station {WATER_TEMP_STATION}")
        else:
            logger.warning("[WaterTemp] No data returned")
    except Exception as e:
        logger.error(f"[WaterTemp] Fetch failed: {e}")

    return _water_temp
