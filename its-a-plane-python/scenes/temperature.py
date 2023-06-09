from datetime import datetime
from rgbmatrix import graphics
from utilities.animator import Animator
from setup import colours, fonts, frames

from utilities.temperature import grab_temperature, TEMPERATURE_MIN, TEMPERATURE_MAX


# Scene Setup
TEMPERATURE_REFRESH_SECONDS = 600
TEMPERATURE_FONT = fonts.small
TEMPERATURE_FONT_HEIGHT = 6
TEMPERATURE_POSITION = (42, TEMPERATURE_FONT_HEIGHT)
TEMPERATURE_MIN_COLOUR = colours.BLUE
TEMPERATURE_MAX_COLOUR = colours.ORANGE


class TemperatureScene(object):
    def __init__(self):
        super().__init__()
        self._last_temperature = None
        self._last_temperature_str = None
        self._last_updated = None
        self._cached_temp = None
        self._redraw_temp = True

    def colour_gradient(self, colour_A, colour_B, ratio):
        return graphics.Color(
            colour_A.red + ((colour_B.red - colour_A.red) * ratio),
            colour_A.green + ((colour_B.green - colour_A.green) * ratio),
            colour_A.blue + ((colour_B.blue - colour_A.blue) * ratio),
        )

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def temperature(self, count):
        # Ensure redraw when there's new data
        if len(self._data):
            self._redraw_temp = True
            return

        seconds_since_update = (datetime.now() - self._last_updated).seconds if self._last_updated is not None else 0
        if not (seconds_since_update % TEMPERATURE_REFRESH_SECONDS) or self._redraw_temp:
            if self._cached_temp is not None and self._redraw_temp:
                current_temperature = self._cached_temp
            else:
                self._cached_temp = current_temperature = grab_temperature()
                self._last_updated = datetime.now()
            
            # Undraw old temperature
            if self._last_temperature_str is not None:
                _ = graphics.DrawText(
                    self.canvas,
                    TEMPERATURE_FONT,
                    TEMPERATURE_POSITION[0],
                    TEMPERATURE_POSITION[1],
                    colours.BLACK,
                    self._last_temperature_str,
                )

            if current_temperature is not None:
                self._last_temperature_str = f"{round(current_temperature)}°".rjust(4, " ")
                self._last_temperature = current_temperature
                self._redraw_temp = False

                if current_temperature > TEMPERATURE_MAX:
                    ratio = 1
                elif current_temperature > TEMPERATURE_MIN:
                    ratio = (current_temperature - TEMPERATURE_MIN) / TEMPERATURE_MAX
                else:
                    ratio = 0

                temp_colour = self.colour_gradient(
                    TEMPERATURE_MIN_COLOUR, TEMPERATURE_MAX_COLOUR, ratio
                )

                # Draw temperature
                _ = graphics.DrawText(
                    self.canvas,
                    TEMPERATURE_FONT,
                    TEMPERATURE_POSITION[0],
                    TEMPERATURE_POSITION[1],
                    temp_colour,
                    self._last_temperature_str,
                )
