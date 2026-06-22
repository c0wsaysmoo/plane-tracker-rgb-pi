"""
airport_status.py — FAA NAS airport delay/ground stop alerts.

Free, no API key. Polls every 5 minutes. Only major airports appear
in the FAA feed (JFK, LGA, EWR, etc. — not GA fields like TEB/MMU).
API returns XML which is parsed into alert dicts.

Usage:
    from utilities.airport_status import get_airport_alerts
    alerts = get_airport_alerts()
    # [{"text": "JFK Dep", "color": "orange"}, {"text": "EWR GStop", "color": "red"}]
"""

import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

try:
    from utilities.api_usage import log_call as _log_api
except ImportError:
    _log_api = lambda source: None

logger = logging.getLogger(__name__)

_API_URL = "https://nasstatus.faa.gov/api/airport-status-information"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "airport_status.json")
_POLL_INTERVAL = 300  # 5 minutes

# In-memory cache
_cached_data = None
_cached_ts = 0.0
_refresh_lock = threading.Lock()
_refresh_pending = False


def _parse_minutes(text):
    """Extract total minutes from strings like '1 hour and 46 minutes' or '31 minutes'."""
    if not text:
        return 0
    text = text.lower().strip()
    total = 0
    hours = re.search(r'(\d+)\s*hour', text)
    mins  = re.search(r'(\d+)\s*min', text)
    if hours:
        total += int(hours.group(1)) * 60
    if mins:
        total += int(mins.group(1))
    if total == 0:
        bare = re.fullmatch(r'(\d+)', text.strip())
        if bare:
            total = int(bare.group(1))
    return total


def _delay_color(minutes):
    """Return color string based on delay severity, or None if below threshold."""
    if minutes >= 120:
        return "red"
    elif minutes >= 90:
        return "orange"
    elif minutes >= 45:
        return "yellow"
    else:
        return None  # too minor to show


def _parse_xml(xml_text):
    """Parse FAA XML response into a list of delay/stop/closure dicts."""
    results = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error(f"[AirportStatus] XML parse error: {e}")
        return results

    for delay_type in root.findall("Delay_type"):
        name = (delay_type.findtext("Name") or "").strip()

        if "Ground Stop" in name:
            for prog in delay_type.findall(".//Ground_Stop_List/Program"):
                arpt = (prog.findtext("ARPT") or "").strip().upper()
                if arpt:
                    results.append({"type": "ground_stop", "arpt": arpt, "minutes": 0})

        elif "Ground Delay" in name:
            for gd in delay_type.findall(".//Ground_Delay_List/Ground_Delay"):
                arpt = (gd.findtext("ARPT") or "").strip().upper()
                if arpt:
                    avg_text = gd.findtext("Avg") or ""
                    results.append({
                        "type": "ground_delay",
                        "arpt": arpt,
                        "minutes": _parse_minutes(avg_text),
                    })

        elif "Arrival" in name or "Departure" in name:
            for ad in delay_type.findall(".//Arrival_Departure_Delay_List/Delay"):
                arpt = (ad.findtext("ARPT") or "").strip().upper()
                if not arpt:
                    continue
                arr_mins = dep_mins = 0
                has_arr = has_dep = False
                for ad_el in ad.findall("Arrival_Departure"):
                    dtype = (ad_el.get("Type") or "").lower()
                    mins = _parse_minutes(ad_el.findtext("Min") or "")
                    if "arrival" in dtype:
                        has_arr = True
                        arr_mins = max(arr_mins, mins)
                    elif "departure" in dtype:
                        has_dep = True
                        dep_mins = max(dep_mins, mins)

                if has_arr and has_dep:
                    results.append({"type": "arr_dep_delay", "arpt": arpt,
                                    "minutes": max(arr_mins, dep_mins)})
                elif has_dep:
                    results.append({"type": "dep_delay", "arpt": arpt, "minutes": dep_mins})
                elif has_arr:
                    results.append({"type": "arr_delay", "arpt": arpt, "minutes": arr_mins})
                else:
                    results.append({"type": "arr_dep_delay", "arpt": arpt, "minutes": 0})

        elif "Closure" in name:
            for cl in delay_type.findall(".//Airport_Closure_List/Airport"):
                arpt = (cl.findtext("ARPT") or "").strip().upper()
                reason = (cl.findtext("Reason") or "").upper()
                if not arpt:
                    continue
                if "GA" in reason and ("TRANSIENT" in reason or "NON SKED" in reason or "NON-SKED" in reason):
                    results.append({"type": "closure_ga", "arpt": arpt, "minutes": 0})
                else:
                    results.append({"type": "closure", "arpt": arpt, "minutes": 0})

    return results


def _fetch():
    """Fetch current airport status from FAA NAS (XML endpoint)."""
    try:
        r = requests.get(_API_URL, timeout=(5, 15))
        r.raise_for_status()
        _log_api("faa_status")
        data = _parse_xml(r.text)

        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"ts": time.time(), "data": data}, f)

        airports = {d["arpt"] for d in data}
        logger.info(f"[AirportStatus] Fetched FAA status: {len(data)} items ({', '.join(sorted(airports)) or 'none'})")
        return data

    except Exception as e:
        logger.error(f"[AirportStatus] Fetch failed: {e}")
        return None


def _load_cache():
    """Load from disk cache if recent enough. Returns (data, ts) or (None, 0)."""
    try:
        with open(_CACHE_FILE, "r") as f:
            obj = json.load(f)
        ts = obj.get("ts", 0)
        if time.time() - ts < _POLL_INTERVAL * 2:
            return obj.get("data", []), ts
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None, 0


def _background_fetch():
    """Fetch FAA status in a background thread so the display never blocks."""
    global _cached_data, _cached_ts, _refresh_pending
    with _refresh_lock:
        try:
            data = _fetch()
            if data is not None:
                _cached_data = data
                _cached_ts = time.time()
        finally:
            _refresh_pending = False


def _refresh():
    """Return cached data immediately; kick off background fetch if stale."""
    global _cached_data, _cached_ts, _refresh_pending

    now = time.time()
    if _cached_data is not None and (now - _cached_ts) < _POLL_INTERVAL:
        return _cached_data

    # Cold start: try disk cache (non-blocking)
    if _cached_data is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_data = disk
            _cached_ts = disk_ts
            logger.info("[AirportStatus] Loaded from disk cache")

    # Schedule non-blocking background fetch if interval elapsed
    if (now - _cached_ts) >= _POLL_INTERVAL and not _refresh_pending:
        _refresh_pending = True
        threading.Thread(target=_background_fetch, daemon=True).start()

    return _cached_data


def get_airport_alerts():
    """Return list of alert dicts for configured airports.

    Each dict: {"text": "JFK Dep 90m", "color": "yellow"|"orange"|"red"}
    Returns [] if no delays or no airports configured.
    """
    import config as cfg
    airports_str = getattr(cfg, "AIRPORT_STATUS_LIST", "")
    if not airports_str:
        return []

    watch_set = {a.strip().upper() for a in airports_str.split(",") if a.strip()}
    if not watch_set:
        return []

    data = _refresh()
    if not data:
        return []

    seen = set()  # first-match wins; FAA XML lists most-severe types first
    alerts = []
    for item in data:
        arpt = item.get("arpt", "")
        if arpt not in watch_set or arpt in seen:
            continue
        seen.add(arpt)

        dtype   = item.get("type", "")
        minutes = item.get("minutes", 0)

        if dtype == "ground_stop":
            alerts.append({"text": f"{arpt} Grnd Stop", "color": "red"})
        elif dtype == "closure":
            alerts.append({"text": f"{arpt} Closed", "color": "red"})
        elif dtype == "closure_ga":
            alerts.append({"text": f"{arpt} GA Closed", "color": "grey"})
        elif dtype == "ground_delay":
            color = _delay_color(minutes)
            if color:
                alerts.append({"text": f"{arpt} GDly {minutes}m", "color": color})
        elif dtype == "dep_delay":
            color = _delay_color(minutes)
            if color:
                alerts.append({"text": f"{arpt} Dep {minutes}m", "color": color})
        elif dtype == "arr_delay":
            color = _delay_color(minutes)
            if color:
                alerts.append({"text": f"{arpt} Arr {minutes}m", "color": color})
        elif dtype == "arr_dep_delay":
            color = _delay_color(minutes)
            if color:
                alerts.append({"text": f"{arpt} Dly {minutes}m", "color": color})

    return alerts
