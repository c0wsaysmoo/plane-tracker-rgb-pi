from rgbmatrix import graphics
from utilities.animator import Animator
from setup import colours, fonts, screen
from config import DISTANCE_UNITS

# Setup
PLANE_COLOUR          = colours.LIGHT_MID_BLUE
PLANE_DISTANCE_COLOUR = colours.LIGHT_PINK
ALTITUDE_COLOUR       = colours.LIGHT_PINK
CLIMB_COLOUR          = colours.LIGHT_GREEN
DESCEND_COLOUR        = colours.LIGHT_LIGHT_RED
PLANE_DISTANCE_FROM_TOP = 31
PLANE_TEXT_HEIGHT = 6
PLANE_FONT = fonts.small


def _format_altitude(altitude):
    """Match trackedstats logic: FL above 18,000ft, ft below, metres if metric."""
    if not altitude:
        return None, None
    altitude = int(altitude)
    if DISTANCE_UNITS == "metric":
        metres = int(altitude * 0.3048)
        return str(metres), "m"
    if altitude >= 18000:
        return f"FL{altitude // 100}", ""
    return f"{altitude:,}", "ft"


def _build_char_list(plane_name, distance, direction, altitude, vertical_speed):
    """Build a list of (char, colour) tuples for the full scrolling line."""
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
        for ch in f" @{alt_val}":
            parts.append((ch, ALTITUDE_COLOUR))
        for ch in alt_unit:
            parts.append((ch, ALTITUDE_COLOUR))
        # Vertical speed arrow (same thresholds as trackedstats.py)
        vs = vertical_speed or 0
        if vs > 64:
            parts.append(("\u2191", CLIMB_COLOUR))    # ↑ green
        elif vs < -64:
            parts.append(("\u2193", DESCEND_COLOUR))  # ↓ red

    return parts


class PlaneDetailsScene(object):
    def __init__(self):
        super().__init__()
        self.plane_position = screen.WIDTH
        self.plane_details_complete = False
        self._data_all_looped = False

    @Animator.KeyFrame.add(1)
    def plane_details(self, count):
        # Guard against no data
        if len(self._data) == 0:
            return

        # Skip rendering after scroll complete (waiting for other regions)
        if self.plane_details_complete:
            return

        # Extract data
        plane_data = self._data[self._data_index]
        plane_name    = plane_data["plane"]
        distance      = plane_data["distance"]
        direction     = plane_data["direction"]
        altitude      = plane_data.get("altitude", 0)
        vertical_speed = plane_data.get("vertical_speed", 0)

        char_list = _build_char_list(plane_name, distance, direction, altitude, vertical_speed)

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
                self.plane_position + total_text_width,
                PLANE_DISTANCE_FROM_TOP,
                colour,
                ch,
            )
            total_text_width += w

        # Handle scrolling
        self.plane_position -= 1

        # Mark scroll complete when text scrolls off (wait for other regions)
        if self.plane_position + total_text_width < 0:
            if len(self._data) > 1:
                self.plane_details_complete = True
                self.mark_scroll_complete("plane_details")
            else:
                self.plane_position = screen.WIDTH

    @Animator.KeyFrame.add(0)
    def reset_plane_details_scroll(self):
        self.plane_position = screen.WIDTH
        self.plane_details_complete = False
