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
            self._scroll_pos,
            PLANE_DISTANCE_FROM_TOP,
            PLANE_COLOUR,
            plane_name_text,
        )

        distance_text_width = graphics.DrawText(
            self.canvas,
            PLANE_FONT,
            self._scroll_pos + plane_name_width,
            PLANE_DISTANCE_FROM_TOP,
            PLANE_DISTANCE_COLOUR,
            distance_text,
        )

        # Draw heading arrow in distinct color
        arrow_width = 0
        if arrow:
            arrow_width = graphics.DrawText(
                self.canvas,
                PLANE_FONT,
                self._scroll_pos + plane_name_width + distance_text_width,
                PLANE_DISTANCE_FROM_TOP,
                HEADING_ARROW_COLOUR,
                arrow,
            )

        # Report width to shared scroll driver
        total_text_width = plane_name_width + distance_text_width + arrow_width
        self.report_scroll_width("plane_details", total_text_width)

    @Animator.KeyFrame.add(0)
    def reset_plane_details_scroll(self):
        pass  # Called by reset_scene(); scroll position owned by Display._scroll_pos
