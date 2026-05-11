"""
Synchronous wrapper around the `fr24` Python package (v0.3.0+).

Provides a drop-in replacement for the old unofficial FlightRadar24API usage,
bridging the async API into the synchronous threading model used by this project.

NOTE: The `fr24` pip package (by cathaypacific8747) is an unofficial gRPC client
that accesses FR24's internal feed endpoint (data-feed.flightradar24.com).
The official FR24 SDK is `pip install fr24sdk` from Flightradar24's GitHub.

Environment variables used by the fr24 package for authentication:
    fr24_subscription_key  – your subscription key
    fr24_token             – your access token (JWT)

Alternatively, FR24_API_KEY in .env (format: "subscription_key|token")
is parsed and injected into the environment before the fr24 package reads them.

Thread-safety: Each API call creates its own event loop and FR24 context manager,
ensuring no shared mutable state across threads. The FR24 client is entered and
exited per call cycle to prevent resource leaks (HTTP/gRPC connections, file
descriptors) over long-running 24/7 operation.
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
try:
    import h2  # noqa: E402, F401
    import h2.connection  # noqa: E402, F401
    import h2.config  # noqa: E402, F401
    import h2.events  # noqa: E402, F401
    import hpack  # noqa: E402, F401
except ImportError:
    pass  # Non-fatal — only needed if using HTTP/2


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
        The gRPC API does NOT provide airline name or airport coordinates
        directly. It provides: flight_number, aircraft type, schedule times,
        flight progress.
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
    Synchronous client wrapping the async `fr24` package.

    Thread-safe: each API call creates a fresh event loop and FR24 context
    manager. The context manager is properly entered and exited per cycle,
    preventing resource leaks (HTTP/gRPC connections, file descriptors) over
    long-running 24/7 operation.

    Includes built-in caching:
      - Live feed (get_flights): polled at most every 90 seconds.
      - Flight details (get_flight_details): cached per flight_id for 30 minutes.
    """

    def __init__(self):
        self._cache = FR24Cache()
        self._fr24_ok = True  # Track whether FR24 is reachable

    def _run_with_client(self, async_func):
        """
        Create a fresh event loop and FR24 context manager, run the async
        function, then properly clean up. This ensures:
        1. Thread-safety (no shared event loop across threads)
        2. No resource leaks (__aexit__ always called)
        3. Fresh connections each cycle (no stale HTTP/2 streams)
        """
        loop = asyncio.new_event_loop()
        try:
            async def _wrapper():
                fr24 = FR24()
                async with fr24:
                    try:
                        await fr24.login("from_env")
                    except Exception as e:
                        logger.debug(f"FR24: Login failed ({type(e).__name__}: {e}) — using anonymous")
                    return await async_func(fr24)
            return loop.run_until_complete(_wrapper())
        finally:
            loop.close()

    @property
    def fr24_ok(self) -> bool:
        """Whether FR24 is currently reachable."""
        return self._fr24_ok

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

        # Check rate limiter (90s polling interval, per-key)
        if not self._cache.should_poll_feed(cache_key):
            logger.info("FR24: Rate limited (90s) — returning cached or empty")
            return []

        # Make the actual API call
        logger.info(f"FR24: Making live API call (key: {cache_key})")
        try:
            result = self._run_with_client(
                lambda fr24: self._get_flights_async(fr24, bounds, airline)
            )
            # Reset fr24_ok on success (fixes: flag never reset to True)
            self._fr24_ok = True
        except (ConnectionError, OSError) as e:
            logger.warning(f"FR24: Connection error: {e}")
            self._fr24_ok = False
            return []
        except Exception as e:
            logger.warning(f"FR24: Unexpected error fetching flights: {e}")
            self._fr24_ok = False
            return []

        # Cache the result and record the poll
        self._cache.set_cached_flights(cache_key, result)
        self._cache.record_feed_poll(cache_key)

        return result

    async def _get_flights_async(
        self,
        fr24: FR24,
        bounds: dict | None,
        airline: str | None,
    ) -> list[LiveFlight]:
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
            # Defensive null handling for protobuf fields
            # (protobuf fields can be absent/None; getattr chains prevent crashes)
            extra = getattr(f, 'extra_info', None)
            route = getattr(extra, 'route', None) if extra else None
            origin_iata = (getattr(route, 'from', '') or '') if route else ''
            destination_iata = (getattr(route, 'to', '') or '') if route else ''
            callsign = getattr(f, 'callsign', '') or ''
            registration = (getattr(extra, 'reg', '') or '') if extra else ''
            aircraft_type = (getattr(extra, 'type', '') or '') if extra else ''
            vspeed = (getattr(extra, 'vspeed', 0) or 0) if extra else 0

            # ETA from schedule (defensive)
            schedule = getattr(extra, 'schedule', None) if extra else None
            eta = 0
            if schedule is not None:
                try:
                    eta = getattr(schedule, 'eta', 0) or 0
                except Exception:
                    eta = 0

            lf = LiveFlight(
                flight_id=f"{f.flightid:x}" if f.flightid else "",
                latitude=f.lat,
                longitude=f.lon,
                altitude=f.alt,
                ground_speed=f.speed,
                heading=f.track,
                vertical_speed=vspeed,
                callsign=callsign,
                registration=registration,
                origin_airport_iata=origin_iata,
                destination_airport_iata=destination_iata,
                airline_icao=callsign[:3] if callsign and len(callsign) >= 3 and callsign[:3].isalpha() else "",
                airline_iata="",
                aircraft_code=aircraft_type,
                on_ground=f.on_ground,
                eta=eta,
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
        try:
            result = self._run_with_client(
                lambda fr24: self._get_flight_details_async(fr24, flight)
            )
            # Reset fr24_ok on success
            self._fr24_ok = True
        except (ConnectionError, OSError) as e:
            logger.warning(f"FR24: Connection error getting details: {e}")
            self._fr24_ok = False
            return {}
        except Exception as e:
            logger.warning(f"FR24: Error getting flight details: {e}")
            self._fr24_ok = False
            return {}

        # Cache the result (even empty dicts, to avoid repeated failed lookups)
        if result:
            self._cache.set_cached_flight_details(flight.flight_id, result)

        return result

    async def _get_flight_details_async(self, fr24: FR24, flight: LiveFlight) -> dict:
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

        # Extract proto fields safely using defensive getattr
        schedule_info = getattr(proto, 'schedule_info', None)
        aircraft_info = getattr(proto, 'aircraft_info', None)
        flight_progress = getattr(proto, 'flight_progress', None)
        flight_plan = getattr(proto, 'flight_plan', None)
        flight_info = getattr(proto, 'flight_info', None)

        # Extract airline name from aircraft_info.registered_owners
        flight_number = (getattr(schedule_info, 'flight_number', '') or '') if schedule_info else ''
        airline_name = (getattr(aircraft_info, 'registered_owners', '') or '') if aircraft_info else ''
        # painted_as_id indicates livery (e.g. special livery when != operated_by_id)
        painted_as_id = (getattr(schedule_info, 'painted_as_id', 0) or 0) if schedule_info else 0
        operated_by_id = (getattr(schedule_info, 'operated_by_id', 0) or 0) if schedule_info else 0

        # flight_plan.departure/destination are ICAO strings (e.g. "EGLL"), not objects
        fp_departure = (getattr(flight_plan, 'departure', '') or '') if flight_plan else ''
        fp_destination = (getattr(flight_plan, 'destination', '') or '') if flight_plan else ''

        # flight_progress distances are in km (unsigned int)
        remaining_km = (getattr(flight_progress, 'remaining_distance', 0) or 0) if flight_progress else 0
        total_km = (getattr(flight_progress, 'great_circle_distance', 0) or 0) if flight_progress else 0
        eta = (getattr(flight_progress, 'eta', 0) or 0) if flight_progress else 0
        traversed_distance = (getattr(flight_progress, 'traversed_distance', 0) or 0) if flight_progress else 0
        elapsed_time = (getattr(flight_progress, 'elapsed_time', 0) or 0) if flight_progress else 0
        remaining_time = (getattr(flight_progress, 'remaining_time', 0) or 0) if flight_progress else 0

        # Schedule fields (defensive)
        sched_departure = (getattr(schedule_info, 'scheduled_departure', None)) if schedule_info else None
        sched_arrival = (getattr(schedule_info, 'scheduled_arrival', None)) if schedule_info else None
        actual_departure = (getattr(schedule_info, 'actual_departure', None)) if schedule_info else None
        actual_arrival = (getattr(schedule_info, 'actual_arrival', None)) if schedule_info else None
        origin_id = (getattr(schedule_info, 'origin_id', 0) or 0) if schedule_info else 0
        destination_id = (getattr(schedule_info, 'destination_id', 0) or 0) if schedule_info else 0

        # Aircraft info (defensive)
        ac_type = (getattr(aircraft_info, 'type', '') or '') if aircraft_info else ''
        ac_reg = (getattr(aircraft_info, 'reg', '') or '') if aircraft_info else ''
        ac_icao_address = (getattr(aircraft_info, 'icao_address', '') or '') if aircraft_info else ''

        # Build old-style nested dict for backward compat with overhead.py parsing
        compat = {
            "aircraft": {
                "model": {
                    "code": ac_type,
                },
                "registration": ac_reg,
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
                    "departure": sched_departure or None,
                    "arrival": sched_arrival or None,
                },
                "real": {
                    "departure": actual_departure or None,
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
                "origin_id": origin_id,
                "destination_id": destination_id,
                "scheduled_departure": sched_departure or None,
                "scheduled_arrival": sched_arrival or None,
                "actual_departure": actual_departure or None,
                "actual_arrival": actual_arrival or None,
            },
            "aircraft_info": {
                "icao_address": ac_icao_address,
                "reg": ac_reg,
                "typecode": ac_type,
                "registered_owners": airline_name,
            },
            "flight_progress": {
                "traversed_distance": traversed_distance,
                "remaining_distance": remaining_km,
                "elapsed_time": elapsed_time,
                "remaining_time": remaining_time,
                "eta": eta,
                "great_circle_distance": total_km,
            },
            "flight_plan": {
                "departure_icao": fp_departure,
                "destination_icao": fp_destination,
            },
        }

        # Trail points (defensive)
        trail_points = []
        flight_trail = getattr(proto, 'flight_trail_list', []) or []
        for tp in flight_trail:
            alt = getattr(tp, 'altitude', 0) or 0
            if alt > 0:
                trail_points.append({
                    "lat": getattr(tp, 'lat', 0),
                    "lng": getattr(tp, 'lon', 0),
                    "alt": alt,
                    "ts": getattr(tp, 'snapshot_id', 0),
                })
        compat["trail"] = trail_points

        # Also store the flight_info position data (useful for tracked)
        if flight_info:
            compat["flight_info"] = {
                "latitude": getattr(flight_info, 'lat', 0),
                "longitude": getattr(flight_info, 'lon', 0),
                "altitude": getattr(flight_info, 'alt', 0),
                "ground_speed": getattr(flight_info, 'speed', 0),
                "heading": getattr(flight_info, 'track', 0),
                "vertical_speed": getattr(flight_info, 'vspeed', 0),
                "callsign": getattr(flight_info, 'callsign', '') or '',
            }

        return compat

    def close(self):
        """Clean up — no-op since we create/destroy per call now."""
        pass

    def __del__(self):
        pass
