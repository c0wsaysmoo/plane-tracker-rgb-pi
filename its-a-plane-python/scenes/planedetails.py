from rgbmatrix import graphics
from utilities.animator import Animator
from setup import colours, fonts, screen
from config import DISTANCE_UNITS

# Setup
PLANE_COLOUR = colours.LIGHT_MID_BLUE
PLANE_DISTANCE_COLOUR = colours.LIGHT_PINK
HEADING_ARROW_COLOUR = colours.LIGHT_GREEN
PLANE_DISTANCE_FROM_TOP = 31
PLANE_TEXT_HEIGHT = 6
PLANE_FONT = fonts.small

# 8-point compass heading arrows (N=0/360, clockwise)
_HEADING_ARROWS = [
    (337.5, 360, "\u2191"),  # N  ↑
    (0, 22.5, "\u2191"),     # N  ↑
    (22.5, 67.5, "\u2197"),  # NE ↗
    (67.5, 112.5, "\u2192"), # E  →
    (112.5, 157.5, "\u2198"),# SE ↘
    (157.5, 202.5, "\u2193"),# S  ↓
    (202.5, 247.5, "\u2199"),# SW ↙
    (247.5, 292.5, "\u2190"),# W  ←
    (292.5, 337.5, "\u2196"),# NW ↖
]


def _heading_to_arrow(heading):
    """Convert numeric heading (0-360) to Unicode arrow character."""
    if heading is None:
        return ""
    heading = heading % 360
    for lo, hi, arrow in _HEADING_ARROWS:
        if lo <= heading < hi:
            return arrow
    return ""


class PlaneDetailsScene(object):
    def __init__(self):
        super().__init__()
        self.plane_position = screen.WIDTH
        self.plane_details_complete = False

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
        plane_name = plane_data["plane"]
        distance = plane_data["distance"]
        direction = plane_data["direction"]
        distance_units = "mi" if DISTANCE_UNITS == "imperial" else "KM"

        # Heading arrow
        heading = plane_data.get("heading", 0)
        arrow = _heading_to_arrow(heading)

        # Construct the plane details strings
        plane_name_text = f'{plane_name} '
        distance_text = f'{distance:.2f}{distance_units} {direction}'

        # Draw background
        self.draw_square(
            0,
            PLANE_DISTANCE_FROM_TOP - PLANE_TEXT_HEIGHT,
            screen.WIDTH,
            screen.HEIGHT,
            colours.BLACK,
        )

        # Draw text with different colors for plane name and distance/direction
        plane_name_width = graphics.DrawText(
            self.canvas,
            PLANE_FONT,
            self.plane_position,
            PLANE_DISTANCE_FROM_TOP,
            PLANE_COLOUR,  # Set the color for the plane name
            plane_name_text,
        )

        distance_text_width = graphics.DrawText(
            self.canvas,
            PLANE_FONT,
            self.plane_position + plane_name_width,
            PLANE_DISTANCE_FROM_TOP,
            PLANE_DISTANCE_COLOUR,  # Set the color for distance/direction
            distance_text,
        )

        # Draw heading arrow in distinct color
        arrow_width = 0
        if arrow:
            arrow_width = graphics.DrawText(
                self.canvas,
                PLANE_FONT,
                self.plane_position + plane_name_width + distance_text_width,
                PLANE_DISTANCE_FROM_TOP,
                HEADING_ARROW_COLOUR,
                arrow,
            )

        # Calculate the total width of the text string
        total_text_width = plane_name_width + distance_text_width + arrow_width

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
