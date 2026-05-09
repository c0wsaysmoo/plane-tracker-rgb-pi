from datetime import datetime, timedelta
import time
import logging
import socket
import json
import os

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

# ===== CACHE SETTINGS =====
# Cache durations match the original scene refresh intervals
# This prevents redundant API calls on reboot while maintaining normal refresh rates
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
TEMPERATURE_CACHE_DURATION = 600   # 10 minutes (matches TEMPERATURE_REFRESH_SECONDS in scenes/temperature.py)
FORECAST_CACHE_DURATION = 3600     # 1 hour (matches hourly refresh in scenes/daysforecast.py)
os.makedirs(CACHE_DIR, exist_ok=True)

def load_cache(cache_file, max_age_seconds):
    """Load cached data if it exists and is fresh"""
    cache_path = os.path.join(CACHE_DIR, cache_file)
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, 'r') as f:
            cache_data = json.load(f)

        cached_time = datetime.fromisoformat(cache_data['timestamp'])
        age_seconds = (datetime.now() - cached_time).total_seconds()

        if age_seconds < max_age_seconds:
            logging.info(f"Using cached data from {cache_file} (age: {int(age_seconds/60)} minutes)")
            return cache_data['data']
        else:
            logging.info(f"Cache expired for {cache_file} (age: {int(age_seconds/60)} minutes)")
            return None
    except Exception as e:
        logging.warning(f"Cache read error: {e}")
        return None

def save_cache(cache_file, data):
    """Save data to cache with timestamp"""
    cache_path = os.path.join(CACHE_DIR, cache_file)
    try:
        cache_data = {
            'timestamp': datetime.now().isoformat(),
            'data': data
        }
        with open(cache_path, 'w') as f:
            json.dump(cache_data, f)
        logging.info("Data cached successfully")
    except Exception as e:
        logging.warning(f"Cache write error: {e}")

# ===== END CACHE SETTINGS =====

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
    # Try cache first - 10 minute cache matches scene refresh interval
    cached = load_cache('temperature.json', TEMPERATURE_CACHE_DURATION)
    if cached is not None:
        return cached.get('temperature'), cached.get('humidity')

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
            logging.error("Rate limit reached, returning error state")
            return None, None

        request.raise_for_status()

        data = request.json().get("data", {}).get("values", {})
        temperature = data.get("temperature")
        humidity = data.get("humidity")

        if temperature is None or humidity is None:
            logging.error("Incomplete data from API")
            return None, None

        # Cache the successful response
        save_cache('temperature.json', {'temperature': temperature, 'humidity': humidity})

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

        return None, None


def grab_forecast(tag="unknown"):
    # Try cache first - 1 hour cache matches scene refresh interval
    cached = load_cache('forecast.json', FORECAST_CACHE_DURATION)
    if cached is not None:
        return cached

    # Use local time minus 1 day as the window start; API uses timezone:auto
    # to correctly anchor daily boundaries to the local timezone
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

        # Cache the successful response
        save_cache('forecast.json', intervals)

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
        return []

    except KeyError as e:
        logging.error(f"[Forecast:{tag}] Unexpected data format: {e}")
        return []
