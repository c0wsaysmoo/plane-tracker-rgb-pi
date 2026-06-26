"""
nws.py — NOAA National Weather Service active weather alerts.

Free, no API key required. US locations only.
Polls https://api.weather.gov/alerts/active?point=LAT,LON
Only Extreme and Severe alerts are surfaced.

Usage:
    from utilities.nws import get_nws_alerts
    alerts = get_nws_alerts()
    # [{"text": "TORN WRN", "color": "red"}, ...]
"""

import json
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.weather.gov/alerts/active"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "nws_alerts.json")
_POLL_INTERVAL = 600  # 10 minutes

_SEVERITY_COLOUR = {
    "Extreme": "red",
    "Severe":  "orange",
}

# NWS event name → ≤10 char display string
# Based on official NWS hazard map list (weather.gov/help-map, updated March 10, 2025)
_EVENT_ABBR = {
    # Tornado
    "Tornado Warning":                    "TORN WRN",
    "Tornado Watch":                      "TORN WCH",
    # Thunderstorm
    "Severe Thunderstorm Warning":        "TSTM WRN",
    "Severe Thunderstorm Watch":          "TSTM WCH",
    "Severe Weather Statement":           "TSTM STMT",
    # Flash Flood
    "Flash Flood Warning":                "FFLOOD WRN",
    "Flash Flood Watch":                  "FFLOOD WCH",
    "Flash Flood Statement":              "FFLOOD STM",
    # Flood
    "Flood Warning":                      "FLOOD WRN",
    "Flood Watch":                        "FLOOD WCH",
    "Flood Statement":                    "FLOOD STM",
    "Flood Advisory":                     "FLOOD ADV",
    "Coastal Flood Warning":              "CSTAL FLD",
    "Coastal Flood Watch":                "CSTAL WCH",
    "Coastal Flood Advisory":             "CSTAL ADV",
    "Coastal Flood Statement":            "CSTAL STM",
    "Lakeshore Flood Warning":            "LAKE FLD",
    "Lakeshore Flood Watch":              "LAKE WCH",
    "Lakeshore Flood Advisory":           "LAKE ADV",
    "Lakeshore Flood Statement":          "LAKE STM",
    # Tropical
    "Hurricane Warning":                  "HURR WRN",
    "Hurricane Watch":                    "HURR WCH",
    "Hurricane Force Wind Warning":       "HURR WIND",
    "Hurricane Force Wind Watch":         "HURR WWCH",
    "Tropical Storm Warning":             "TROP WRN",
    "Tropical Storm Watch":               "TROP WCH",
    "Tropical Cyclone Local Statement":   "TROP STMT",
    "Typhoon Warning":                    "TYPH WRN",
    "Typhoon Watch":                      "TYPH WCH",
    "Storm Surge Warning":                "SURGE WRN",
    "Storm Surge Watch":                  "SURGE WCH",
    # Winter
    "Blizzard Warning":                   "BLIZZ WRN",
    "Winter Storm Warning":               "WNTR WRN",
    "Winter Storm Watch":                 "WNTR WCH",
    "Winter Weather Advisory":            "WNTR ADV",
    "Ice Storm Warning":                  "ICE WRN",
    "Snow Squall Warning":                "SQUALL WRN",
    "Lake Effect Snow Warning":           "LKSNOW WRN",
    "Freezing Rain Advisory":             "FRZRN ADV",
    "Freezing Fog Advisory":              "FRZFOG ADV",
    "Freezing Spray Advisory":            "FRZSPY ADV",
    "Heavy Freezing Spray Warning":       "FRZSPY WRN",
    "Heavy Freezing Spray Watch":         "FRZSPY WCH",
    # Wind Chill / Cold
    "Wind Chill Warning":                 "WCHILL WRN",
    "Wind Chill Watch":                   "WCHILL WCH",
    "Wind Chill Advisory":                "WCHILL ADV",
    "Extreme Cold Warning":               "COLD WRN",
    "Extreme Cold Watch":                 "COLD WCH",
    "Cold Weather Advisory":              "COLD ADV",
    # Freeze / Frost
    "Freeze Warning":                     "FREZ WRN",
    "Freeze Watch":                       "FREZ WCH",
    "Frost Advisory":                     "FROST ADV",
    # Heat — renamed March 4, 2025: Excessive → Extreme
    "Extreme Heat Warning":               "HEAT WRN",
    "Extreme Heat Watch":                 "HEAT WCH",
    "Excessive Heat Warning":             "HEAT WRN",   # legacy name, keep for safety
    "Excessive Heat Watch":               "HEAT WCH",   # legacy name, keep for safety
    "Heat Advisory":                      "HEAT ADV",
    # Wind
    "High Wind Warning":                  "HI WND WRN",
    "High Wind Watch":                    "HI WND WCH",
    "Extreme Wind Warning":               "XTRM WIND",
    "Wind Advisory":                      "WIND ADV",
    "Lake Wind Advisory":                 "LK WND ADV",
    "Brisk Wind Advisory":                "BRSK WIND",
    # Fire
    "Red Flag Warning":                   "RED FLAG",
    "Fire Weather Watch":                 "FIRE WCH",
    "Fire Warning":                       "FIRE WRN",
    "Extreme Fire Danger":                "FIRE DNGR",
    # Fog / Smoke / Dust
    "Dense Fog Advisory":                 "FOG ADV",
    "Dense Smoke Advisory":               "SMOKE ADV",
    "Dust Storm Warning":                 "DUST WRN",
    "Blowing Dust Warning":               "DUST WRN",
    "Dust Advisory":                      "DUST ADV",
    "Blowing Dust Advisory":              "DUST ADV",
    # Marine
    "Special Marine Warning":             "MARINE WRN",
    "Gale Warning":                       "GALE WRN",
    "Gale Watch":                         "GALE WCH",
    "Storm Warning":                      "STORM WRN",
    "Storm Watch":                        "STORM WCH",
    "Hazardous Seas Warning":             "HAZ SEAS",
    "Hazardous Seas Watch":               "HAZ SEAS W",
    "Small Craft Advisory":               "SML CRAFT",
    "High Surf Warning":                  "SURF WRN",
    "High Surf Advisory":                 "SURF ADV",
    "Rip Current Statement":              "RIP CURR",
    "Beach Hazards Statement":            "BCH HAZARD",
    "Low Water Advisory":                 "LOW WATER",
    # Avalanche / Snow
    "Avalanche Warning":                  "AVLNCH WRN",
    "Avalanche Watch":                    "AVLNCH WCH",
    "Avalanche Advisory":                 "AVLNCH ADV",
    # Volcanic / Geologic
    "Tsunami Warning":                    "TSUNAMI WRN",
    "Tsunami Watch":                      "TSUNAMI WCH",
    "Tsunami Advisory":                   "TSUNAMI ADV",
    "Earthquake Warning":                 "QUAKE WRN",
    "Volcano Warning":                    "VOLCAN WRN",
    "Ashfall Warning":                    "ASHFALL WRN",
    "Ashfall Advisory":                   "ASHFALL ADV",
    # Civil / Emergency
    "Shelter In Place Warning":           "SHELTER-IN",
    "Evacuation Immediate":               "EVACUATE",
    "Civil Danger Warning":               "CIVIL WRN",
    "Civil Emergency Message":            "CIVIL EMRG",
    "Law Enforcement Warning":            "LAW WRN",
    "Local Area Emergency":               "LOCAL EMRG",
    "911 Telephone Outage":               "911 OUTAGE",
    "Nuclear Power Plant Warning":        "NUCLEAR WRN",
    "Radiological Hazard Warning":        "RADIO HAZ",
    "Hazardous Materials Warning":        "HAZMAT WRN",
    # Air Quality
    "Air Quality Alert":                  "AIR QUAL",
    "Air Stagnation Advisory":            "AIR STAG",
}

# Force certain high-impact events to red regardless of NWS severity field.
# (e.g. Extreme Heat Warning has severity="Severe" in the API but deserves red.)
_EVENT_COLOUR_OVERRIDE = {
    "Extreme Heat Warning":       "red",
    "Extreme Heat Watch":         "red",
    "Excessive Heat Warning":     "red",   # legacy
    "Severe Thunderstorm Warning": "red",
    "Severe Thunderstorm Watch":   "orange",
    "Tornado Warning":            "red",
    "Tornado Watch":              "red",
    "Blizzard Warning":           "red",
    "Ice Storm Warning":          "red",
    "Extreme Wind Warning":       "red",
    "Tsunami Warning":            "red",
    "Earthquake Warning":         "red",
    "Nuclear Power Plant Warning":"red",
    "Radiological Hazard Warning":"red",
    "Hazardous Materials Warning":"red",
    "Evacuation Immediate":       "red",
    "Shelter In Place Warning":   "red",
}


def _abbreviate(event):
    """Return ≤10 char display text for a NWS event name."""
    event_clean = event.strip()
    if event_clean in _EVENT_ABBR:
        return _EVENT_ABBR[event_clean]
    return event_clean[:10]


# In-memory cache
_cached_data = None
_cached_ts = 0.0
_lock = threading.Lock()


def _suppress_watches(alerts):
    """Drop Watch alerts when a Warning for the same hazard is already present."""
    warning_prefixes = set()
    for a in alerts:
        text = a.get("text", "")
        if " WRN" in text:
            warning_prefixes.add(text.split(" WRN")[0].strip())
    if not warning_prefixes:
        return alerts
    filtered = []
    for a in alerts:
        text = a.get("text", "")
        if " WCH" in text:
            prefix = text.split(" WCH")[0].strip()
            if prefix in warning_prefixes:
                logger.debug(f"[NWS] Suppressing watch '{text}' — warning already active")
                continue
        filtered.append(a)
    return filtered


def _fetch(lat, lon):
    """Fetch active alerts from NWS for the given coordinates."""
    try:
        r = requests.get(
            _API_URL,
            params={"point": f"{lat},{lon}"},
            headers={"User-Agent": "its-a-plane-python/1.0"},
            timeout=(5, 15),
        )
        r.raise_for_status()

        features = r.json().get("features", [])
        alerts = []
        for feature in features:
            props = feature.get("properties", {})
            severity = props.get("severity", "")
            if severity not in _SEVERITY_COLOUR:
                continue
            event = props.get("event", "WX ALERT")
            logger.debug(f"[NWS] Raw event: {event!r}  severity: {severity!r}")
            color = _EVENT_COLOUR_OVERRIDE.get(event.strip(), _SEVERITY_COLOUR[severity])
            alerts.append({"text": _abbreviate(event), "color": color})

        alerts = _suppress_watches(alerts)

        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump({"ts": time.time(), "data": alerts}, f)

        logger.debug(f"[NWS] Fetched {len(alerts)} Extreme/Severe alert(s)")
        return alerts

    except Exception as e:
        logger.error(f"[NWS] Fetch failed: {e}")
        return None


def _load_cache():
    """Load from disk cache. Returns (data, ts) or (None, 0)."""
    try:
        with open(_CACHE_FILE, "r") as f:
            obj = json.load(f)
        ts = obj.get("ts", 0)
        if time.time() - ts < _POLL_INTERVAL * 2:
            return obj.get("data", []), ts
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None, 0


def _background_refresh(lat, lon):
    """Run the blocking fetch off the caller's thread. Releases _lock when done."""
    global _cached_data, _cached_ts
    try:
        now = time.time()
        data = _fetch(lat, lon)
        if data is not None:
            _cached_data = data
            _cached_ts = now
        else:
            # Back off so a failed fetch doesn't retry every second
            _cached_ts = now
    finally:
        _lock.release()


def _refresh(lat, lon):
    """Return cached alerts immediately; kick off a background fetch if stale.

    Never blocks on the network. Previously this had no lock at all, so a
    stale cache meant *every* concurrent request (e.g. a slave hammering
    /clock/json) would independently open its own blocking call to
    api.weather.gov — if that call hung (DNS outage, slow upstream), each
    request thread hung with it, exhausting the Flask server's threads. Now
    only one background fetch runs at a time and callers never block.
    """
    global _cached_data, _cached_ts

    now = time.time()
    if _cached_data is not None and (now - _cached_ts) < _POLL_INTERVAL:
        return _cached_data

    if _cached_data is None:
        disk, disk_ts = _load_cache()
        if disk is not None:
            _cached_data = disk
            _cached_ts = disk_ts
            logger.debug("[NWS] Loaded from disk cache")
            if (now - _cached_ts) < _POLL_INTERVAL:
                return _cached_data

    # Stale or missing — try to claim the refresh slot without blocking.
    if _lock.acquire(blocking=False):
        threading.Thread(target=_background_refresh, args=(lat, lon), daemon=True).start()
    # else: a refresh is already in flight elsewhere — fall through and
    # return whatever we've got.

    return _cached_data or []


def get_nws_alerts():
    """Return list of active Extreme/Severe NWS alert dicts for TEMPERATURE_LOCATION.

    Each dict: {"text": "TORN WRN", "color": "red"|"orange"}
    Returns [] if alerts are disabled or location is not configured.
    """
    try:
        import config as cfg
        if not getattr(cfg, "WEATHER_ALERTS_ENABLED", True):
            return []
        location = getattr(cfg, "TEMPERATURE_LOCATION", "")
        if not location:
            return []
        parts = location.split(",")
        if len(parts) != 2:
            logger.warning(f"[NWS] Cannot parse TEMPERATURE_LOCATION: {location!r}")
            return []
        lat, lon = parts[0].strip(), parts[1].strip()
    except Exception as e:
        logger.error(f"[NWS] Config error: {e}")
        return []

    return _refresh(lat, lon)
