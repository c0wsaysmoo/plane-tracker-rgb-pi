from datetime import datetime
import colorsys
from rgbmatrix import graphics
from utilities.animator import Animator
from setup import colours, fonts, frames, screen
from utilities.temperature import grab_temperature_and_humidity
from config import NIGHT_START, NIGHT_END

# Scene Setup
TEMPERATURE_REFRESH_SECONDS = 600
TEMPERATURE_FONT = fonts.small
TEMPERATURE_FONT_HEIGHT = 6
NIGHT_START_TIME = datetime.strptime(NIGHT_START, "%H:%M")
NIGHT_END_TIME = datetime.strptime(NIGHT_END, "%H:%M")

class TemperatureScene(object):
    def __init__(self):
        super().__init__()
        self._last_temperature = None
        self._last_temperature_str = None
        self._last_updated = None
        self._cached_temp = None
        self._cached_humidity = None
        self._redraw_temp = True

    def colour_gradient(self, colour_A, colour_B, ratio):
        return graphics.Color(
            colour_A.red + ((colour_B.red - colour_A.red) * ratio),
            colour_A.green + ((colour_B.green - colour_A.green) * ratio),
            colour_A.blue + ((colour_B.blue - colour_A.blue) * ratio),
            )

        return graphics.Color(int(r), int(g), int(b))

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def temperature(self, count):
        #redraws the screen at night start and end so it'll adjust the brightness
        now = datetime.now().replace(microsecond=0).time()
        if now == NIGHT_START_TIME.time() or now == NIGHT_END_TIME.time():
            self._redraw_temp = True
            return  
            
        # Ensure redraw when there's new data
        if len(self._data):
            self._redraw_temp = True
            return

        seconds_since_update = (datetime.now() - self._last_updated).seconds if self._last_updated is not None else 0
        if not (seconds_since_update % TEMPERATURE_REFRESH_SECONDS) or self._redraw_temp:
            if self._cached_temp is not None and self._redraw_temp:
                current_temperature, current_humidity = self._cached_temp
            else:
                self._cached_temp = current_temperature, current_humidity = grab_temperature_and_humidity()
                self._last_updated = datetime.now()
                
              # Undraw old temperature
            if self._last_temperature_str is not None:
                self.draw_square(
                    40,  # x-coordinate of top-left corner
                    0,   # y-coordinate of top-left corner
                    64,  # width of the rectangle
                    5,   # height of the rectangle
                    colours.BLACK,  # Use background color to clear the area
                )
                
            if current_temperature is not None:
                self._last_temperature_str = f"{round(current_temperature)}°"
                self._last_temperature = current_temperature
                self._redraw_temp = False
                
                # Get the humidity ratio (0% -> white, 100% -> blue)
                humidity_ratio = current_humidity / 100.0

                temp_colour = self.colour_gradient(colours.WHITE, colours.DARK_BLUE, humidity_ratio)

                # Calculate the length of the formatted temperature string
                font_character_width = 5
                temperature_string_width = len(self._last_temperature_str) * font_character_width

                # Calculate the starting x-coordinate for centering within the range 40 to 64
                middle_x = (40 + 64) // 2

                # Calculate the starting x-coordinate for centering the temperature string
                start_x = middle_x - temperature_string_width // 2

                # Define and initialize TEMPERATURE_POSITION
                TEMPERATURE_POSITION = (start_x, TEMPERATURE_FONT_HEIGHT)

                # Draw temperature
                _ = graphics.DrawText(
                    self.canvas,
                    TEMPERATURE_FONT,
                    TEMPERATURE_POSITION[0],
                    TEMPERATURE_POSITION[1],
                    temp_colour,
                    self._last_temperature_str,
                )
