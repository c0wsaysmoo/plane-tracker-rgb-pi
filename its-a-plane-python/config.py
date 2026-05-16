"""
config.py — Compatibility shim.
Reads from config/config.json and config/secrets.json.
All existing scenes and utilities import from this module unchanged.
Call config.reload() after saving new values to pick them up at runtime.
"""
import json
import os

_BASE     = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE, "config", "config.json")
_SEC_PATH = os.path.join(_BASE, "config", "secrets.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def reload():
    """Reload config from disk — call after web UI saves changes."""
    global _cfg, _sec
    global ZONE_HOME, LOCATION_HOME, TEMPERATURE_LOCATION
    global TEMPERATURE_UNITS, DISTANCE_UNITS, SPEED_UNITS
    global CLOCK_FORMAT, JOURNEY_CODE_SELECTED, JOURNEY_BLANK_FILLER
    global BRIGHTNESS, BRIGHTNESS_NIGHT, NIGHT_BRIGHTNESS
    global NIGHT_START, NIGHT_END, GPIO_SLOWDOWN, HAT_PWM_ENABLED
    global FORECAST_DAYS, MIN_ALTITUDE, MAX_FARTHEST, MAX_CLOSEST, EMAIL
    global MASTER_TRACKER, OTHER_TRACKER_HOSTNAMES
    global API_SOURCE_ORDER, API_SOURCE_ENABLED
    global TOMORROW_API_KEY, OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET
    global AIRLABS_API_KEYS, AIRLABS_API_KEY
    global FLIGHTAWARE_API_KEYS, FLIGHTAWARE_API_KEY, FLIGHTAWARE_MONTHLY_LIMIT
    global FLIGHTRADAR24_KEY

    _cfg = _load(_CFG_PATH)
    _sec = _load(_SEC_PATH)

    _loc  = _cfg.get("location", {})
    _disp = _cfg.get("display", {})
    _flt  = _cfg.get("flights", {})
    _ms   = _cfg.get("master_slave", {})

    # Location / units
    ZONE_HOME = _loc.get("zone_home", {
        "tl_y": 0.0, "tl_x": 0.0,
        "br_y": 0.0, "br_x": 0.0,
    })
    LOCATION_HOME         = _loc.get("location_home", [0.0, 0.0])
    TEMPERATURE_LOCATION  = _loc.get("temperature_location", "")
    TEMPERATURE_UNITS     = _loc.get("temperature_units", "imperial")
    DISTANCE_UNITS        = _loc.get("distance_units", "imperial")
    SPEED_UNITS           = _loc.get("speed_units", "imperial")
    CLOCK_FORMAT          = _loc.get("clock_format", "12hr")
    JOURNEY_CODE_SELECTED = _loc.get("journey_code", "ORD")
    JOURNEY_BLANK_FILLER  = _loc.get("journey_blank_filler", " ? ")

    # Display
    BRIGHTNESS        = _disp.get("brightness", 100)
    BRIGHTNESS_NIGHT  = _disp.get("brightness_night", 50)
    NIGHT_BRIGHTNESS  = _disp.get("night_brightness", False)
    NIGHT_START       = _disp.get("night_start", "22:00")
    NIGHT_END         = _disp.get("night_end", "06:00")
    GPIO_SLOWDOWN     = _disp.get("gpio_slowdown", 2)
    HAT_PWM_ENABLED   = _disp.get("hat_pwm_enabled", False)
    FORECAST_DAYS     = _disp.get("forecast_days", 3)

    # Flights
    MIN_ALTITUDE = _flt.get("min_altitude", 2000)
    MAX_FARTHEST = _flt.get("max_farthest", 3)
    MAX_CLOSEST  = _flt.get("max_closest", 3)
    EMAIL        = _flt.get("email", "")

    # Master / slave
    MASTER_TRACKER          = _ms.get("master_tracker", "")
    OTHER_TRACKER_HOSTNAMES = _ms.get("other_tracker_hostnames", [])

    # API Sources
    _apis = _cfg.get("api_sources", {})
    API_SOURCE_ORDER   = _apis.get("order",   ["AirLabs", "FlightAware", "FR24"])
    API_SOURCE_ENABLED = _apis.get("enabled", {})

    # Secrets
    TOMORROW_API_KEY          = _sec.get("tomorrow_api_key", "")
    OPENSKY_CLIENT_ID         = _sec.get("opensky_client_id", "")
    OPENSKY_CLIENT_SECRET     = _sec.get("opensky_client_secret", "")
    AIRLABS_API_KEYS          = _sec.get("airlabs_api_keys", [])
    AIRLABS_API_KEY           = AIRLABS_API_KEYS[0] if AIRLABS_API_KEYS else _sec.get("airlabs_api_key", "")
    FLIGHTAWARE_API_KEYS      = _sec.get("flightaware_api_keys", [])
    FLIGHTAWARE_API_KEY       = FLIGHTAWARE_API_KEYS[0] if FLIGHTAWARE_API_KEYS else _sec.get("flightaware_api_key", "")
    FLIGHTAWARE_MONTHLY_LIMIT = _sec.get("flightaware_monthly_limit", 4.50)
    FLIGHTRADAR24_KEY         = _sec.get("flightradar24_key", "")


# Initial load on import
_cfg = {}
_sec = {}
reload()
