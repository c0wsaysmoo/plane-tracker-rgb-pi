from datetime import datetime, timedelta
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
            int(colour_A.red + ((colour_B.red - colour_A.red) * ratio)),
            int(colour_A.green + ((colour_B.green - colour_A.green) * ratio)),
            int(colour_A.blue + ((colour_B.blue - colour_A.blue) * ratio)),
        )

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def temperature(self, count):
        # Redraw at night start/end to adjust brightness
        now = datetime.now().replace(microsecond=0).time()
        if now == NIGHT_START_TIME.time() or now == NIGHT_END_TIME.time():
            self._redraw_temp = True
            return  

        # Ensure redraw when there's new data
        if len(self._data):
            self._redraw_temp = True
            return

        # Determine seconds since last update
        seconds_since_update = (datetime.now() - self._last_updated).total_seconds() if self._last_updated else TEMPERATURE_REFRESH_SECONDS
        retry_interval_on_error = 60

        # Determine if we need to fetch new data
        need_fetch = (
            seconds_since_update >= TEMPERATURE_REFRESH_SECONDS or
            (self._cached_temp is None and (self._last_updated is None or seconds_since_update >= retry_interval_on_error))
        )

        # Force redraw if switching back to scene and cached temp exists
        force_draw = self._redraw_temp or (self._cached_temp is not None)

        if need_fetch or force_draw:
            # Use cached values if present and not fetching
            if self._cached_temp and not need_fetch:
                current_temperature, current_humidity = self._cached_temp
            else:
                # Fetch new values
                current_temperature, current_humidity = grab_temperature_and_humidity()
                if current_temperature is not None and current_humidity is not None:
                    self._cached_temp = (current_temperature, current_humidity)
                    self._last_updated = datetime.now()
                else:
                    # Keep ERR displayed and schedule retry in 1 minute
                    current_temperature, current_humidity = None, None
                    if self._cached_temp is None:
                        # Adjust _last_updated so next fetch occurs in 1 minute
                        self._last_updated = datetime.now() - timedelta(seconds=TEMPERATURE_REFRESH_SECONDS - retry_interval_on_error)

            # Clear old temperature
            if self._last_temperature_str is not None:
                self.draw_square(
                    40, 0, 64, 5, colours.BLACK
                )

            # Determine display string and color
            if current_temperature is None or current_humidity is None:
                display_str = "ERR"
                temp_colour = colours.RED
            else:
                display_str = f"{round(current_temperature)}°"
                humidity_ratio = current_humidity / 100.0
                temp_colour = self.colour_gradient(colours.WHITE, colours.DARK_BLUE, humidity_ratio)

            # Update state
            self._last_temperature_str = display_str
            self._last_temperature = current_temperature
            self._redraw_temp = False

            # Calculate string position (centered)
            font_character_width = 5
            temperature_string_width = len(display_str) * font_character_width
            middle_x = (40 + 64) // 2
            start_x = middle_x - temperature_string_width // 2
            TEMPERATURE_POSITION = (start_x, TEMPERATURE_FONT_HEIGHT)

            # Draw temperature/error
            graphics.DrawText(
                self.canvas,
                TEMPERATURE_FONT,
                TEMPERATURE_POSITION[0],
                TEMPERATURE_POSITION[1],
                temp_colour,
                display_str,
            )
