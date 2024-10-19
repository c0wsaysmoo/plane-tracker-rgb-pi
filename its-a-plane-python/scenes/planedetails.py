from rgbmatrix import graphics
from utilities.animator import Animator
from setup import colours, fonts, screen
from config import DISTANCE_UNITS

# Setup
PLANE_COLOUR = colours.LIGHT_MID_BLUE
PLANE_DISTANCE_COLOUR = colours.LIGHT_PINK
PLANE_DISTANCE_FROM_TOP = 31
PLANE_TEXT_HEIGHT = 6
PLANE_FONT = fonts.small

class PlaneDetailsScene(object):
    def __init__(self):
        super().__init__()
        self.plane_position = screen.WIDTH
        self._data_all_looped = False

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

        # Construct the plane details strings
        plane_name_text = f'{plane_name} '
        distance_text = f'{distance:.2f}{distance_units} {direction}'

        # Calculate the widths of each section
        plane_name_width = len(plane_name_text) * 5
        distance_direction_text_width = max(len(distance_text) * 5, screen.WIDTH)

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

        # Calculate the total width of the text string
        total_text_width = plane_name_width + distance_text_width

        # Handle scrolling
        self.plane_position -= 1

        # Check if the text has completely scrolled off the screen
        if self.plane_position + total_text_width < 0:
            self.plane_position = screen.WIDTH
            if len(self._data) > 1:
                self._data_index = (self._data_index + 1) % len(self._data)
                self._data_all_looped = (not self._data_index) or self._data_all_looped 
                self.reset_scene()

    @Animator.KeyFrame.add(0)
    def reset_scrolling(self):
        self.plane_position = screen.WIDTH
