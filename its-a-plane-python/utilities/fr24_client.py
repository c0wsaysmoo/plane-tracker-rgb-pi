"""
Synchronous wrapper around the official `fr24` Python SDK (v0.3.0+).

Provides a drop-in replacement for the old unofficial FlightRadar24API usage,
bridging the async API into the synchronous threading model used by this project.

Environment variables used by the fr24 package for authentication:
    fr24_subscription_key  – your subscription key
    fr24_token             – your access token (JWT)

Alternatively, FR24_API_KEY in config.py (format: "subscription_key|token")
is parsed and injected into the environment before the fr24 package reads them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from utilities.cache import FR24Cache

logger = logging.getLogger(__name__)


def _is_jwt(token: str) -> bool:
    """Check if a string looks like a valid JWT (3 dot-separated base64 parts)."""
    parts = token.split(".")
    return len(parts) == 3 and all(len(p) > 0 for p in parts)


def _ensure_env_credentials() -> None:
    """
    Read FR24_API_KEY from config (if available) and set the environment
    variables that the fr24 package expects: fr24_subscription_key, fr24_token.
    """
    if os.environ.get("fr24_subscription_key"):
        return  # already set

    try:
        from config import FR24_API_KEY
    except (ImportError, AttributeError):
        FR24_API_KEY = os.environ.get("FR24_API_KEY", "")

    if not FR24_API_KEY:
        logger.warning("No FR24 API key found – using anonymous access")
        return

    if "|" in FR24_API_KEY:
        sub_key, token = FR24_API_KEY.split("|", 1)
        os.environ.setdefault("fr24_subscription_key", sub_key)
        # Only set token if it's actually a JWT; otherwise token auth will
        # fail silently and fall back to anonymous anyway
        if _is_jwt(token):
            os.environ.setdefault("fr24_token", token)
    else:
        os.environ.setdefault("fr24_subscription_key", FR24_API_KEY)


# Inject credentials before importing fr24
_ensure_env_credentials()

from fr24 import FR24, BoundingBox  # noqa: E402

# Force-import h2 early (before rgbmatrix drops privileges).
# httpx lazy-imports h2 only when AsyncClient(http2=True) is constructed;
# by that time drop_privileges may have removed read access to venv files.
import h2  # noqa: E402, F401
import h2.connection  # noqa: E402, F401
import h2.config  # noqa: E402, F401
import h2.events  # noqa: E402, F401
import hpack  # noqa: E402, F401


@dataclass
class LiveFlight:
    """
    A lightweight object mimicking the old FlightRadar24API Flight object
    so that downstream code (distance calculations, bearings, etc.) works
    without modification.
    """
    flight_id: str
    latitude: float
    longitude: float
    altitude: int
    ground_speed: int
    heading: int
    vertical_speed: int
    callsign: str
    registration: str
    origin_airport_iata: str
    destination_airport_iata: str
    airline_icao: str
    airline_iata: str
    aircraft_code: str
    on_ground: bool
    eta: int  # estimated arrival unix timestamp (0 if unknown)

    # Fields populated after get_flight_details
    airline_name: str = ""
    number: str = ""
    origin_airport_latitude: float = 0.0
    origin_airport_longitude: float = 0.0
    destination_airport_latitude: float = 0.0
    destination_airport_longitude: float = 0.0

    def set_flight_details(self, details: dict) -> None:
        """
        Populate additional fields from a flight_details dict response.
        The official gRPC API does NOT provide airline name or airport coordinates.
        It provides: flight_number, aircraft type, schedule times, flight progress.
        """
        if not details:
            return

        schedule = details.get("schedule_info", {})
        flight_progress = details.get("flight_progress", {})
        flight_info = details.get("flight_info", {})

        self.number = schedule.get("flight_number", "") or self.callsign
        # Airline name from aircraft_info.registered_owners
        aircraft = details.get("aircraft_info", {})
        self.airline_name = aircraft.get("registered_owners", "") or ""

        # Update position from flight_info if available (more current)
        if flight_info:
            if flight_info.get("latitude"):
                self.latitude = flight_info["latitude"]
            if flight_info.get("longitude"):
                self.longitude = flight_info["longitude"]
            if flight_info.get("altitude"):
                self.altitude = flight_info["altitude"]
            if flight_info.get("ground_speed"):
                self.ground_speed = flight_info["ground_speed"]
            if flight_info.get("heading"):
                self.heading = flight_info["heading"]
            if flight_info.get("vertical_speed"):
                self.vertical_speed = flight_info["vertical_speed"]


class FR24Client:
    """
    Synchronous client wrapping the async `fr24` SDK.
    Thread-safe: each call runs its own event loop iteration.

    Includes built-in caching:
      - Live feed (get_flights): polled at most every 90 seconds.
      - Flight details (get_flight_details): cached per flight_id for 30 minutes.
    """

    def __init__(self):
        self._fr24: FR24 | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cache = FR24Cache()

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        loop = self._get_loop()
        return loop.run_until_complete(coro)

    async def _ensure_client(self) -> FR24:
        if self._fr24 is None:
            logger.info("FR24: Initializing client and logging in...")
            self._fr24 = FR24()
            await self._fr24.__aenter__()
            try:
                await self._fr24.login("from_env")
                logger.info("FR24: Login successful")
            except Exception as e:
                logger.warning(f"FR24: Login failed ({type(e).__name__}: {e}) — continuing with anonymous access")
        return self._fr24

    @property
    def cache(self) -> FR24Cache:
        """Expose the cache for external inspection/testing."""
        return self._cache

    def get_flights(
        self,
        bounds: dict | None = None,
        airline: str | None = None,
    ) -> list[LiveFlight]:
        """
        Get live flights, optionally filtered by bounding box or airline.

        Results are cached for 90 seconds. If called again within the polling
        interval, the cached result is returned without making an API call.

        :param bounds: Dict with keys tl_y, tl_x, br_y, br_x (old format)
                       or use default world bounds
        :param airline: ICAO airline code to filter by (post-filter)
        :returns: List of LiveFlight objects
        """
        cache_key = self._cache.make_feed_cache_key(bounds, airline)

        # Check cache first
        cached = self._cache.get_cached_flights(cache_key)
        if cached is not None:
            logger.info(f"FR24: Cache hit ({len(cached)} flights) for key: {cache_key}")
            return cached

        # Check rate limiter (90s polling interval)
        if not self._cache.should_poll_feed():
            logger.info("FR24: Rate limited (90s) — returning cached or empty")
            # Try to return any cached result for this key (even if slightly different params)
            return cached if cached is not None else []

        # Make the actual API call
        logger.info(f"FR24: Making live API call (key: {cache_key})")
        result = self._run(self._get_flights_async(bounds, airline))

        # Cache the result and record the poll
        self._cache.set_cached_flights(cache_key, result)
        self._cache.record_feed_poll()

        return result

    async def _get_flights_async(
        self,
        bounds: dict | None,
        airline: str | None,
    ) -> list[LiveFlight]:
        fr24 = await self._ensure_client()

        if bounds:
            # Old format: tl_y=north, tl_x=west, br_y=south, br_x=east
            bbox = BoundingBox(
                south=bounds["br_y"],
                north=bounds["tl_y"],
                west=bounds["tl_x"],
                east=bounds["br_x"],
            )
        else:
            # World bounds
            bbox = BoundingBox(south=-90, north=90, west=-180, east=180)

        # Max 4 fields for unauthenticated users; vspeed requires auth
        fields = {"flight", "reg", "route", "type"}

        logger.info(f"FR24: Fetching live feed (bbox: S={bbox.south}, N={bbox.north}, W={bbox.west}, E={bbox.east})")
        result = await fr24.live_feed.fetch(
            bounding_box=bbox,
            limit=1500,
            fields=fields,
        )

        try:
            proto = result.to_proto()
        except Exception as e:
            logger.warning(f"FR24: Failed to parse live feed response: {e}")
            return []

        flight_count = len(proto.flights_list)
        logger.info(f"FR24: Live feed returned {flight_count} flights")
        flights = []
        for f in proto.flights_list:
            route = f.extra_info.route
            origin_iata = getattr(route, "from", "") or ""
            destination_iata = route.to or ""

            lf = LiveFlight(
                flight_id=f"{f.flightid:x}" if f.flightid else "",
                latitude=f.lat,
                longitude=f.lon,
                altitude=f.alt,
                ground_speed=f.speed,
                heading=f.track,
                vertical_speed=f.extra_info.vspeed,
                callsign=f.callsign or "",
                registration=f.extra_info.reg or "",
                origin_airport_iata=origin_iata,
                destination_airport_iata=destination_iata,
                airline_icao=f.callsign[:3] if f.callsign and len(f.callsign) >= 3 and f.callsign[:3].isalpha() else "",
                airline_iata="",
                aircraft_code=f.extra_info.type or "",
                on_ground=f.on_ground,
                eta=f.extra_info.schedule.eta if f.extra_info.HasField("schedule") else 0,
            )
            flights.append(lf)

        # Post-filter by airline ICAO if specified
        if airline:
            airline_upper = airline.upper()
            flights = [
                f for f in flights
                if f.airline_icao.upper() == airline_upper
            ]

        return flights

    def get_flight_details(self, flight: LiveFlight) -> dict:
        """
        Get detailed information about a live flight.

        Results are cached per flight_id for 30 minutes. If the flight details
        are already in cache, the cached version is returned without an API call.

        :param flight: A LiveFlight object (must have flight_id set)
        :returns: Nested dict with flight details
        """
        if not flight.flight_id:
            return {}

        # Check 30-minute cache first
        cached = self._cache.get_cached_flight_details(flight.flight_id)
        if cached is not None:
            logger.debug(f"FR24 flight detail cache hit for: {flight.flight_id}")
            return cached

        # Cache miss — fetch from API
        result = self._run(self._get_flight_details_async(flight))

        # Cache the result (even empty dicts, to avoid repeated failed lookups)
        if result:
            self._cache.set_cached_flight_details(flight.flight_id, result)

        return result

    async def _get_flight_details_async(self, flight: LiveFlight) -> dict:
        fr24 = await self._ensure_client()

        if not flight.flight_id:
            return {}

        try:
            result = await fr24.flight_details.fetch(
                flight_id=flight.flight_id,
                verbose=True,
            )
            proto = result.to_proto()
        except Exception as e:
            logger.warning(f"Failed to fetch flight details for {flight.flight_id}: {e}")
            return {}

        # Extract proto fields safely
        schedule_info = proto.schedule_info
        aircraft_info = proto.aircraft_info
        flight_progress = proto.flight_progress
        flight_plan = proto.flight_plan
        flight_info = proto.flight_info

        # Extract airline name from aircraft_info.registered_owners
        flight_number = schedule_info.flight_number or ""
        airline_name = aircraft_info.registered_owners or ""
        # painted_as_id indicates livery (e.g. special livery when != operated_by_id)
        painted_as_id = schedule_info.painted_as_id or 0
        operated_by_id = schedule_info.operated_by_id or 0

        # flight_plan.departure/destination are ICAO strings (e.g. "EGLL"), not objects
        fp_departure = flight_plan.departure or ""  # ICAO origin
        fp_destination = flight_plan.destination or ""  # ICAO destination

        # flight_progress distances are in km (unsigned int)
        remaining_km = flight_progress.remaining_distance or 0
        total_km = flight_progress.great_circle_distance or 0
        eta = flight_progress.eta or 0

        # Build old-style nested dict for backward compat with overhead.py parsing
        compat = {
            "aircraft": {
                "model": {
                    "code": aircraft_info.type or "",
                },
                "registration": aircraft_info.reg or "",
            },
            "airline": {
                "name": airline_name,
                "code": {
                    "icao": flight.airline_icao,
                },
            },
            "airport": {
                "origin": None,
                "destination": None,
            },
            "time": {
                "scheduled": {
                    "departure": schedule_info.scheduled_departure or None,
                    "arrival": schedule_info.scheduled_arrival or None,
                },
                "real": {
                    "departure": schedule_info.actual_departure or None,
                },
                "estimated": {
                    "arrival": eta or None,
                },
            },
            "trail": [],
            "owner": {
                "code": {
                    "icao": flight.airline_icao,
                },
            },
            # New fields for _grab_tracked compatibility
            "schedule_info": {
                "flight_number": flight_number,
                "operated_by_id": operated_by_id,
                "painted_as_id": painted_as_id,
                "origin_id": schedule_info.origin_id or 0,
                "destination_id": schedule_info.destination_id or 0,
                "scheduled_departure": schedule_info.scheduled_departure or None,
                "scheduled_arrival": schedule_info.scheduled_arrival or None,
                "actual_departure": schedule_info.actual_departure or None,
                "actual_arrival": schedule_info.actual_arrival or None,
            },
            "aircraft_info": {
                "icao_address": aircraft_info.icao_address or "",
                "reg": aircraft_info.reg or "",
                "typecode": aircraft_info.type or "",
                "registered_owners": airline_name,
            },
            "flight_progress": {
                "traversed_distance": flight_progress.traversed_distance or 0,
                "remaining_distance": remaining_km,
                "elapsed_time": flight_progress.elapsed_time or 0,
                "remaining_time": flight_progress.remaining_time or 0,
                "eta": eta,
                "great_circle_distance": total_km,
            },
            "flight_plan": {
                "departure_icao": fp_departure,
                "destination_icao": fp_destination,
            },
        }

        # Trail points
        trail_points = []
        for tp in proto.flight_trail_list:
            if tp.altitude > 0:
                trail_points.append({
                    "lat": tp.lat,
                    "lng": tp.lon,
                    "alt": tp.altitude,
                    "ts": tp.snapshot_id,
                })
        compat["trail"] = trail_points

        # Also store the flight_info position data (useful for tracked)
        if flight_info:
            compat["flight_info"] = {
                "latitude": flight_info.lat,
                "longitude": flight_info.lon,
                "altitude": flight_info.alt,
                "ground_speed": flight_info.speed,
                "heading": flight_info.track,
                "vertical_speed": flight_info.vspeed,
                "callsign": flight_info.callsign or "",
            }

        return compat

    def close(self):
        """Clean up the async client."""
        if self._fr24:
            try:
                self._run(self._fr24.__aexit__(None, None, None))
            except Exception:
                pass
            self._fr24 = None
        if self._loop and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
