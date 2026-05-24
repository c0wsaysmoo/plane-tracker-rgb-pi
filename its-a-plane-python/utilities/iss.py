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
        logger.info(f"[ISS] Fetched {visible_count} visible passes")
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


def _refresh():
    """Refresh data if poll interval has elapsed."""
    global _cached_passes, _cached_ts

    import config as cfg
    location = cfg.LOCATION_HOME
    if location == [0.0, 0.0]:
        return []

    now = time.time()
    if _cached_passes is not None and (now - _cached_ts) < _POLL_INTERVAL:
        return _cached_passes

    if _cached_passes is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_passes = disk
            _cached_ts = disk_ts
            logger.info("[ISS] Loaded from disk cache")

    if (now - _cached_ts) >= _POLL_INTERVAL:
        passes = _fetch(location[0], location[1])
        if passes is not None:
            _cached_passes = passes
            _cached_ts = now

    return _cached_passes or []


def get_iss_alert():
    """Return alert dict if a visible ISS pass is within 10 minutes, else None.

    Returns {"text": "ISS 3m", "color": "white"} or None.
    """
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
                # Pass already started — show if still in progress
                duration = p.get("duration_sec", 0)
                if seconds_until > -duration:
                    return {"text": "ISS now!", "color": "white"}
                continue

            if seconds_until <= _ALERT_WINDOW:
                mins = max(1, int(seconds_until / 60))
                return {"text": f"ISS {mins}m", "color": "white"}

        except (KeyError, ValueError, TypeError) as e:
            logger.debug(f"[ISS] Skipping pass with parse error: {e}")
            continue

    return None
