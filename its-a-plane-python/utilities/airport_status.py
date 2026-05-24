"""
airport_status.py — FAA NAS airport delay/ground stop alerts.

Free, no API key. Polls every 5 minutes. Only major airports appear
in the FAA feed (JFK, LGA, EWR, etc. — not GA fields like TEB/MMU).
API returns XML which is parsed into alert dicts.

Usage:
    from utilities.airport_status import get_airport_alerts
    alerts = get_airport_alerts()
    # [{"text": "JFK Delay", "color": "orange"}, {"text": "EWR GStop", "color": "red"}]
"""

import json
import logging
import os
import time
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://nasstatus.faa.gov/api/airport-status-information"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "airport_status.json")
_POLL_INTERVAL = 300  # 5 minutes

# In-memory cache
_cached_data = None  # list of {"type": "ground_stop"|"ground_delay"|"closure", "arpt": "JFK", ...}
_cached_ts = 0.0


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
            for prog in delay_type.findall(".//Program"):
                arpt = (prog.findtext("ARPT") or "").strip().upper()
                if arpt:
                    results.append({"type": "ground_stop", "arpt": arpt})

        elif "Ground Delay" in name:
            for gd in delay_type.findall(".//Ground_Delay"):
                arpt = (gd.findtext("ARPT") or "").strip().upper()
                if arpt:
                    results.append({"type": "ground_delay", "arpt": arpt})

        elif "Arrival" in name or "Departure" in name:
            for ad in delay_type.findall(".//Delay"):
                arpt = (ad.findtext("ARPT") or "").strip().upper()
                if arpt:
                    results.append({"type": "arr_dep_delay", "arpt": arpt})

        elif "Closure" in name:
            for cl in delay_type.findall(".//Airport"):
                arpt = (cl.findtext("ARPT") or "").strip().upper()
                if arpt:
                    results.append({"type": "closure", "arpt": arpt})

    return results


def _fetch():
    """Fetch current airport status from FAA NAS (XML endpoint)."""
    try:
        r = requests.get(_API_URL, timeout=(5, 15))
        r.raise_for_status()
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


def _refresh():
    """Refresh data if poll interval has elapsed."""
    global _cached_data, _cached_ts

    now = time.time()
    if _cached_data is not None and (now - _cached_ts) < _POLL_INTERVAL:
        return _cached_data

    if _cached_data is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_data = disk
            _cached_ts = disk_ts
            logger.info("[AirportStatus] Loaded from disk cache")

    if (now - _cached_ts) >= _POLL_INTERVAL:
        data = _fetch()
        if data is not None:
            _cached_data = data
            _cached_ts = now

    return _cached_data


def get_airport_alerts():
    """Return list of alert dicts for configured airports.

    Each dict: {"text": "JFK Delay", "color": "orange"}
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

        dtype = item.get("type", "")
        if dtype == "ground_stop":
            alerts.append({"text": f"{arpt} GStop", "color": "red"})
        elif dtype == "closure":
            alerts.append({"text": f"{arpt} Closd", "color": "red"})
        elif dtype in ("ground_delay", "arr_dep_delay"):
            alerts.append({"text": f"{arpt} Delay", "color": "orange"})

    return alerts
