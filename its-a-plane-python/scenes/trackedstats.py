from utilities.animator import Animator
from utilities.cities import get_nearest_city
from setup import colours, fonts, screen
from config import DISTANCE_UNITS
from rgbmatrix import graphics

# Optional configs — defaults for when not set
try:
    from config import SPEED_UNITS
except (ImportError, ModuleNotFoundError, NameError):
    SPEED_UNITS = "knots"

try:
    from config import CLOCK_FORMAT
except (ImportError, ModuleNotFoundError, NameError):
    CLOCK_FORMAT = "24hr"

LINE3_Y = 31
FONT = fonts.small

# Time remaining + distance remaining
TIME_DIST_COLOUR = colours.LIGHT_MID_BLUE

# Aircraft type, altitude, speed
STATS_COLOUR    = colours.LIGHT_PINK
AIRCRAFT_COLOUR = colours.LIGHT_PINK
CITY_COLOUR     = colours.WHITE

# Cache nearest city result — only recalculate when position changes significantly
_city_cache = {"lat": None, "lon": None, "result": None}
_CITY_CACHE_THRESHOLD = 0.01  # ~1km — recalculate when plane moves this far


def _format_altitude(altitude):
    """Format altitude as flight level (FL350) or raw feet below 1000ft."""
    if not altitude:
        return None
    altitude = int(altitude)
    if altitude >= 1000:
        fl = altitude // 100
        return f"FL{fl:03d}"
    else:
        return f"{altitude}ft"


def _format_speed(ground_speed):
    if not ground_speed:
        return None, None
    if SPEED_UNITS == "imperial":
        mph = ground_speed * 1.15078
        return f"{int(mph)}", "mph"
    elif SPEED_UNITS == "metric":
        kph = ground_speed * 1.852
        return f"{int(kph)}", "km/h"
    else:  # knots default
        return f"{int(ground_speed)}", "knts"


def _format_dep_time(dep_time_str):
    """Format departure time from '2026-05-11 18:30'. Respects CLOCK_FORMAT."""
    if not dep_time_str:
        return ""
    try:
        parts = dep_time_str.split(" ")
        if len(parts) < 2:
            return dep_time_str
        hm = parts[1].split(":")
        hour = int(hm[0])
        minute = int(hm[1]) if len(hm) > 1 else 0

        if CLOCK_FORMAT == "12hr":
            ampm = "a" if hour < 12 else "p"
            display_hour = hour % 12 or 12
            if minute:
                return f"{display_hour}:{minute:02d}{ampm}"
            return f"{display_hour}{ampm}"
        else:
            return f"{hour}:{minute:02d}"
    except (ValueError, IndexError):
        return dep_time_str


def _build_stats(data):
    """
    Build list of (text, colour) tuples for the stats line.
    Live:      1:23 234mi nr Atlanta B738 FL350↑ 260mph
    Scheduled: Departs 6:30p EWR→LAX
    """
    parts = []

    # Scheduled (pre-departure) — show departure info instead of live stats
    if data.get("is_scheduled"):
        dep = _format_dep_time(data.get("dep_time", ""))
        origin = data.get("origin", "")
        dest = data.get("destination", "")
        label = f"Departs {dep} {origin}\u2192{dest}" if dep else f"Scheduled {origin}\u2192{dest}"
        for ch in label:
            parts.append((ch, TIME_DIST_COLOUR))
        return parts

    # Time remaining
    if data.get("time_remaining"):
        for ch in data["time_remaining"]:
            parts.append((ch, TIME_DIST_COLOUR))
        parts.append((" ", STATS_COLOUR))

    # Distance remaining
    if data.get("dist_remaining") is not None:
        unit = "km" if DISTANCE_UNITS == "metric" else "mi"
        dist_str = f"{int(data['dist_remaining'])}{unit}"
        for ch in dist_str:
            parts.append((ch, TIME_DIST_COLOUR))
        parts.append((" ", STATS_COLOUR))

    # Nearest city (cached — only recalculate when position changes)
    lat = data.get("latitude")
    lon = data.get("longitude")
    if lat is not None and lon is not None:
        if (_city_cache["lat"] is None
                or abs(lat - _city_cache["lat"]) > _CITY_CACHE_THRESHOLD
                or abs(lon - _city_cache["lon"]) > _CITY_CACHE_THRESHOLD):
            _city_cache["lat"] = lat
            _city_cache["lon"] = lon
            _city_cache["result"] = get_nearest_city(lat, lon)
        nearest = _city_cache["result"]
        if nearest:
            for ch in f"nr {nearest['name']}":
                parts.append((ch, CITY_COLOUR))
            parts.append((" ", STATS_COLOUR))

    # Aircraft type
    aircraft = data.get("aircraft_type", "")
    if aircraft and aircraft not in ("", "N/A"):
        for ch in aircraft:
            parts.append((ch, AIRCRAFT_COLOUR))
        parts.append((" ", STATS_COLOUR))

    # Altitude + vertical speed arrow
    alt_str = _format_altitude(data.get("altitude"))
    if alt_str:
        for ch in alt_str:
            parts.append((ch, STATS_COLOUR))
        # Vertical speed arrow immediately after
        vs = data.get("vertical_speed", 0) or 0
        if vs > 64:
            parts.append(("\u2191", colours.LIGHT_GREEN))
        elif vs < -64:
            parts.append(("\u2193", colours.LIGHT_LIGHT_RED))
        parts.append((" ", STATS_COLOUR))

    # Speed (no space between value and unit)
    spd_val, spd_unit = _format_speed(data.get("ground_speed"))
    if spd_val:
        for ch in spd_val:
            parts.append((ch, STATS_COLOUR))
        for ch in spd_unit:
            parts.append((ch, STATS_COLOUR))

    return parts


class TrackedStatsScene(object):
    def __init__(self):
        super().__init__()
        self._ts_pos = screen.WIDTH
        self._ts_len = 0

    @Animator.KeyFrame.add(0)
    def reset_tracked_stats_scroll(self):
        self._ts_pos = screen.WIDTH
        self._ts_len = 0

    @Animator.KeyFrame.add(1)
    def tracked_stats(self, count):
        if len(self._data) > 0:
            return

        tracked = self.overhead.tracked_data
        if not tracked:
            return

        char_list = _build_stats(tracked)

        # Clear row
        self.draw_square(0, LINE3_Y - 6, screen.WIDTH, LINE3_Y, colours.BLACK)

        # Draw scrolling text
        total_len = 0
        for ch, colour in char_list:
            w = graphics.DrawText(
                self.canvas, FONT,
                self._ts_pos + total_len, LINE3_Y,
                colour, ch,
            )
            total_len += w
        self._ts_len = total_len

        self._ts_pos -= 1

        if self._ts_pos + self._ts_len < 0:
            self._ts_pos = screen.WIDTH
