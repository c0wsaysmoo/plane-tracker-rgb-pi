"""
Configuration — all values sourced exclusively from environment variables.

NO user-configurable defaults are stored in this file.
All configuration must be provided via:
  - /etc/plane-tracker.env (systemd EnvironmentFile for production)
  - .env file in the project root (for local development via python-dotenv)

See .env.example for documentation of all available variables and their defaults.
"""
import os

# Load .env file if present (for local dev; systemd uses EnvironmentFile instead)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass


def _bool(val: str) -> bool:
    return val.strip().lower() in ("true", "1", "yes", "on")


def _require(name: str) -> str:
    """Return env var value or empty string (caller decides how to handle missing)."""
    return os.environ.get(name, "")


# --- API Keys ---
FR24_API_KEY = _require("FR24_API_KEY")
TOMORROW_API_KEY = _require("TOMORROW_API_KEY")

# --- Bounding box for overhead flight detection ---
ZONE_HOME = {
    "tl_y": float(os.environ["ZONE_TL_LAT"]) if "ZONE_TL_LAT" in os.environ else 0.0,
    "tl_x": float(os.environ["ZONE_TL_LON"]) if "ZONE_TL_LON" in os.environ else 0.0,
    "br_y": float(os.environ["ZONE_BR_LAT"]) if "ZONE_BR_LAT" in os.environ else 0.0,
    "br_x": float(os.environ["ZONE_BR_LON"]) if "ZONE_BR_LON" in os.environ else 0.0,
}

# --- Home location (for distance calculations) ---
LOCATION_HOME = [
    float(os.environ["HOME_LAT"]) if "HOME_LAT" in os.environ else 0.0,
    float(os.environ["HOME_LON"]) if "HOME_LON" in os.environ else 0.0,
]

# --- Weather ---
TEMPERATURE_LOCATION = _require("TEMPERATURE_LOCATION")
TEMPERATURE_UNITS = os.environ.get("TEMPERATURE_UNITS", "metric")
FORECAST_DAYS = int(os.environ.get("FORECAST_DAYS", "3"))

# --- Display & units ---
DISTANCE_UNITS = os.environ.get("DISTANCE_UNITS", "metric")
CLOCK_FORMAT = os.environ.get("CLOCK_FORMAT", "24hr")
BRIGHTNESS = int(os.environ.get("BRIGHTNESS", "100"))
BRIGHTNESS_NIGHT = int(os.environ.get("BRIGHTNESS_NIGHT", "50"))
NIGHT_BRIGHTNESS = _bool(os.environ.get("NIGHT_BRIGHTNESS", "False"))
NIGHT_START = os.environ.get("NIGHT_START", "22:00")
NIGHT_END = os.environ.get("NIGHT_END", "06:00")
GPIO_SLOWDOWN = int(os.environ.get("GPIO_SLOWDOWN", "2"))
HAT_PWM_ENABLED = _bool(os.environ.get("HAT_PWM_ENABLED", "True"))

# --- Flight filtering ---
MIN_ALTITUDE = int(os.environ.get("MIN_ALTITUDE", "0"))
JOURNEY_CODE_SELECTED = _require("JOURNEY_CODE_SELECTED")
JOURNEY_BLANK_FILLER = os.environ.get("JOURNEY_BLANK_FILLER", " ? ")
SPEED_UNITS = os.environ.get("SPEED_UNITS", "metric")

# --- Logging & notifications ---
EMAIL = os.environ.get("EMAIL", "")
MAX_FARTHEST = int(os.environ.get("MAX_FARTHEST", "3"))
MAX_CLOSEST = int(os.environ.get("MAX_CLOSEST", "3"))
