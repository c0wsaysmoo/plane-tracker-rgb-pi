from rgbmatrix import graphics
from utilities.animator import Animator
from setup import colours, fonts, screen
from config import DISTANCE_UNITS

# Setup
PLANE_COLOUR = colours.LIGHT_MID_BLUE
PLANE_DISTANCE_COLOUR = colours.LIGHT_PINK
ALTITUDE_COLOUR = colours.LIGHT_PINK
CLIMB_COLOUR = colours.LIGHT_GREEN
DESCEND_COLOUR = colours.LIGHT_LIGHT_RED
PLANE_DISTANCE_FROM_TOP = 31
PLANE_TEXT_HEIGHT = 6
PLANE_FONT = fonts.small

# 8-point compass heading arrows (N=0/360, clockwise)
# Concept from c0wsaysmoo/plane-tracker-rgb-pi
_HEADING_ARROWS = ["\u2191", "\u2197", "\u2192", "\u2198", "\u2193", "\u2199", "\u2190", "\u2196"]


def _heading_to_arrow(heading):
    """Convert numeric heading (0-360) to Unicode arrow character."""
    if heading is None:
        return ""
    try:
        return _HEADING_ARROWS[int((float(heading) + 22.5) / 45) % 8]
    except (TypeError, ValueError):
        return ""


def _format_altitude(altitude):
    """FL above 18,000ft, comma-formatted feet below, metres if metric."""
    if not altitude:
        return None, None
    altitude = int(altitude)
    if DISTANCE_UNITS == "metric":
        metres = int(altitude * 0.3048)
        return str(metres), "m"
    if altitude >= 18000:
        return f"FL{altitude // 100}", ""
    return f"{altitude:,}", "ft"


def _build_char_list(plane_name, distance, direction, altitude, vertical_speed, heading):
    """Build a list of (char, colour) tuples for the full scrolling line.
    Concept from c0wsaysmoo/plane-tracker-rgb-pi."""
    parts = []

    if DISTANCE_UNITS == "imperial":
        dist_unit = "mi"
    elif DISTANCE_UNITS == "metric":
        dist_unit = "km"
    else:
        dist_unit = "nm"

    # Plane name
    for ch in f"{plane_name} ":
        parts.append((ch, PLANE_COLOUR))

    # Distance + direction
    for ch in f"{distance:.2f}{dist_unit} {direction}":
        parts.append((ch, PLANE_DISTANCE_COLOUR))

    # Altitude
    alt_val, alt_unit = _format_altitude(altitude)
    if alt_val:
        for ch in f" @ {alt_val}":
            parts.append((ch, ALTITUDE_COLOUR))
        for ch in alt_unit:
            parts.append((ch, ALTITUDE_COLOUR))
        # Vertical speed arrow (same thresholds as trackedstats.py)
        vs = vertical_speed or 0
        if vs > 64:
            parts.append(("\u2191", CLIMB_COLOUR))
        elif vs < -64:
            parts.append(("\u2193", DESCEND_COLOUR))

    # Heading arrow
    if heading is not None:
        arrow = _heading_to_arrow(heading)
        if arrow:
            parts.append((" ", PLANE_DISTANCE_COLOUR))
            parts.append((arrow, PLANE_DISTANCE_COLOUR))

    return parts


class PlaneDetailsScene(object):
    def __init__(self):
        super().__init__()

    @Animator.KeyFrame.add(1)
    def plane_details(self, count):
        # Guard against no data
        if len(self._data) == 0:
            return

        # Extract data
        plane_data = self._data[self._data_index]
        plane_name = plane_data["plane"]
        distance = plane_data["distance"]
        direction = plane_data["direction"]
        altitude = plane_data.get("altitude", 0)
        vertical_speed = plane_data.get("vertical_speed", 0)
        heading = plane_data.get("heading")

        char_list = _build_char_list(plane_name, distance, direction, altitude, vertical_speed, heading)

        # Draw background
        self.draw_square(
            0,
            PLANE_DISTANCE_FROM_TOP - PLANE_TEXT_HEIGHT,
            screen.WIDTH,
            screen.HEIGHT,
            colours.BLACK,
        )

        # Draw each character at its scrolling position
        total_text_width = 0
        for ch, colour in char_list:
            w = graphics.DrawText(
                self.canvas,
                PLANE_FONT,
                self._scroll_pos + total_text_width,
                PLANE_DISTANCE_FROM_TOP,
                colour,
                ch,
            )
            total_text_width += w

        # Report width to shared scroll driver
        self.report_scroll_width("plane_details", total_text_width)

    @Animator.KeyFrame.add(0)
    def reset_plane_details_scroll(self):
        pass  # Called by reset_scene(); scroll position owned by Display._scroll_pos
