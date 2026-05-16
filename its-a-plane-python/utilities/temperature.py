"""
temperature.py — Auto-selects master or slave mode based on config.
If MASTER_TRACKER = "" this Pi calls Tomorrow.io directly.
If MASTER_TRACKER = "hostname" this Pi polls the master's /weather/json endpoint.

The file cache constants (_load_file_cache, _save_file_cache, _TEMP_CACHE_FILE,
_FORECAST_CACHE_FILE, _CACHE_TTL) are always defined at module level so scenes
can import them in both master and slave mode.
"""

from datetime import datetime, timedelta
import time
import logging
import os
import socket
import json

try:
    from config import MASTER_TRACKER
except (ImportError, ModuleNotFoundError, NameError):
    MASTER_TRACKER = ""

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

# ── File cache — always available regardless of master/slave mode ─────────────

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
os.makedirs(_CACHE_DIR, exist_ok=True)
_TEMP_CACHE_FILE     = os.path.join(_CACHE_DIR, "temperature.json")
_FORECAST_CACHE_FILE = os.path.join(_CACHE_DIR, "forecast.json")
_CACHE_TTL           = 7200  # 2 hours


def _load_file_cache(path):
    try:
        with open(path, "r") as f:
            obj = json.load(f)
            return obj.get("data"), obj.get("ts", 0)
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None, 0


def _save_file_cache(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"data": data, "ts": time.time()}, f)
        os.replace(tmp, path)
    except (PermissionError, OSError) as e:
        logging.warning(f"Cannot write cache {path}: {e}")


# ── Slave mode ────────────────────────────────────────────────────────────────
if MASTER_TRACKER:
    import requests
    from requests.exceptions import RequestException

    def _url(path):
        host = MASTER_TRACKER.strip().rstrip("/")
        if not host.startswith("http"):
            # Only append .local:8080 if no port is already specified
            if ":" not in host:
                host = f"http://{host}.local:8080"
            else:
                host = f"http://{host}"
        return f"{host}{path}"

    def grab_temperature_and_humidity():
        try:
            r = requests.get(_url("/weather/json"), timeout=10)
            r.raise_for_status()
            data = r.json()
            temp = data.get("temperature")
            hum  = data.get("humidity")
            if temp is None or hum is None:
                raise ValueError("incomplete data")
            _save_file_cache(_TEMP_CACHE_FILE, [temp, hum])
            # Cache forecast too while we have it — saves a second HTTP call
            forecast = data.get("forecast", [])
            if isinstance(forecast, list) and forecast:
                _save_file_cache(_FORECAST_CACHE_FILE, forecast)
            return temp, hum
        except Exception as e:
            logging.error(f"[Slave/Weather] Cannot reach master: {e}")
            cached, ts = _load_file_cache(_TEMP_CACHE_FILE)
            if cached and (time.time() - ts) < _CACHE_TTL:
                data = cached if isinstance(cached, (list, tuple)) else [None, None]
                return (data[0], data[1]) if len(data) >= 2 else (None, None)
            return None, None

    def grab_forecast(tag="unknown"):
        try:
            r = requests.get(_url("/weather/json"), timeout=10)
            r.raise_for_status()
            data = r.json()
            # Master returns forecast inside /weather/json
            forecast = data.get("forecast", [])
            if isinstance(forecast, list) and forecast:
                _save_file_cache(_FORECAST_CACHE_FILE, forecast)
                return forecast
            raise ValueError("empty forecast")
        except Exception as e:
            logging.error(f"[Slave/Weather:{tag}] Cannot reach master: {e}")
            cached, ts = _load_file_cache(_FORECAST_CACHE_FILE)
            if cached and (time.time() - ts) < _CACHE_TTL:
                return cached
            return []

    print(f"[Weather] Slave mode — polling master at {_url('')}")

# ── Master mode ───────────────────────────────────────────────────────────────
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

    try:
        from config import TEMPERATURE_LOCATION
    except (ModuleNotFoundError, NameError, ImportError):
        TEMPERATURE_LOCATION = ""

    _session = None

    def _get_session():
        global _session
        if _session is None:
            _session = Session()
            retries = Retry(total=3, connect=3, read=3, backoff_factor=2,
                            allowed_methods=["GET", "POST"],
                            status_forcelist=[429, 500, 502, 503, 504],
                            raise_on_status=False)
            adapter = HTTPAdapter(max_retries=retries, pool_connections=2, pool_maxsize=2)
            _session.mount("https://", adapter)
            _session.mount("http://", adapter)
        return _session

    TOMORROW_API_URL = "https://api.tomorrow.io/v4"

    def grab_temperature_and_humidity():
        try:
            r = _get_session().get(
                f"{TOMORROW_API_URL}/weather/realtime",
                params={"location": TEMPERATURE_LOCATION, "units": TEMPERATURE_UNITS,
                        "apikey": TOMORROW_API_KEY},
                timeout=(5, 20),
            )
            if r.status_code == 429:
                logging.error("Tomorrow.io rate limit — using file cache")
                cached, ts = _load_file_cache(_TEMP_CACHE_FILE)
                if cached and (time.time() - ts) < _CACHE_TTL:
                    data = cached if isinstance(cached, (list, tuple)) else [None, None]; return (data[0], data[1]) if len(data) >= 2 else (None, None)
                return None, None
            r.raise_for_status()
            values = r.json().get("data", {}).get("values", {})
            temp, hum = values.get("temperature"), values.get("humidity")
            if temp is None or hum is None:
                return None, None
            _save_file_cache(_TEMP_CACHE_FILE, [temp, hum])
            return temp, hum
        except RequestException as e:
            logging.error(f"Temperature request failed: {e}")
            cached, ts = _load_file_cache(_TEMP_CACHE_FILE)
            if cached and (time.time() - ts) < _CACHE_TTL:
                data = cached if isinstance(cached, (list, tuple)) else [None, None]; return (data[0], data[1]) if len(data) >= 2 else (None, None)
            return None, None

    def grab_forecast(tag="unknown"):
        dt = datetime.now() - timedelta(days=1)
        try:
            resp = _get_session().post(
                f"{TOMORROW_API_URL}/timelines",
                headers={"Accept-Encoding": "gzip", "accept": "application/json",
                         "content-type": "application/json"},
                params={"apikey": TOMORROW_API_KEY},
                json={
                    "location": TEMPERATURE_LOCATION, "units": TEMPERATURE_UNITS,
                    "timezone": "auto", "dailyStartHour": 6,
                    "fields": ["temperatureMin", "temperatureMax", "weatherCodeFullDay",
                               "sunriseTime", "sunsetTime", "moonPhase"],
                    "timesteps": ["1d"],
                    "endTime": (dt + timedelta(days=int(FORECAST_DAYS))).isoformat(),
                },
                timeout=(5, 20),
            )
            if resp.status_code == 429:
                logging.error(f"[Forecast:{tag}] Rate limit — using file cache")
                cached, ts = _load_file_cache(_FORECAST_CACHE_FILE)
                if cached and (time.time() - ts) < _CACHE_TTL:
                    return cached
                return []
            resp.raise_for_status()
            timelines = resp.json().get("data", {}).get("timelines", [])
            if not timelines:
                return []
            intervals = timelines[0].get("intervals", [])
            if intervals:
                _save_file_cache(_FORECAST_CACHE_FILE, intervals)
            return intervals
        except RequestException as e:
            logging.error(f"[Forecast:{tag}] Request failed: {e}")
            cached, ts = _load_file_cache(_FORECAST_CACHE_FILE)
            if cached and (time.time() - ts) < _CACHE_TTL:
                return cached
            return []
        except KeyError as e:
            logging.error(f"[Forecast:{tag}] Unexpected data format: {e}")
            return []
