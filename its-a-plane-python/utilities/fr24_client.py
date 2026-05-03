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
from typing import Any

logger = logging.getLogger(__name__)


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
        os.environ.setdefault("fr24_token", token)
    else:
        os.environ.setdefault("fr24_subscription_key", FR24_API_KEY)


# Inject credentials before importing fr24
_ensure_env_credentials()

from fr24 import FR24, BoundingBox  # noqa: E402


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
        Mimics the old API's set_flight_details behaviour.
        """
        schedule = details.get("schedule_info", {})
        aircraft = details.get("aircraft_info", {})
        flight_progress = details.get("flight_progress", {})
        flight_plan = details.get("flight_plan", {})
        aircraft_details = details.get("aircraft_details", {})

        self.number = schedule.get("flight_number", "") or self.callsign
        self.airline_name = aircraft_details.get("airline_name", "") or ""

        # Origin/destination from flight_plan if available
        origin = flight_plan.get("origin", {})
        destination = flight_plan.get("destination", {})

        if origin:
            self.origin_airport_latitude = origin.get("lat", 0.0) or 0.0
            self.origin_airport_longitude = origin.get("lon", 0.0) or 0.0
            if origin.get("iata"):
                self.origin_airport_iata = origin["iata"]

        if destination:
            self.destination_airport_latitude = destination.get("lat", 0.0) or 0.0
            self.destination_airport_longitude = destination.get("lon", 0.0) or 0.0
            if destination.get("iata"):
                self.destination_airport_iata = destination["iata"]


class FR24Client:
    """
    Synchronous client wrapping the async `fr24` SDK.
    Thread-safe: each call runs its own event loop iteration.
    """

    def __init__(self):
        self._fr24: FR24 | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

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
            self._fr24 = FR24()
            await self._fr24.__aenter__()
            await self._fr24.login("from_env")
        return self._fr24

    def get_flights(
        self,
        bounds: dict | None = None,
        airline: str | None = None,
    ) -> list[LiveFlight]:
        """
        Get live flights, optionally filtered by bounding box or airline.

        :param bounds: Dict with keys tl_y, tl_x, br_y, br_x (old format)
                       or use default world bounds
        :param airline: ICAO airline code to filter by (post-filter)
        :returns: List of LiveFlight objects
        """
        return self._run(self._get_flights_async(bounds, airline))

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

        # Include vspeed field for authenticated users
        fields = {"flight", "reg", "route", "type", "vspeed"}

        result = await fr24.live_feed.fetch(
            bounding_box=bbox,
            limit=2000,
            fields=fields,
        )

        proto = result.to_proto()
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

        :param flight: A LiveFlight object (must have flight_id set)
        :returns: Nested dict with flight details
        """
        return self._run(self._get_flight_details_async(flight))

    async def _get_flight_details_async(self, flight: LiveFlight) -> dict:
        fr24 = await self._ensure_client()

        if not flight.flight_id:
            return {}

        result = await fr24.flight_details.fetch(
            flight_id=flight.flight_id,
            verbose=True,
        )

        proto = result.to_proto()
        details = result.to_dict()

        # Build a compatibility dict matching what the old API returned
        # so existing code parsing logic works with minimal changes
        schedule_info = getattr(proto, "schedule_info", None)
        aircraft_info = getattr(proto, "aircraft_info", None)
        flight_progress = getattr(proto, "flight_progress", None)
        flight_plan = getattr(proto, "flight_plan", None)
        aircraft_details_proto = getattr(proto, "aircraft_details", None)
        flight_info = getattr(proto, "flight_info", None)

        # Build old-style nested dict for backward compat with overhead.py parsing
        compat = {
            "aircraft": {
                "model": {
                    "code": aircraft_info.type if aircraft_info else "",
                },
                "registration": aircraft_info.reg if aircraft_info else "",
            },
            "airline": {
                "name": (aircraft_details_proto.airline_name if aircraft_details_proto and hasattr(aircraft_details_proto, "airline_name") else "") or "",
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
                    "departure": schedule_info.scheduled_departure if schedule_info and schedule_info.scheduled_departure else None,
                    "arrival": schedule_info.scheduled_arrival if schedule_info and schedule_info.scheduled_arrival else None,
                },
                "real": {
                    "departure": schedule_info.actual_departure if schedule_info and schedule_info.actual_departure else None,
                },
                "estimated": {
                    "arrival": flight_progress.eta if flight_progress and flight_progress.eta else None,
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
                "flight_number": schedule_info.flight_number if schedule_info else "",
                "origin_id": schedule_info.origin_id if schedule_info else 0,
                "destination_id": schedule_info.destination_id if schedule_info else 0,
                "scheduled_departure": schedule_info.scheduled_departure if schedule_info else None,
                "scheduled_arrival": schedule_info.scheduled_arrival if schedule_info else None,
                "actual_departure": schedule_info.actual_departure if schedule_info else None,
                "actual_arrival": schedule_info.actual_arrival if schedule_info else None,
            },
            "aircraft_info": {
                "icao_address": aircraft_info.icao_address if aircraft_info else "",
                "reg": aircraft_info.reg if aircraft_info else "",
                "typecode": aircraft_info.type if aircraft_info else "",
            },
            "flight_progress": {
                "traversed_distance": flight_progress.traversed_distance if flight_progress else 0,
                "remaining_distance": flight_progress.remaining_distance if flight_progress else 0,
                "elapsed_time": flight_progress.elapsed_time if flight_progress else 0,
                "remaining_time": flight_progress.remaining_time if flight_progress else 0,
                "eta": flight_progress.eta if flight_progress else 0,
                "great_circle_distance": flight_progress.great_circle_distance if flight_progress else 0,
            },
            "aircraft_details": {
                "airline_name": "",
            },
            "flight_plan": {
                "origin": {},
                "destination": {},
            },
        }

        # Parse flight plan for origin/destination coordinates
        if flight_plan:
            if hasattr(flight_plan, "origin") and flight_plan.origin:
                fp_origin = flight_plan.origin
                compat["airport"]["origin"] = {
                    "position": {
                        "latitude": fp_origin.lat if hasattr(fp_origin, "lat") else 0,
                        "longitude": fp_origin.lon if hasattr(fp_origin, "lon") else 0,
                    },
                    "code": {
                        "iata": fp_origin.iata if hasattr(fp_origin, "iata") else "",
                    },
                }
                compat["flight_plan"]["origin"] = {
                    "lat": fp_origin.lat if hasattr(fp_origin, "lat") else 0,
                    "lon": fp_origin.lon if hasattr(fp_origin, "lon") else 0,
                    "iata": fp_origin.iata if hasattr(fp_origin, "iata") else "",
                }

            if hasattr(flight_plan, "destination") and flight_plan.destination:
                fp_dest = flight_plan.destination
                compat["airport"]["destination"] = {
                    "position": {
                        "latitude": fp_dest.lat if hasattr(fp_dest, "lat") else 0,
                        "longitude": fp_dest.lon if hasattr(fp_dest, "lon") else 0,
                    },
                    "code": {
                        "iata": fp_dest.iata if hasattr(fp_dest, "iata") else "",
                    },
                }
                compat["flight_plan"]["destination"] = {
                    "lat": fp_dest.lat if hasattr(fp_dest, "lat") else 0,
                    "lon": fp_dest.lon if hasattr(fp_dest, "lon") else 0,
                    "iata": fp_dest.iata if hasattr(fp_dest, "iata") else "",
                }

        # Aircraft details (airline name)
        if aircraft_details_proto and hasattr(aircraft_details_proto, "airline_name"):
            compat["aircraft_details"]["airline_name"] = aircraft_details_proto.airline_name or ""

        # Trail points
        if hasattr(proto, "flight_trail_list"):
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
