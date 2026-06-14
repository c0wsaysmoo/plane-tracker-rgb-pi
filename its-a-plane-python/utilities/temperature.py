"""
temperature.py — Auto-selects master or slave mode based on config.
If MASTER_TRACKER = "" this Pi calls Tomorrow.io directly.
If MASTER_TRACKER = "hostname" this Pi polls the master's /weather/json endpoint.
"""

from datetime import datetime, timedelta
import time
import logging
import os
import socket
import json

# ─── Master/slave routing ─────────────────────────────────────────────────────
try:
    from config import MASTER_TRACKER
except (ImportError, ModuleNotFoundError, NameError):
    MASTER_TRACKER = ""

# ─── Shared config (both modes need these) ───────────────────────────────────
try:
    from config import TEMPERATURE_UNITS
except (ModuleNotFoundError, NameError, ImportError):
    TEMPERATURE_UNITS = "metric"

try:
    from config import FORECAST_DAYS
except (ModuleNotFoundError, NameError, ImportError):
    FORECAST_DAYS = 3

if TEMPERATURE_UNITS not in ("metric", "imperial"):
    TEMPERATURE_UNITS = "metric"


# ─────────────────────────────────────────────────────────────────────────────
# SLAVE MODE — poll weather data from the master Pi
# ─────────────────────────────────────────────────────────────────────────────
if MASTER_TRACKER:
    import requests
    from requests.exceptions import RequestException

    def _url(path):
        host = MASTER_TRACKER.rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}.local:8080"
        return f"{host}{path}"

    def grab_temperature_and_humidity():
        """Fetch current temperature, humidity and weather code from the master's /weather/json endpoint."""
        try:
            r = requests.get(_url("/weather/json"), timeout=10)
            r.raise_for_status()
            data = r.json()
            temperature  = data.get("temperature")
            humidity     = data.get("humidity")
            weather_code = data.get("weatherCode")
            if temperature is None or humidity is None:
                logging.warning("[Slave/Weather] Master returned incomplete temp/humidity data")
                return None, None, None
            return temperature, humidity, weather_code
        except RequestException as e:
            logging.error(f"[Slave/Weather] Cannot reach master for weather: {e}")
            return None, None, None

    def grab_forecast(tag="unknown"):
        """Fetch forecast intervals from the master's /weather/json endpoint."""
        try:
            r = requests.get(_url("/weather/json"), timeout=10)
            r.raise_for_status()
            data = r.json()
            forecast = data.get("forecast", [])
            if not isinstance(forecast, list):
                logging.warning(f"[Slave/Weather:{tag}] Master returned non-list forecast")
                return []
            return forecast
        except RequestException as e:
            logging.error(f"[Slave/Weather:{tag}] Cannot reach master for forecast: {e}")
            return []

    print(f"[Weather] Slave mode — polling master at {_url('')}")


# ─────────────────────────────────────────────────────────────────────────────
# MASTER MODE — full Tomorrow.io stack with persistent file cache
# ─────────────────────────────────────────────────────────────────────────────
else:
    print("[Weather] Master mode — calling Tomorrow.io directly")

    from requests import Session
    from requests.adapters import HTTPAdapter
    from requests.exceptions import RequestException
    from urllib3.util.retry import Retry

    try:
        from config import TOMORROW_API_KEY
    except (ModuleNotFoundError, NameError, ImportError):
        TOMORROW_API_KEY = None

    from config import TEMPERATURE_LOCATION

    # ─── Persistent File Cache ────────────────────────────────────────────────
    _CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
    os.makedirs(_CACHE_DIR, exist_ok=True)
    _TEMP_CACHE_FILE     = os.path.join(_CACHE_DIR, "temperature.json")
    _FORECAST_CACHE_FILE = os.path.join(_CACHE_DIR, "forecast.json")
    _CACHE_TTL           = 7200  # 2 hours — use file cache if API fails within this window

    # ─── Day/Night helpers ────────────────────────────────────────────────────
    def _is_daytime():
        """Return True if current local time is between today's sunrise and sunset."""
        try:
            cached, _ = _load_file_cache(_FORECAST_CACHE_FILE)
            if cached and isinstance(cached, list):
                today = datetime.now().date().isoformat()
                for interval in cached:
                    if interval.get("startTime", "").startswith(today):
                        values = interval.get("values", {})
                        sunrise_str = values.get("sunriseTime")
                        sunset_str  = values.get("sunsetTime")
                        if sunrise_str and sunset_str:
                            def _parse(s):
                                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                            return _parse(sunrise_str) <= datetime.now() <= _parse(sunset_str)
        except Exception:
            pass
        h = datetime.now().hour
        return 6 <= h < 20

    def _to_day_night_code(code):
        """Convert a 4-digit base weatherCode to the 5-digit day/night variant."""
        if code is None:
            return None
        code = int(code)
        if code < 10000:
            return code * 10 + (0 if _is_daytime() else 1)
        return code

    # ─── Invalidate caches if units have changed ──────────────────────────────
    def _invalidate_on_units_change():
        for path in (_TEMP_CACHE_FILE, _FORECAST_CACHE_FILE):
            try:
                with open(path, "r") as f:
                    obj = json.load(f)
                if obj.get("units") != TEMPERATURE_UNITS:
                    logging.info(f"[Weather] Units changed, deleting stale cache: {path}")
                    os.remove(path)
            except (FileNotFoundError, json.JSONDecodeError):
                pass

    _invalidate_on_units_change()

    def _load_file_cache(path, units=None):
        """Load cached data from file. Returns (data, timestamp) or (None, 0).
        If `units` is provided and doesn't match what's stored, treats cache as a miss
        so a units change (metric ↔ imperial) always triggers a fresh API call."""
        try:
            with open(path, "r") as f:
                obj = json.load(f)
            if units is not None and obj.get("units") != units:
                logging.info(f"Cache units mismatch ({obj.get('units')!r} → {units!r}), invalidating {path}")
                return None, 0
            return obj.get("data"), obj.get("ts", 0)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return None, 0

    def _save_file_cache(path, data, units=None):
        """Save data + timestamp (+ units) to file cache (atomic via rename)."""
        try:
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"data": data, "ts": time.time(), "units": units}, f)
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
                    "units":    TEMPERATURE_UNITS,
                    "apikey":   TOMORROW_API_KEY
                },
                timeout=(5, 20)
            )

            if request.status_code == 429:
                logging.error("Rate limit reached, trying file cache")
                cached, ts = _load_file_cache(_TEMP_CACHE_FILE, units=TEMPERATURE_UNITS)
                if cached and (time.time() - ts) < _CACHE_TTL:
                    return tuple(cached) if isinstance(cached, list) else cached
                return None, None

            request.raise_for_status()

            data         = request.json().get("data", {}).get("values", {})
            temperature  = data.get("temperature")
            humidity     = data.get("humidity")
            weather_code = _to_day_night_code(data.get("weatherCode"))

            if temperature is None or humidity is None:
                logging.error("Incomplete data from API")
                return None, None, None

            _save_file_cache(_TEMP_CACHE_FILE, [temperature, humidity, weather_code], units=TEMPERATURE_UNITS)
            return temperature, humidity, weather_code

        except (RequestException, ValueError) as e:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if is_dns_error(e):
                logging.error(f"[{timestamp}] DNS failure resolving api.tomorrow.io - will retry")
            else:
                logging.error(f"[{timestamp}] Temperature request failed: {e}")

            cached, ts = _load_file_cache(_TEMP_CACHE_FILE, units=TEMPERATURE_UNITS)
            if cached and (time.time() - ts) < _CACHE_TTL:
                vals = tuple(cached) if isinstance(cached, list) else cached
                if len(vals) == 2:
                    return vals[0], vals[1], None
                return vals
            return None, None, None


    def grab_forecast(tag="unknown"):
        dt = datetime.now() - timedelta(days=1)

        try:
            s = get_session()
            resp = s.post(
                f"{TOMORROW_API_URL}/timelines",
                headers={
                    "Accept-Encoding": "gzip",
                    "accept":          "application/json",
                    "content-type":    "application/json"
                },
                params={"apikey": TOMORROW_API_KEY},
                json={
                    "location":       TEMPERATURE_LOCATION,
                    "units":          TEMPERATURE_UNITS,
                    "timezone":       "auto",
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
                    "endTime":   (dt + timedelta(days=int(FORECAST_DAYS))).isoformat(),
                },
                timeout=(5, 20)
            )

            if resp.status_code == 429:
                logging.error(f"[Forecast:{tag}] Rate limit reached, trying file cache")
                cached, ts = _load_file_cache(_FORECAST_CACHE_FILE, units=TEMPERATURE_UNITS)
                if cached and (time.time() - ts) < _CACHE_TTL:
                    return cached
                return []

            resp.raise_for_status()

            data      = resp.json().get("data", {})
            timelines = data.get("timelines", [])
            if not timelines:
                logging.error(f"[Forecast:{tag}] No timelines returned from API")
                return []

            intervals = timelines[0].get("intervals", [])
            if not intervals:
                logging.error(f"[Forecast:{tag}] Timelines returned but no intervals")
                return []

            _save_file_cache(_FORECAST_CACHE_FILE, intervals, units=TEMPERATURE_UNITS)
            return intervals

        except RequestException as e:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if is_dns_error(e):
                logging.error(f"[{timestamp}] [Forecast:{tag}] DNS failure resolving api.tomorrow.io - will retry")
            else:
                logging.error(f"[{timestamp}] [Forecast:{tag}] API request failed: {e}")

            cached, ts = _load_file_cache(_FORECAST_CACHE_FILE, units=TEMPERATURE_UNITS)
            if cached and (time.time() - ts) < _CACHE_TTL:
                return cached
            return []

        except KeyError as e:
            logging.error(f"[Forecast:{tag}] Unexpected data format: {e}")
            return []
