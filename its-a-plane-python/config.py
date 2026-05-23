"""
Configuration — values sourced from environment variables with optional JSON overlay.

Priority (highest first):
  1. config/config.json (written by web config UI)
  2. /etc/plane-tracker.env (systemd EnvironmentFile)
  3. .env file in project root (python-dotenv)

See .env.example for documentation of all available variables and their defaults.
"""
import json
import logging
import os

_logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_JSON = os.path.join(_BASE_DIR, "config", "config.json")

# Load .env file if present (for local dev; systemd uses EnvironmentFile instead)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE_DIR, "..", ".env"))
except ImportError:
    pass

# JSON overlay (loaded once, refreshed on reload())
_json_config = {}


def _load_json_config():
    """Load config/config.json if it exists. Returns dict or empty."""
    global _json_config
    if os.path.exists(_CONFIG_JSON):
        try:
            with open(_CONFIG_JSON, "r", encoding="utf-8") as f:
                _json_config = json.load(f)
            _logger.info(f"[Config] Loaded JSON overlay from {_CONFIG_JSON}")
        except Exception as e:
            _logger.error(f"[Config] Failed to load {_CONFIG_JSON}: {e}")
            _json_config = {}
    else:
        _json_config = {}
    return _json_config


def _get(name: str, default: str = "") -> str:
    """Get config value: JSON overlay first, then env var, then default."""
    if name in _json_config:
        return str(_json_config[name])
    return os.environ.get(name, default)


def _bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def config_source():
    """Return 'json' if JSON overlay is active, else 'env'."""
    return "json" if _json_config else "env"


def _apply():
    """(Re)apply all config values from current sources."""
    global FR24_API_KEY, TOMORROW_API_KEY, AIRLABS_API_KEY, NPS_API_KEY
    global ZONE_HOME, LOCATION_HOME
    global TEMPERATURE_LOCATION, TEMPERATURE_UNITS, FORECAST_DAYS
    global DISTANCE_UNITS, CLOCK_FORMAT, BRIGHTNESS, BRIGHTNESS_NIGHT
    global NIGHT_BRIGHTNESS, NIGHT_START, NIGHT_END, GPIO_SLOWDOWN, HAT_PWM_ENABLED
    global LED_RGB_SEQUENCE, TIDE_STATION
    global MIN_ALTITUDE, JOURNEY_CODE_SELECTED, JOURNEY_BLANK_FILLER, SPEED_UNITS
    global EMAIL, MAX_FARTHEST, MAX_CLOSEST

    # --- API Keys ---
    FR24_API_KEY = _get("FR24_API_KEY")
    TOMORROW_API_KEY = _get("TOMORROW_API_KEY")
    AIRLABS_API_KEY = _get("AIRLABS_API_KEY")
    NPS_API_KEY = _get("NPS_API_KEY")

    # --- Bounding box for overhead flight detection ---
    ZONE_HOME = {
        "tl_y": float(_get("ZONE_TL_LAT", "0")),
        "tl_x": float(_get("ZONE_TL_LON", "0")),
        "br_y": float(_get("ZONE_BR_LAT", "0")),
        "br_x": float(_get("ZONE_BR_LON", "0")),
    }

    # --- Home location (for distance calculations) ---
    LOCATION_HOME = [
        float(_get("HOME_LAT", "0")),
        float(_get("HOME_LON", "0")),
    ]

    # --- Weather ---
    TEMPERATURE_LOCATION = _get("TEMPERATURE_LOCATION")
    TEMPERATURE_UNITS = _get("TEMPERATURE_UNITS", "metric")
    FORECAST_DAYS = int(_get("FORECAST_DAYS", "3"))

    # --- Display & units ---
    DISTANCE_UNITS = _get("DISTANCE_UNITS", "metric")
    CLOCK_FORMAT = _get("CLOCK_FORMAT", "24hr")
    BRIGHTNESS = int(_get("BRIGHTNESS", "100"))
    BRIGHTNESS_NIGHT = int(_get("BRIGHTNESS_NIGHT", "50"))
    NIGHT_BRIGHTNESS = _bool(_get("NIGHT_BRIGHTNESS", "False"))
    NIGHT_START = _get("NIGHT_START", "22:00")
    NIGHT_END = _get("NIGHT_END", "06:00")
    GPIO_SLOWDOWN = int(_get("GPIO_SLOWDOWN", "2"))
    LED_RGB_SEQUENCE = _get("LED_RGB_SEQUENCE", "RGB")
    TIDE_STATION = _get("TIDE_STATION", "")
    HAT_PWM_ENABLED = _bool(_get("HAT_PWM_ENABLED", "True"))

    # --- Flight filtering ---
    MIN_ALTITUDE = int(_get("MIN_ALTITUDE", "0"))
    JOURNEY_CODE_SELECTED = _get("JOURNEY_CODE_SELECTED")
    _raw_filler = _get("JOURNEY_BLANK_FILLER", "").strip()
    JOURNEY_BLANK_FILLER = f" {_raw_filler} " if _raw_filler else " ? "
    SPEED_UNITS = _get("SPEED_UNITS", "metric")

    # --- Logging & notifications ---
    EMAIL = _get("EMAIL")
    MAX_FARTHEST = int(_get("MAX_FARTHEST", "3"))
    MAX_CLOSEST = int(_get("MAX_CLOSEST", "3"))


def reload():
    """Reload config from all sources. Called by web config UI after saving."""
    _load_json_config()
    _apply()
    _logger.info("[Config] Configuration reloaded")


# Initial load
_load_json_config()
_apply()
