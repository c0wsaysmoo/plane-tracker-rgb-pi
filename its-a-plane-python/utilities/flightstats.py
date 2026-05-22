"""
flightstats.py — Free route lookup fallback via FlightStats JSON API.

Used when FR24 gRPC returns empty origin/destination (~1% of flights).
No API key required — uses the public flight tracker endpoint.

Returns: {"origin": "EWR", "destination": "LAX", "aircraft": "B738"} or None.
"""

import logging
import re
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# FlightStats public JSON endpoint (not HTML scraping)
_BASE_URL = "https://www.flightstats.com/v2/api-next/flight-tracker/{carrier}/{number}/{year}/{month}/{day}"

# Cache: callsign → (result_dict_or_None, timestamp)
_cache = {}
_CACHE_TTL = 300  # 5 minutes

# Common ICAO→IATA carrier mappings (FlightStats uses IATA codes)
_ICAO_TO_IATA = {
    "AAL": "AA", "UAL": "UA", "DAL": "DL", "SWA": "WN",
    "JBU": "B6", "ASA": "AS", "NKS": "NK", "FFT": "F9",
    "HAL": "HA", "SKW": "OO", "RPA": "YX", "ENY": "MQ",
    "EJA": "EJ", "AWI": "ZW", "JIA": "OH", "GJS": "G7",
    "BAW": "BA", "DLH": "LH", "AFR": "AF", "KLM": "KL",
    "SAS": "SK", "FIN": "AY", "ACA": "AC", "WJA": "WS",
    "QFA": "QF", "ANZ": "NZ", "SIA": "SQ", "CPA": "CX",
    "JAL": "JL", "ANA": "NH", "KAL": "KE", "AAR": "OZ",
    "CCA": "CA", "CES": "MU", "CSN": "CZ", "THY": "TK",
    "UAE": "EK", "ETD": "EY", "QTR": "QR", "SAA": "SA",
    "TAM": "JJ", "AVA": "AV", "AZU": "AD", "GLO": "G3",
    "VOI": "VY", "EZY": "U2", "RYR": "FR", "WZZ": "W6",
    "VIR": "VS", "ICE": "FI", "TAP": "TP", "IBS": "IB",
    "EIN": "EI", "SWR": "LX", "AUA": "OS", "BEL": "SN",
    # Regional US carriers
    "ASH": "S5", "CPZ": "C5", "EDV": "9E", "PDT": "PT",
    "ROU": "ROU",
}


def _parse_callsign(callsign):
    """
    Parse callsign into (IATA carrier, flight number).

    Examples:
        "UAL1234" → ("UA", "1234")
        "UA1234"  → ("UA", "1234")
        "BAW123"  → ("BA", "123")

    Returns (None, None) if unparseable.
    """
    if not callsign:
        return None, None

    callsign = callsign.strip().upper()

    # Try ICAO 3-letter prefix first
    m = re.match(r'^([A-Z]{3})(\d+)$', callsign)
    if m:
        icao = m.group(1)
        number = m.group(2)
        iata = _ICAO_TO_IATA.get(icao)
        if iata:
            return iata, number
        # Unknown ICAO prefix — try as-is (some carriers use 3-letter IATA)
        return icao, number

    # Try IATA 2-letter prefix
    m = re.match(r'^([A-Z0-9]{2})(\d+)$', callsign)
    if m:
        return m.group(1), m.group(2)

    return None, None


def get_route(callsign):
    """
    Look up route info for a flight callsign via FlightStats.

    Returns dict with origin, destination, aircraft (IATA codes) or None.
    Results are cached for 5 minutes.
    """
    if not callsign:
        return None

    callsign = callsign.strip().upper()

    # Check cache (evict expired entries, hard cap at 500)
    from time import time
    now = time()
    if len(_cache) > 500:
        expired = [k for k, (_, ts) in _cache.items() if now - ts >= _CACHE_TTL]
        for k in expired:
            del _cache[k]
        # Hard cap: if still over 500 after expiry, drop oldest
        if len(_cache) > 500:
            oldest = sorted(_cache, key=lambda k: _cache[k][1])[:len(_cache) - 300]
            for k in oldest:
                del _cache[k]
    if callsign in _cache:
        result, ts = _cache[callsign]
        if now - ts < _CACHE_TTL:
            return result

    carrier, number = _parse_callsign(callsign)
    if not carrier or not number:
        _cache[callsign] = (None, now)
        return None

    # Try today first, then yesterday
    for date in [datetime.now(), datetime.now() - timedelta(days=1)]:
        url = _BASE_URL.format(
            carrier=carrier,
            number=number,
            year=date.year,
            month=date.month,
            day=date.day,
        )

        try:
            r = requests.get(url, timeout=(5, 10), headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            })
            if r.status_code != 200:
                continue

            data = r.json()
            # Navigate to flight data
            flight_state = data.get("data", {}).get("flightState")
            if not flight_state:
                continue

            dep_airport = flight_state.get("departureAirport", {})
            arr_airport = flight_state.get("arrivalAirport", {})
            equipment = flight_state.get("equipment", {})

            origin = dep_airport.get("iata", "")
            destination = arr_airport.get("iata", "")

            if not origin and not destination:
                continue

            result = {
                "origin": origin,
                "destination": destination,
                "aircraft": equipment.get("iata", ""),
            }
            _cache[callsign] = (result, now)
            logger.info(f"[FlightStats] {callsign}: {origin}→{destination}")
            return result

        except Exception as e:
            logger.debug(f"[FlightStats] {callsign} lookup failed: {e}")
            continue

    _cache[callsign] = (None, now)
    return None
