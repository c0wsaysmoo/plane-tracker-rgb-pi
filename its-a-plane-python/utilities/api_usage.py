"""API usage tracking — per-source, per-day call counts.

Thread-safe, auto-prunes data older than 90 days.
Persists to DATA_DIR/api_usage.json alongside other plane-tracker data files.
Batches disk writes (at most every 60 seconds) to avoid SD card thrash.
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from time import time

logger = logging.getLogger(__name__)

# Same DATA_DIR convention as overhead.py / app.py
DATA_DIR = os.environ.get("PLANE_TRACKER_DATA_DIR", "/var/lib/plane-tracker")
USAGE_FILE = os.path.join(DATA_DIR, "api_usage.json")
_SAVE_INTERVAL = 60  # seconds between disk writes

# Known API sources
SOURCES = [
    "fr24_grpc",
    "airlabs",
    "noaa_tides",
    "noaa_water",
    "tomorrow_io",
    "owm",
    "nws",
    "iss_api",
    "faa_status",
    "nominatim",
    "nps",
    "adsbdb",
    "flightstats",
]

_lock = threading.Lock()
_data: dict = {}  # {"YYYY-MM-DD": {"source": count, ...}, ...}
_loaded = False
_dirty = False
_last_save_ts = 0.0


def _load():
    """Load usage data from disk (once)."""
    global _data, _loaded
    if _loaded:
        return
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            _data = json.load(f)
        if not isinstance(_data, dict):
            _data = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        _data = {}
    _loaded = True


def _save(force=False):
    """Write usage data to disk if dirty and interval elapsed, pruning >90 days."""
    global _dirty, _last_save_ts
    if not _dirty:
        return
    now = time()
    if not force and (now - _last_save_ts) < _SAVE_INTERVAL:
        return

    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    pruned = {k: v for k, v in _data.items() if k >= cutoff}
    _data.clear()
    _data.update(pruned)

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = USAGE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f)
        os.replace(tmp, USAGE_FILE)
        try:
            os.chmod(USAGE_FILE, 0o666)
        except OSError:
            pass
        _dirty = False
        _last_save_ts = now
    except OSError as e:
        logger.error("api_usage: failed to save: %s", e)


def log_call(source: str):
    """Increment today's count for the given API source."""
    global _dirty
    today = datetime.now().strftime("%Y-%m-%d")
    with _lock:
        _load()
        day = _data.setdefault(today, {})
        day[source] = day.get(source, 0) + 1
        _dirty = True
        _save()


def flush():
    """Force a disk write if there are pending changes. Call at shutdown."""
    with _lock:
        _save(force=True)


def get_usage() -> dict:
    """Return full usage data: {"YYYY-MM-DD": {"source": count, ...}, ...}."""
    with _lock:
        _load()
        return dict(_data)


def get_summary() -> dict:
    """Return current month totals + daily breakdown for the current month."""
    with _lock:
        _load()
        now = datetime.now()
        month_prefix = now.strftime("%Y-%m")
        month_totals = {}
        daily = {}
        for date_str, sources in sorted(_data.items()):
            if not date_str.startswith(month_prefix):
                continue
            daily[date_str] = dict(sources)
            for src, count in sources.items():
                month_totals[src] = month_totals.get(src, 0) + count
        return {
            "month": month_prefix,
            "totals": month_totals,
            "daily": daily,
        }
