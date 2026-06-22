"""
nws_alerts.py — Active weather alerts from the NWS API.

Free, no API key required. Polls every 15 minutes by lat/lon.
Returns abbreviated alert texts with severity-based color names
for display on a 64x32 LED matrix.

Usage:
    from utilities.nws_alerts import get_active_alerts
    alerts = get_active_alerts()
    # [{"text": "HiSurf", "color": "cyan"}, {"text": "RipCurr", "color": "cyan"}]
    # or []
"""

import json
import logging
import os
import threading
import time

import requests

try:
    from utilities.api_usage import log_call as _log_api
except ImportError:
    _log_api = lambda source: None

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.weather.gov/alerts/active"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "nws_alerts.json")
_POLL_INTERVAL = 900  # 15 minutes
_USER_AGENT = "PlaneTracker/1.0 (flight tracker LED display)"

# Abbreviation map: NWS event name → (short text, color name)
# Color names are resolved by the scene (clock.py) to actual RGB values.
# Short text should be ≤9 chars to avoid alert overflow (>9 clears date zone).
# Based on official NWS hazard map (weather.gov/help-map, updated March 2025).
_ALERT_MAP = {
    # ── Life-threatening warnings — red ──
    "Tornado Warning":                ("Tornado!", "red"),
    "Severe Thunderstorm Warning":    ("SvrStorm", "red"),
    "Hurricane Warning":              ("Hurricne", "red"),
    "Hurricane Force Wind Warning":   ("HrcnWind", "red"),
    "Flash Flood Warning":            ("FlFlood",  "red"),
    "Tsunami Warning":                ("Tsunami!", "red"),
    "Extreme Wind Warning":           ("ExtWind",  "red"),
    "Storm Surge Warning":            ("StrmSrge", "red"),
    "Snow Squall Warning":            ("SnSquall", "red"),
    "Earthquake Warning":             ("Quake!",   "red"),
    "Volcano Warning":                ("Volcano!", "red"),
    # Civil emergencies — red
    "Evacuation Immediate":           ("EVACUATE", "red"),
    "Shelter In Place Warning":       ("SHELTER",  "red"),
    "Civil Danger Warning":           ("CivilWrn", "red"),
    "Civil Emergency Message":        ("CivilEmg", "red"),
    "Nuclear Power Plant Warning":    ("Nuclear!", "red"),
    "Radiological Hazard Warning":    ("RadHaz!",  "red"),
    "Hazardous Materials Warning":    ("HazMat!",  "red"),
    "Law Enforcement Warning":        ("LawEnfrc", "red"),
    "Local Area Emergency":           ("LocalEmg", "red"),
    "911 Telephone Outage":           ("911 Out",  "red"),
    # ── Significant warnings — orange ──
    "Flood Warning":                  ("Flood",    "orange"),
    "High Wind Warning":              ("HiWind",   "orange"),
    "Blizzard Warning":               ("Blizzard", "orange"),
    "Ice Storm Warning":              ("IceStorm", "orange"),
    "Winter Storm Warning":           ("WntStorm", "orange"),
    "Excessive Heat Warning":         ("ExtHeat",  "orange"),
    "Extreme Heat Warning":           ("ExtHeat",  "orange"),
    "Heat Advisory":                  ("Heat Adv", "orange"),
    "Tropical Storm Warning":         ("TropStrm", "orange"),
    "Fire Weather Watch":             ("FireWthr", "orange"),
    "Red Flag Warning":               ("RedFlag",  "orange"),
    "Wind Chill Warning":             ("WndChill", "orange"),
    "Extreme Cold Warning":           ("ExtCold",  "orange"),
    "Coastal Flood Warning":          ("CstFldW",  "orange"),
    "Lakeshore Flood Warning":        ("LkFlood",  "orange"),
    "Lake Effect Snow Warning":       ("LkSnow",   "orange"),
    "Fire Warning":                   ("Fire!",    "orange"),
    "Extreme Fire Danger":            ("FireDngr", "orange"),
    "Dust Storm Warning":             ("DustStrm", "orange"),
    "Typhoon Warning":                ("Typhoon!", "orange"),
    "Avalanche Warning":              ("Avalanch", "orange"),
    "Ashfall Warning":                ("Ashfall",  "orange"),
    "Heavy Freezing Spray Warning":   ("FrzSpray", "orange"),
    # Marine — orange
    "Special Marine Warning":         ("MarineWn", "orange"),
    "Gale Warning":                   ("Gale!",    "orange"),
    "Storm Warning":                  ("Storm!",   "orange"),
    "Hazardous Seas Warning":         ("HazSeas",  "orange"),
    "High Surf Warning":              ("HiSurf!",  "orange"),
    # ── Advisories and statements — cyan ──
    "High Surf Advisory":             ("HiSurf",   "cyan"),
    "Rip Current Statement":          ("RipCurr",  "cyan"),
    "Wind Advisory":                  ("Wind Adv", "cyan"),
    "Wind Chill Advisory":            ("WndChill", "cyan"),
    "Cold Weather Advisory":          ("ColdAdv",  "cyan"),
    "Freeze Warning":                 ("Freeze",   "cyan"),
    "Frost Advisory":                 ("Frost",    "cyan"),
    "Winter Weather Advisory":        ("WntWthr",  "cyan"),
    "Coastal Flood Advisory":         ("CstFlood", "cyan"),
    "Dense Fog Advisory":             ("DensFog",  "cyan"),
    "Freezing Fog Advisory":          ("FrzFog",   "cyan"),
    "Dense Smoke Advisory":           ("Smoke",    "cyan"),
    "Freezing Rain Advisory":         ("FrzRain",  "cyan"),
    "Freezing Spray Advisory":        ("FrzSpray", "cyan"),
    "Beach Hazards Statement":        ("BeachHaz", "cyan"),
    "Flood Advisory":                 ("FloodAdv", "cyan"),
    "Lakeshore Flood Advisory":       ("LkFldAdv", "cyan"),
    "Lake Wind Advisory":             ("LkWind",   "cyan"),
    "Brisk Wind Advisory":            ("BrskWind", "cyan"),
    "Dust Advisory":                  ("Dust",     "cyan"),
    "Blowing Dust Advisory":          ("BlwDust",  "cyan"),
    "Blowing Dust Warning":           ("BlwDust!", "orange"),
    "Avalanche Advisory":             ("AvalAdv",  "cyan"),
    "Ashfall Advisory":               ("AshAdv",   "cyan"),
    "Small Craft Advisory":           ("SmlCraft", "cyan"),
    "Low Water Advisory":             ("LowWater", "cyan"),
    "Tsunami Advisory":               ("TsuAdv",   "cyan"),
    "Marine Weather Statement":       ("MarineWx", "cyan"),
    # ── Air quality — yellow ──
    "Air Quality Alert":              ("AirQlty",  "yellow"),
    "Air Stagnation Advisory":        ("AirStag",  "yellow"),
    # ── Statements / misc — grey ──
    "Special Weather Statement":      ("SpclWthr", "grey"),
    "Severe Weather Statement":       ("SvrStmt",  "grey"),
    "Flash Flood Statement":          ("FFldStmt", "grey"),
    "Flood Statement":                ("FldStmt",  "grey"),
    "Coastal Flood Statement":        ("CstFStmt", "grey"),
    "Lakeshore Flood Statement":      ("LkFStmt",  "grey"),
    "Tropical Cyclone Local Statement": ("TropStmt", "grey"),
    # ── Watches — grey (lower urgency; suppressed when matching warning active) ──
    "Tornado Watch":                  ("TornWtch", "grey"),
    "Severe Thunderstorm Watch":      ("StrmWtch", "grey"),
    "Flash Flood Watch":              ("FldWatch", "grey"),
    "Flood Watch":                    ("FldWatch", "grey"),
    "Winter Storm Watch":             ("WntWatch", "grey"),
    "Hurricane Watch":                ("HrcnWtch", "grey"),
    "Hurricane Force Wind Watch":     ("HrcnWWch", "grey"),
    "Tropical Storm Watch":           ("TropWtch", "grey"),
    "Storm Surge Watch":              ("SrgeWtch", "grey"),
    "Typhoon Watch":                  ("TyphWtch", "grey"),
    "Excessive Heat Watch":           ("HeatWtch", "grey"),
    "Extreme Heat Watch":             ("HeatWtch", "grey"),
    "Extreme Cold Watch":             ("ColdWtch", "grey"),
    "Wind Chill Watch":               ("WChlWtch", "grey"),
    "High Wind Watch":                ("HWndWtch", "grey"),
    "Freeze Watch":                   ("FrezWtch", "grey"),
    "Avalanche Watch":                ("AvalWtch", "grey"),
    "Gale Watch":                     ("GaleWtch", "grey"),
    "Storm Watch":                    ("StmWatch", "grey"),
    "Hazardous Seas Watch":           ("HzSeaWch", "grey"),
    "Lakeshore Flood Watch":          ("LkFWatch", "grey"),
    "Coastal Flood Watch":            ("CstFWtch", "grey"),
    "Heavy Freezing Spray Watch":     ("FrzSpWch", "grey"),
}

# Force certain high-impact events to red regardless of their default color.
# (e.g. Extreme Heat has severity="Severe" in API but deserves red on display.)
_EVENT_COLOUR_OVERRIDE = {
    "Extreme Heat Warning":           "red",
    "Extreme Heat Watch":             "red",
    "Excessive Heat Warning":         "red",
    "Tornado Watch":                  "red",
    "Blizzard Warning":               "red",
    "Ice Storm Warning":              "red",
    "Tsunami Warning":                "red",
    "Earthquake Warning":             "red",
    "Nuclear Power Plant Warning":    "red",
    "Radiological Hazard Warning":    "red",
    "Hazardous Materials Warning":    "red",
    "Evacuation Immediate":           "red",
    "Shelter In Place Warning":       "red",
}

# ── Watch suppression ──
# When a Warning is active, drop the matching Watch (e.g. Tornado Warning
# active → suppress Tornado Watch). Matching is done on the NWS event name
# prefix: "Tornado Warning" and "Tornado Watch" share "Tornado".

def _suppress_watches(alerts, features):
    """Drop Watch-level alerts when a Warning for the same hazard is active."""
    # Build set of hazard prefixes that have an active warning
    warning_prefixes = set()
    for feature in features:
        event = feature.get("properties", {}).get("event", "")
        if event.endswith(" Warning"):
            warning_prefixes.add(event.replace(" Warning", ""))

    if not warning_prefixes:
        return alerts

    filtered = []
    for alert, feature in zip(alerts, features):
        event = feature.get("properties", {}).get("event", "")
        if event.endswith(" Watch"):
            prefix = event.replace(" Watch", "")
            if prefix in warning_prefixes:
                logger.info(f"[NWS] Suppressing '{event}' — warning already active")
                continue
        filtered.append(alert)
    return filtered


# In-memory cache
_cached_alerts = None  # list of alert dicts from API
_cached_ts = 0.0
_refresh_lock = threading.Lock()
_refresh_pending = False


def _fetch(lat, lon):
    """Fetch active alerts from NWS for a location."""
    try:
        r = requests.get(_BASE_URL, params={
            "point": f"{lat},{lon}",
            "status": "actual",
            "message_type": "alert,update",
        }, headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/geo+json",
        }, timeout=(5, 15))
        r.raise_for_status()
        _log_api("nws")
        data = r.json()
        features = data.get("features", [])

        # Cache to disk
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"ts": time.time(), "features": features}, f)

        logger.info(f"[NWS] Fetched {len(features)} active alerts")
        return features

    except Exception as e:
        logger.error(f"[NWS] Fetch failed: {e}")
        return None


def _load_cache():
    """Load from disk cache if recent enough. Returns (features, ts) or (None, 0)."""
    try:
        with open(_CACHE_FILE, "r") as f:
            obj = json.load(f)
        ts = obj.get("ts", 0)
        if time.time() - ts < _POLL_INTERVAL * 2:
            return obj.get("features", []), ts
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None, 0


def _background_fetch(lat, lon):
    """Fetch NWS alerts in a background thread so the display never blocks."""
    global _cached_alerts, _cached_ts, _refresh_pending
    with _refresh_lock:
        try:
            features = _fetch(lat, lon)
            if features is not None:
                _cached_alerts = features
                _cached_ts = time.time()
        finally:
            _refresh_pending = False


def _refresh():
    """Return cached data immediately; kick off background fetch if stale."""
    global _cached_alerts, _cached_ts, _refresh_pending

    import config as cfg
    location = cfg.LOCATION_HOME
    if location == [0.0, 0.0]:
        return []

    now = time.time()
    if _cached_alerts is not None and (now - _cached_ts) < _POLL_INTERVAL:
        return _cached_alerts

    # Cold start: try disk cache (non-blocking)
    if _cached_alerts is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_alerts = disk
            _cached_ts = disk_ts
            logger.info("[NWS] Loaded from disk cache")

    # Schedule non-blocking background fetch if interval elapsed
    if (now - _cached_ts) >= _POLL_INTERVAL and not _refresh_pending:
        _refresh_pending = True
        threading.Thread(target=_background_fetch, args=(location[0], location[1]), daemon=True).start()

    return _cached_alerts or []


def get_active_alerts():
    """
    Return list of active alert dicts for display.

    Each dict: {"text": "HiSurf", "color": "cyan"}
    Returns [] if no alerts or no location configured.
    Deduplicates by event name (same event won't appear twice).
    Suppresses Watch-level alerts when a matching Warning is active.
    """
    features = _refresh()
    if not features:
        return []

    seen = set()
    alerts = []
    matched_features = []
    for feature in features:
        props = feature.get("properties", {})
        event = props.get("event", "")
        if not event or event in seen:
            continue
        seen.add(event)

        mapping = _ALERT_MAP.get(event)
        if mapping:
            text, color = mapping
        else:
            # Unknown alert type — show first 9 chars
            text = event[:9]
            color = "grey"

        # Apply color override for high-impact events
        color = _EVENT_COLOUR_OVERRIDE.get(event, color)

        alerts.append({"text": text, "color": color})
        matched_features.append(feature)

    # Suppress Watch alerts when matching Warning is active
    alerts = _suppress_watches(alerts, matched_features)

    return alerts
