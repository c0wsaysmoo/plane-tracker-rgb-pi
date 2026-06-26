"""
iss.py — ISS overhead pass predictions.

Uses the free Pollux Labs ISS API (no key required).
Polls every 30 minutes for upcoming visible passes.
Shows alert when a visible pass is within 10 minutes.

Usage:
    from utilities.iss import get_iss_alert
    alert = get_iss_alert()
    # {"text": "ISS 3m", "color": "white"}  or  None
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://iss-api.polluxlabs.io/iss-pass"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "iss.json")
_POLL_INTERVAL = 1800  # 30 minutes
_ALERT_WINDOW = 600    # show alert 10 minutes before pass

# In-memory cache
_cached_passes = None
_cached_ts = 0.0
_next_retry_after = 0.0  # absolute timestamp; 0 = retry immediately
_consecutive_failures = 0
_lock = threading.Lock()


def _fetch(lat, lon):
    """Fetch upcoming visible ISS passes."""
    try:
        r = requests.get(_API_URL, params={
            "lat": lat,
            "lon": lon,
            "visible_only": "true",
        }, timeout=(5, 15))
        r.raise_for_status()
        data = r.json()
        passes = data.get("passes", [])

        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"ts": time.time(), "passes": passes}, f)

        visible_count = len(passes)
        logger.debug(f"[ISS] Fetched {visible_count} visible passes")
        return passes

    except Exception as e:
        logger.error(f"[ISS] Fetch failed: {e}")
        return None


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


def _background_refresh(lat, lon):
    """Run the blocking fetch off the caller's thread. Releases _lock when done."""
    global _cached_passes, _cached_ts, _next_retry_after, _consecutive_failures
    try:
        now = time.time()
        passes = _fetch(lat, lon)
        if passes is not None:
            _cached_passes = passes
            _cached_ts = now
            _consecutive_failures = 0
            _next_retry_after = now + _POLL_INTERVAL
        else:
            _consecutive_failures += 1
            # Exponential backoff: 60m, 120m, max 4h
            backoff = min(_POLL_INTERVAL * (2 ** _consecutive_failures), 14400)
            _next_retry_after = now + backoff
            logger.warning(f"[ISS] Backing off, next retry in {backoff // 60:.0f}m")
    finally:
        _lock.release()


def _refresh():
    """Return cached passes immediately; kick off a background fetch if stale.

    Never blocks on the network. If a refresh is already in flight (or we're
    in a backoff window), callers just get the current cached passes — this
    is what prevents thread pileup on the Flask server when the ISS API or
    DNS is degraded.
    """
    global _cached_passes, _cached_ts, _next_retry_after

    import config as cfg
    location = cfg.LOCATION_HOME
    if location == [0.0, 0.0]:
        return []

    now = time.time()
    if _cached_passes is not None and now < _next_retry_after:
        return _cached_passes

    if _cached_passes is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_passes = disk
            _cached_ts = disk_ts
            _next_retry_after = disk_ts + _POLL_INTERVAL
            logger.debug("[ISS] Loaded from disk cache")
            if now < _next_retry_after:
                return _cached_passes

    # Stale (and past backoff) — try to claim the refresh slot without blocking.
    if now >= _next_retry_after and _lock.acquire(blocking=False):
        threading.Thread(
            target=_background_refresh, args=(location[0], location[1]), daemon=True
        ).start()
    # else: a refresh is already in flight elsewhere, or we're still in a
    # backoff window — fall through and return whatever we've got.

    return _cached_passes or []


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


def get_iss_alert():
    """Return alert dict if a visible ISS pass is within 10 minutes, else None.

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

    logger.info(f"[ISS] Slave mode — polling master at {_slave_url('')}")
