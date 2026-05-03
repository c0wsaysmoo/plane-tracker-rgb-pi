"""
Tomorrow.io weather API wrapper with built-in rate limiting.

Free tier: 25 requests/hour (one every ~2.4 minutes).
We enforce a minimum 3-minute gap between ALL API calls to stay safe.
"""
from datetime import datetime, timedelta
import time
import logging
import socket
import threading

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
    TOMORROW_API_KEY = None
    TEMPERATURE_UNITS = "metric"
    FORECAST_DAYS = 3

if TEMPERATURE_UNITS not in ("metric", "imperial"):
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

logger = logging.getLogger(__name__)

# ─── Rate Limiter ────────────────────────────────────────────────────────────
# Tomorrow.io free tier: 25 requests/hour = 1 every 144s
# We use 180s (3 min) minimum between any API call for safety margin.
_MIN_INTERVAL_S = 180  # seconds between API calls
_last_call_ts = 0.0
_rate_lock = threading.Lock()


def _rate_limited() -> bool:
    """Return True if we should skip this API call due to rate limiting."""
    global _last_call_ts
    with _rate_lock:
        elapsed = time.time() - _last_call_ts
        if elapsed < _MIN_INTERVAL_S:
            return True
        return False


def _record_call():
    """Record that an API call was just made."""
    global _last_call_ts
    with _rate_lock:
        _last_call_ts = time.time()


# ─── DNS helper ──────────────────────────────────────────────────────────────

def is_dns_error(exc: Exception) -> bool:
    cause = exc
    while cause:
        if isinstance(cause, socket.gaierror):
            return True
        cause = cause.__cause__
    return False


# ─── HTTP Session (shared, with retries on server errors only) ───────────────
_session = None


def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()

        retries = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=3,
            allowed_methods=["GET", "POST"],
            # Do NOT retry on 429 — that makes rate limiting worse
            status_forcelist=[500, 502, 503, 504],
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


# ─── API URL ─────────────────────────────────────────────────────────────────
TOMORROW_API_URL = "https://api.tomorrow.io/v4"


# ─── Temperature & Humidity ──────────────────────────────────────────────────
_cached_temp = None
_cached_temp_ts = 0.0
_TEMP_CACHE_TTL = 300  # 5 minutes


def grab_temperature_and_humidity():
    """
    Fetch current temperature and humidity.
    Returns cached data if called within the cache TTL or rate limit window.
    """
    global _cached_temp, _cached_temp_ts

    if not TOMORROW_API_KEY:
        logger.warning("TOMORROW_API_KEY not set — skipping temperature fetch")
        return None, None

    # Return cache if still fresh
    if _cached_temp and (time.time() - _cached_temp_ts) < _TEMP_CACHE_TTL:
        return _cached_temp

    # Rate limit check
    if _rate_limited():
        logger.debug("Rate limit: skipping temperature API call, using cache")
        return _cached_temp if _cached_temp else (None, None)

    try:
        _record_call()
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
            logger.warning("Tomorrow.io rate limit hit (429) — will retry later")
            return _cached_temp if _cached_temp else (None, None)

        request.raise_for_status()

        data = request.json().get("data", {}).get("values", {})
        temperature = data.get("temperature")
        humidity = data.get("humidity")

        if temperature is None or humidity is None:
            logger.error("Incomplete data from Tomorrow.io API")
            return _cached_temp if _cached_temp else (None, None)

        _cached_temp = (temperature, humidity)
        _cached_temp_ts = time.time()
        return temperature, humidity

    except (RequestException, ValueError) as e:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        if is_dns_error(e):
            logger.error(
                f"[{timestamp}] DNS failure resolving api.tomorrow.io - will retry"
            )
        else:
            logger.error(
                f"[{timestamp}] Temperature request failed: {e}"
            )

        return _cached_temp if _cached_temp else (None, None)


# ─── Forecast ────────────────────────────────────────────────────────────────
_cached_forecast = None
_cached_forecast_ts = 0.0
_FORECAST_CACHE_TTL = 3600  # 1 hour


def grab_forecast(tag="unknown"):
    """
    Fetch daily forecast data.
    Returns cached data if called within the cache TTL or rate limit window.
    """
    global _cached_forecast, _cached_forecast_ts

    if not TOMORROW_API_KEY:
        logger.warning("TOMORROW_API_KEY not set — skipping forecast fetch")
        return []

    # Return cache if still fresh
    if _cached_forecast and (time.time() - _cached_forecast_ts) < _FORECAST_CACHE_TTL:
        return _cached_forecast

    # Rate limit check
    if _rate_limited():
        logger.debug(f"[Forecast:{tag}] Rate limit: skipping API call, using cache")
        return _cached_forecast if _cached_forecast else []

    dt = datetime.now() - timedelta(days=1)

    try:
        _record_call()
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
            logger.warning(f"[Forecast:{tag}] Tomorrow.io rate limit hit (429) — will retry later")
            return _cached_forecast if _cached_forecast else []

        resp.raise_for_status()

        data = resp.json().get("data", {})
        timelines = data.get("timelines", [])
        if not timelines:
            logger.error(f"[Forecast:{tag}] No timelines returned from API")
            return _cached_forecast if _cached_forecast else []

        intervals = timelines[0].get("intervals", [])
        if not intervals:
            logger.error(f"[Forecast:{tag}] Timelines returned but no intervals")
            return _cached_forecast if _cached_forecast else []

        _cached_forecast = intervals
        _cached_forecast_ts = time.time()
        return intervals

    except RequestException as e:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        if is_dns_error(e):
            logger.error(
                f"[{timestamp}] [Forecast:{tag}] DNS failure resolving api.tomorrow.io - will retry"
            )
        else:
            logger.error(
                f"[{timestamp}] [Forecast:{tag}] API request failed: {e}"
            )
        return _cached_forecast if _cached_forecast else []

    except KeyError as e:
        logger.error(f"[Forecast:{tag}] Unexpected data format: {e}")
        return _cached_forecast if _cached_forecast else []
