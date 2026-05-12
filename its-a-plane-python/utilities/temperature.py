from datetime import datetime, timedelta
import time
import logging
import os
import socket
import json

from requests import Session
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry

# Attempt to load config data
try:
    from config import TOMORROW_API_KEY
    from config import TEMPERATURE_UNITS
    from config import FORECAST_DAYS

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    TOMORROW_API_KEY = None
    TEMPERATURE_UNITS = "metric"
    FORECAST_DAYS = 3

if TEMPERATURE_UNITS != "metric" and TEMPERATURE_UNITS != "imperial":
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

# ─── Persistent File Cache ───────────────────────────────────────────────────
# Survives reboots — prevents blank display when API is temporarily unavailable.
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
os.makedirs(_CACHE_DIR, exist_ok=True)
_TEMP_CACHE_FILE = os.path.join(_CACHE_DIR, "temperature.json")
_FORECAST_CACHE_FILE = os.path.join(_CACHE_DIR, "forecast.json")
_CACHE_TTL = 7200  # 2 hours — use file cache if API fails within this window


def _load_file_cache(path):
    """Load cached data from file. Returns (data, timestamp) or (None, 0)."""
    try:
        with open(path, "r") as f:
            obj = json.load(f)
            return obj.get("data"), obj.get("ts", 0)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None, 0


def _save_file_cache(path, data):
    """Save data + timestamp to file cache (atomic via rename)."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"data": data, "ts": time.time()}, f)
        os.replace(tmp, path)  # atomic on POSIX
    except (PermissionError, OSError) as e:
        logging.warning(f"Cannot write cache {path}: {e}")


def is_dns_error(exc: Exception) -> bool:
    cause = exc
    while cause:
        if isinstance(cause, socket.gaierror):
            return True
        cause = cause.__cause__
    return False
    
_session = None

def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()

        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=2,
            allowed_methods=["GET", "POST"],
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retries,
            pool_connections=2,
            pool_maxsize=2,
        )

        _session.mount("https://", adapter)
        _session.mount("http://", adapter)

    return _session
    
# Weather API
TOMORROW_API_URL = "https://api.tomorrow.io/v4"

def grab_temperature_and_humidity():
    try:
        s = get_session()
        request = s.get(
            f"{TOMORROW_API_URL}/weather/realtime",
            params={
                "location": TEMPERATURE_LOCATION,
                "units": TEMPERATURE_UNITS,
                "apikey": TOMORROW_API_KEY
            },
            timeout=(5, 20)
        )

        if request.status_code == 429:
            logging.error("Rate limit reached, trying file cache")
            cached, ts = _load_file_cache(_TEMP_CACHE_FILE)
            if cached and (time.time() - ts) < _CACHE_TTL:
                return tuple(cached) if isinstance(cached, list) else cached
            return None, None

        request.raise_for_status()

        data = request.json().get("data", {}).get("values", {})
        temperature = data.get("temperature")
        humidity = data.get("humidity")

        if temperature is None or humidity is None:
            logging.error("Incomplete data from API")
            return None, None

        _save_file_cache(_TEMP_CACHE_FILE, [temperature, humidity])
        return temperature, humidity

    except (RequestException, ValueError) as e:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        if is_dns_error(e):
            logging.error(
                f"[{timestamp}] DNS failure resolving api.tomorrow.io - will retry"
            )
        else:
            logging.error(
                f"[{timestamp}] Temperature request failed: {e}"
            )

        # Try file cache before giving up
        cached, ts = _load_file_cache(_TEMP_CACHE_FILE)
        if cached and (time.time() - ts) < _CACHE_TTL:
            return tuple(cached) if isinstance(cached, list) else cached
        return None, None
        
        
def grab_forecast(tag="unknown"):
    dt = datetime.now() - timedelta(days=1)

    try:
        s = get_session()
        resp = s.post(
            f"{TOMORROW_API_URL}/timelines",
            headers={
                "Accept-Encoding": "gzip",
                "accept": "application/json",
                "content-type": "application/json"
            },
            params={"apikey": TOMORROW_API_KEY},
            json={
                "location": TEMPERATURE_LOCATION,
                "units": TEMPERATURE_UNITS,
                "timezone": "auto",
                "dailyStartHour": 6,
                "fields": [
                    "temperatureMin",
                    "temperatureMax",
                    "weatherCodeFullDay",
                    "sunriseTime",
                    "sunsetTime",
                    "moonPhase"
                ],
                "timesteps": ["1d"],
                "endTime": (dt + timedelta(days=int(FORECAST_DAYS))).isoformat(),
            },
            timeout=(5, 20)
        )

        if resp.status_code == 429:
            logging.error(f"[Forecast:{tag}] Rate limit reached, trying file cache")
            cached, ts = _load_file_cache(_FORECAST_CACHE_FILE)
            if cached and (time.time() - ts) < _CACHE_TTL:
                return cached
            return []

        resp.raise_for_status()

        data = resp.json().get("data", {})
        timelines = data.get("timelines", [])
        if not timelines:
            logging.error(f"[Forecast:{tag}] No timelines returned from API")
            return []

        intervals = timelines[0].get("intervals", [])
        if not intervals:
            logging.error(f"[Forecast:{tag}] Timelines returned but no intervals")
            return []

        _save_file_cache(_FORECAST_CACHE_FILE, intervals)
        return intervals

    except RequestException as e:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        if is_dns_error(e):
            logging.error(
                f"[{timestamp}] [Forecast:{tag}] DNS failure resolving api.tomorrow.io - will retry"
            )
        else:
            logging.error(
                f"[{timestamp}] [Forecast:{tag}] API request failed: {e}"
            )

        # Try file cache before giving up
        cached, ts = _load_file_cache(_FORECAST_CACHE_FILE)
        if cached and (time.time() - ts) < _CACHE_TTL:
            return cached
        return []

    except KeyError as e:
        logging.error(f"[Forecast:{tag}] Unexpected data format: {e}")
        return []
