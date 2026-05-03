"""
Configuration — all values sourced from environment variables.

The environment is loaded from /etc/plane-tracker.env (systemd EnvironmentFile)
or from the project-root .env file (via python-dotenv for local development).

See .env.example for documentation of all variables.
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


# --- Bounding box for overhead flight detection ---
ZONE_HOME = {
    "tl_y": float(os.environ.get("ZONE_TL_LAT", "51.595")),
    "tl_x": float(os.environ.get("ZONE_TL_LON", "-0.314")),
    "br_y": float(os.environ.get("ZONE_BR_LAT", "51.47")),
    "br_x": float(os.environ.get("ZONE_BR_LON", "-0.111")),
}

# --- Home location (for distance calculations) ---
LOCATION_HOME = [
    float(os.environ.get("HOME_LAT", "51.55864")),
    float(os.environ.get("HOME_LON", "-0.177332")),
]

# --- Weather ---
TEMPERATURE_LOCATION = os.environ.get("TEMPERATURE_LOCATION", "51.55864,-0.177332")
TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY", "")
TEMPERATURE_UNITS = os.environ.get("TEMPERATURE_UNITS", "metric")
FORECAST_DAYS = int(os.environ.get("FORECAST_DAYS", "3"))

# --- Display & units ---
DISTANCE_UNITS = os.environ.get("DISTANCE_UNITS", "imperial")
CLOCK_FORMAT = os.environ.get("CLOCK_FORMAT", "12hr")
BRIGHTNESS = int(os.environ.get("BRIGHTNESS", "100"))
BRIGHTNESS_NIGHT = int(os.environ.get("BRIGHTNESS_NIGHT", "50"))
NIGHT_BRIGHTNESS = _bool(os.environ.get("NIGHT_BRIGHTNESS", "True"))
NIGHT_START = os.environ.get("NIGHT_START", "20:00")
NIGHT_END = os.environ.get("NIGHT_END", "06:00")
GPIO_SLOWDOWN = int(os.environ.get("GPIO_SLOWDOWN", "2"))
HAT_PWM_ENABLED = _bool(os.environ.get("HAT_PWM_ENABLED", "True"))

# --- Flight filtering ---
MIN_ALTITUDE = int(os.environ.get("MIN_ALTITUDE", "1000"))
JOURNEY_CODE_SELECTED = os.environ.get("JOURNEY_CODE_SELECTED", "LHR")
JOURNEY_BLANK_FILLER = os.environ.get("JOURNEY_BLANK_FILLER", " ? ")

# --- Logging & notifications ---
EMAIL = os.environ.get("EMAIL", "")
MAX_FARTHEST = int(os.environ.get("MAX_FARTHEST", "3"))
MAX_CLOSEST = int(os.environ.get("MAX_CLOSEST", "3"))

# --- FlightRadar24 API ---
FR24_API_KEY = os.environ.get("FR24_API_KEY", "")
