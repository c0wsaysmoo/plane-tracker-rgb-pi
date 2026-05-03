from utilities.animator import Animator
from setup import colours, fonts, screen
from config import DISTANCE_UNITS
from rgbmatrix import graphics

# Optional SPEED_UNITS config — defaults to knots if not set
try:
    from config import SPEED_UNITS
except (ImportError, ModuleNotFoundError, NameError):
    SPEED_UNITS = "knots"

LINE3_Y = 31
FONT = fonts.small

# Time remaining + distance remaining
TIME_DIST_COLOUR = colours.LIGHT_MID_BLUE

# Aircraft type, altitude, speed
STATS_COLOUR    = colours.LIGHT_PINK
LABEL_COLOUR    = colours.LIGHT_PINK
AIRCRAFT_COLOUR = colours.LIGHT_PINK


def _format_altitude(altitude):
    if not altitude:
        return None, None
    if DISTANCE_UNITS == "metric":
        metres = int(altitude * 0.3048)
        return str(metres), "m"
    else:
        return str(int(altitude)), "ft"


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


def _build_stats(data):
    """
    Build list of (text, colour) tuples for the stats line.
    Format: 1:23 234mi B738 35000ft^ 260mph
    """
    parts = []

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

    # Aircraft type
    aircraft = data.get("aircraft_type", "")
    if aircraft and aircraft not in ("", "N/A"):
        for ch in aircraft:
            parts.append((ch, AIRCRAFT_COLOUR))
        parts.append((" ", STATS_COLOUR))

    # Altitude + vertical speed arrow (no space between value and unit)
    alt_val, alt_unit = _format_altitude(data.get("altitude"))
    if alt_val:
        for ch in alt_val:
            parts.append((ch, STATS_COLOUR))
        for ch in alt_unit:
            parts.append((ch, STATS_COLOUR))
        # Vertical speed arrow immediately after unit
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
