"""
iss.py — ISS overhead pass predictions.

Computes passes locally using TLE orbital data from CelesTrak + the
ephem library.  No external pass-prediction API needed.  TLE is refreshed
every 12 hours (or on first run); pass computation runs every 30 minutes
and takes <1 second on a Pi Zero.

Reports ALL passes above 10 degrees max elevation (not just eye-visible
ones) — so you get an alert whenever the ISS is overhead, day or night.

Requires:  pip install ephem

Usage:
    from utilities.iss import get_iss_alert
    alert = get_iss_alert()
    # {"text": "ISS 3m", "color": "white"}  or  None
"""

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone, timedelta

import requests

try:
    import ephem
except ImportError:
    ephem = None

logger = logging.getLogger(__name__)

_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "iss.json")
_TLE_CACHE_FILE = os.path.join(_CACHE_DIR, "iss_tle.json")
_POLL_INTERVAL = 1800      # recompute passes every 30 minutes
_TLE_REFRESH = 43200       # refresh TLE every 12 hours
_ALERT_WINDOW = 600        # show alert 10 minutes before pass
_MIN_ELEVATION = 10        # minimum max-elevation to include a pass (degrees)
_PREDICT_HOURS = 24        # how far ahead to predict

# Compass direction from azimuth
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _az_to_compass(az_rad):
    """Convert azimuth in radians to 16-point compass direction."""
    deg = math.degrees(float(az_rad)) % 360
    idx = int((deg + 11.25) / 22.5) % 16
    return _COMPASS[idx]


# ── TLE management ──────────────────────────────────────────────────────────

_tle_lines = None   # (name, line1, line2)
_tle_ts = 0.0


def _load_tle_cache():
    """Load TLE from disk cache if fresh enough."""
    global _tle_lines, _tle_ts
    try:
        with open(_TLE_CACHE_FILE, "r") as f:
            obj = json.load(f)
        if time.time() - obj.get("ts", 0) < _TLE_REFRESH * 2:
            _tle_lines = (obj["name"], obj["line1"], obj["line2"])
            _tle_ts = obj["ts"]
            return True
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return False


def _fetch_tle():
    """Download current TLE from CelesTrak."""
    global _tle_lines, _tle_ts
    try:
        r = requests.get(_TLE_URL, timeout=(5, 15))
        r.raise_for_status()
        lines = r.text.strip().split("\n")
        if len(lines) >= 3:
            name = lines[0].strip()
            line1 = lines[1].strip()
            line2 = lines[2].strip()
            _tle_lines = (name, line1, line2)
            _tle_ts = time.time()
            os.makedirs(_CACHE_DIR, exist_ok=True)
            with open(_TLE_CACHE_FILE, "w") as f:
                json.dump({"ts": _tle_ts, "name": name,
                           "line1": line1, "line2": line2}, f)
            logger.info(f"[ISS] TLE updated: {name}")
            return True
    except Exception as e:
        logger.error(f"[ISS] TLE fetch failed: {e}")
    return False


def _ensure_tle():
    """Make sure we have a TLE, fetching if needed."""
    if _tle_lines and (time.time() - _tle_ts) < _TLE_REFRESH:
        return True
    if _load_tle_cache():
        if (time.time() - _tle_ts) < _TLE_REFRESH:
            return True
    return _fetch_tle()


# ── Pass computation ────────────────────────────────────────────────────────

def _compute_passes(lat, lon):
    """Compute ISS passes for the next _PREDICT_HOURS hours using ephem.

    Returns list of dicts matching the same schema the rest of the code expects:
        [{"rise": {"time": "...", "compass": "NW"},
          "set":  {"time": "...", "compass": "SE"},
          "culmination": {"elevation_deg": 45.2},
          "duration_sec": 340}, ...]
    """
    if ephem is None:
        logger.error("[ISS] ephem not installed — pip install ephem")
        return None
    if not _ensure_tle():
        return None

    name, line1, line2 = _tle_lines
    iss = ephem.readtle(name, line1, line2)

    obs = ephem.Observer()
    obs.lat = str(lat)
    obs.lon = str(lon)
    obs.elevation = 10
    obs.horizon = "0"   # compute from true horizon; filter by max_elev later
    obs.pressure = 0    # disable atmospheric refraction modeling (satellite work)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)  # ephem wants naive UTC
    obs.date = ephem.Date(now_utc)
    end_date = ephem.Date(now_utc + timedelta(hours=_PREDICT_HOURS))

    passes = []
    for _ in range(30):  # safety cap
        try:
            info = obs.next_pass(iss)
            rise_time, rise_az, max_time, max_alt, set_time, set_az = info
            if rise_time is None or rise_time > end_date:
                break
            # At polar latitudes ephem may return None for set/max fields
            if set_time is None or max_alt is None:
                obs.date = (rise_time or obs.date) + ephem.minute
                continue

            max_elev = math.degrees(float(max_alt))
            if max_elev < _MIN_ELEVATION:
                obs.date = set_time + ephem.minute
                continue

            rise_dt = ephem.Date(rise_time).datetime().replace(tzinfo=timezone.utc)
            set_dt = ephem.Date(set_time).datetime().replace(tzinfo=timezone.utc)
            duration = (set_dt - rise_dt).total_seconds()

            passes.append({
                "rise": {
                    "time": rise_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "compass": _az_to_compass(rise_az),
                },
                "set": {
                    "time": set_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "compass": _az_to_compass(set_az),
                },
                "culmination": {
                    "elevation_deg": round(max_elev, 1),
                },
                "duration_sec": int(duration),
            })
            obs.date = set_time + ephem.minute
        except Exception as e:
            logger.error(f"[ISS] Pass computation error: {e}")
            break

    # Cache to disk
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_CACHE_FILE, "w") as f:
        json.dump({"ts": time.time(), "passes": passes}, f)

    logger.debug(f"[ISS] Computed {len(passes)} passes (>={_MIN_ELEVATION}°) "
                 f"for next {_PREDICT_HOURS}h")
    return passes


# ── Caching & threading ─────────────────────────────────────────────────────

_cached_passes = None
_cached_ts = 0.0
_next_retry_after = 0.0
_consecutive_failures = 0
_lock = threading.Lock()


def _load_cache():
    """Load from disk cache if recent enough. Returns (passes, ts) or (None, 0)."""
    try:
        with open(_CACHE_FILE, "r") as f:
            obj = json.load(f)
        ts = obj.get("ts", 0)
        if time.time() - ts < _POLL_INTERVAL * 2:
            return obj.get("passes", []), ts
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None, 0


def _background_compute(lat, lon):
    """Compute ISS passes in a background thread. Releases _lock when done."""
    global _cached_passes, _cached_ts, _next_retry_after, _consecutive_failures
    try:
        passes = _compute_passes(lat, lon)
        now = time.time()
        if passes is not None:
            _cached_passes = passes
            _cached_ts = now
            _consecutive_failures = 0
            _next_retry_after = now + _POLL_INTERVAL
        else:
            _consecutive_failures += 1
            backoff = min(_POLL_INTERVAL * (2 ** _consecutive_failures), 14400)
            _next_retry_after = now + backoff
            logger.warning(f"[ISS] Backing off, next retry in {backoff // 60:.0f}m")
    finally:
        _lock.release()


def _refresh():
    """Return cached data immediately; kick off background compute if stale.

    Never blocks on the network or on computation.  If a compute is already
    in flight (or we're in a backoff window), callers just get the current
    cached passes.
    """
    global _cached_passes, _cached_ts, _next_retry_after

    import config as cfg
    location = cfg.LOCATION_HOME
    if location == [0.0, 0.0]:
        return []

    now = time.time()
    if _cached_passes is not None and now < _next_retry_after:
        return _cached_passes

    # Cold start: try disk cache (non-blocking)
    if _cached_passes is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_passes = disk
            _cached_ts = disk_ts
            _next_retry_after = disk_ts + _POLL_INTERVAL
            logger.info("[ISS] Loaded from disk cache")
            if now < _next_retry_after:
                return _cached_passes

    # Stale (and past backoff) — try to claim the compute slot without blocking.
    if now >= _next_retry_after and _lock.acquire(blocking=False):
        threading.Thread(
            target=_background_compute, args=(location[0], location[1]), daemon=True
        ).start()

    return _cached_passes or []


# ── Public API ──────────────────────────────────────────────────────────────

def _find_active_pass(passes):
    """Find a pass that is currently in progress. Returns (pass_dict, seconds_since_rise) or (None, 0)."""
    now = datetime.now(timezone.utc)
    for p in passes:
        try:
            rise_str = p.get("rise", {}).get("time", "")
            if not rise_str:
                continue
            rise_time = datetime.fromisoformat(rise_str.replace("Z", "+00:00"))
            seconds_since = (now - rise_time).total_seconds()
            duration = p.get("duration_sec", 0)
            if 0 <= seconds_since < duration:
                return p, seconds_since
        except (KeyError, ValueError, TypeError):
            continue
    return None, 0


def get_iss_pass_data():
    """Return detailed pass data when ISS is actively overhead, else None.

    Returns dict with keys:
        rise_time, set_time, rise_compass, set_compass, max_elevation,
        duration_sec, progress (0.0-1.0), time_remaining_sec, is_active
    """
    passes = _refresh()
    if not passes:
        return None

    active_pass, seconds_since = _find_active_pass(passes)
    if active_pass is None:
        return None

    duration = active_pass.get("duration_sec", 1) or 1
    progress = max(0.0, min(1.0, seconds_since / duration))
    time_remaining = max(0, duration - int(seconds_since))

    return {
        "rise_time": active_pass.get("rise", {}).get("time", ""),
        "set_time": active_pass.get("set", {}).get("time", ""),
        "rise_compass": active_pass.get("rise", {}).get("compass", "?"),
        "set_compass": active_pass.get("set", {}).get("compass", "?"),
        "max_elevation": active_pass.get("culmination", {}).get("elevation_deg", 0),
        "duration_sec": duration,
        "progress": progress,
        "time_remaining_sec": time_remaining,
        "is_active": True,
    }


def get_iss_groundtrack(past_minutes=30, future_minutes=65, step_minutes=2):
    """Return the ISS ground track from past to future as a list of [lat, lon] points.

    Returns {"points": [[lat, lon], ...], "current_index": int} where current_index
    is the index of "now" — everything before it is past, everything after is future.
    """
    if ephem is None:
        return None
    if not _tle_lines:
        _load_tle_cache()
    if not _tle_lines:
        return None
    try:
        name, line1, line2 = _tle_lines
        iss = ephem.readtle(name, line1, line2)
        now = ephem.now()
        current_index = int(past_minutes / step_minutes)
        total_steps = int((past_minutes + future_minutes) / step_minutes)
        points = []
        for i in range(total_steps + 1):
            t = now + (i - current_index) * step_minutes * ephem.minute
            iss.compute(t)
            points.append([
                round(math.degrees(float(iss.sublat)), 3),
                round(math.degrees(float(iss.sublong)), 3),
            ])
        return {"points": points, "current_index": current_index}
    except Exception as e:
        logger.debug(f"[ISS] groundtrack compute failed: {e}")
        return None


def get_iss_position():
    """Return the ISS current sub-satellite point, or None.

    Returns {"lat": <deg>, "lon": <deg>, "alt_km": <float>}.  Computed locally
    from the cached TLE — never triggers a network fetch, so it's safe to call
    from a web request handler at high frequency.
    """
    if ephem is None:
        return None
    # Use whatever TLE we already have (memory or disk); don't block on network.
    if not _tle_lines:
        _load_tle_cache()
    if not _tle_lines:
        return None
    try:
        name, line1, line2 = _tle_lines
        iss = ephem.readtle(name, line1, line2)
        iss.compute(ephem.now())
        return {
            "lat": round(math.degrees(float(iss.sublat)), 3),
            "lon": round(math.degrees(float(iss.sublong)), 3),
            "alt_km": round(float(iss.elevation) / 1000.0, 1),
        }
    except Exception as e:
        logger.debug(f"[ISS] position compute failed: {e}")
        return None


def get_iss_alert():
    """Return alert dict if an ISS pass is within 10 minutes, else None.

    Returns {"text": "ISS 3m", "color": "white"} or None.
    When the pass is actively overhead, returns None (takeover scene handles it).
    """
    import config as cfg
    if not getattr(cfg, "ISS_ALERTS_ENABLED", True):
        return None

    passes = _refresh()
    if not passes:
        return None

    now = datetime.now(timezone.utc)

    for p in passes:
        try:
            rise_str = p.get("rise", {}).get("time", "")
            if not rise_str:
                continue
            rise_time = datetime.fromisoformat(rise_str.replace("Z", "+00:00"))
            seconds_until = (rise_time - now).total_seconds()

            if seconds_until < 0:
                # Pass already started — takeover scene handles active passes
                duration = p.get("duration_sec", 0)
                if seconds_until > -duration:
                    return None  # suppress "ISS now!" — takeover scene is active
                continue

            if seconds_until <= _ALERT_WINDOW:
                mins = max(1, int(seconds_until / 60))
                return {"text": f"ISS {mins}m", "color": "white"}

        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"[ISS] Skipping pass with parse error: {e}")
            continue

    return None


# ─────────────────────────────────────────────────────────────────────────────
# SLAVE MODE — override public functions to poll master instead
# ─────────────────────────────────────────────────────────────────────────────
try:
    from config import MASTER_TRACKER as _MASTER_TRACKER
except (ImportError, ModuleNotFoundError, NameError):
    _MASTER_TRACKER = ""

if _MASTER_TRACKER:
    import requests as _requests
    from requests.exceptions import RequestException as _RequestException

    _slave_cache: dict = {}
    _slave_cache_ts: float = 0.0
    _SLAVE_TTL = 60  # seconds

    def _slave_url(path):
        host = _MASTER_TRACKER.rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}.local:8080"
        return f"{host}{path}"

    def _fetch_slave():
        global _slave_cache, _slave_cache_ts
        now = time.time()
        if _slave_cache and (now - _slave_cache_ts) < _SLAVE_TTL:
            return _slave_cache
        try:
            r = _requests.get(_slave_url("/iss/json"), timeout=10)
            r.raise_for_status()
            _slave_cache = r.json()
            _slave_cache_ts = now
        except _RequestException as e:
            logger.error(f"[Slave/ISS] Cannot reach master: {e}")
        return _slave_cache

    def get_iss_alert():
        return _fetch_slave().get("alert")

    def get_iss_pass_data():
        return _fetch_slave().get("pass_data")

    def get_iss_position():
        return _fetch_slave().get("position")

    logger.info(f"[ISS] Slave mode — polling master at {_slave_url('')}")
