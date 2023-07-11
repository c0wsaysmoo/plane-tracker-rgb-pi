from datetime import datetime, timedelta
from PIL import Image

from utilities.animator import Animator
from setup import colours, fonts, frames, screen
from utilities.temperature import grab_forecast

from rgbmatrix import graphics

# Setup
DAY_COLOUR = colours.GREY
MIN_T_COLOUR = colours.BLUE_MEDIUM
MAX_T_COLOUR = colours.ORANGE
TEXT_FONT = fonts.extrasmall
FONT_HEIGHT = 5
DISTANCE_FROM_TOP = 32
ICON_SIZE = 10
FORECAST_SIZE = FONT_HEIGHT * 2 + ICON_SIZE

DAY_POSITION = DISTANCE_FROM_TOP - FONT_HEIGHT - ICON_SIZE
ICON_POSITION = DISTANCE_FROM_TOP - FONT_HEIGHT - ICON_SIZE
TEMP_POSITION = DISTANCE_FROM_TOP


class DaysForecastScene(object):
    def __init__(self):
        super().__init__()
        self._redraw_forecast = True
        self._last_hour = None
        self._cached_forecast = None

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def day(self, count):
        # Ensure redraw when there's new data
        if len(self._data):
            self._redraw_forecast = True
            return

        # If there's no data to display
        # then draw the day
        current_hour = datetime.now().hour

        # Only draw if time needs updated
        if self._last_hour != current_hour or self._redraw_forecast:
            # Clear space if last day is different from current
            if self._last_hour is not None:
                self.draw_square(
                    0,
                    DISTANCE_FROM_TOP - FORECAST_SIZE,
                    screen.WIDTH,
                    FORECAST_SIZE,
                    colours.BLACK,
                )
            self._last_hour = current_hour

            if self._cached_forecast is not None and self._redraw_forecast:
                forecast = self._cached_forecast
            else:
                forecast = grab_forecast()
                self._cached_forecast = forecast

            if forecast is not None:
                self._redraw_forecast = False
                offset = 0
                for day in forecast:
                    # Clear previous temperature values
                    self.draw_square(
                        offset + 2,
                        TEMP_POSITION - FONT_HEIGHT,
                        offset + 22,
                        TEMP_POSITION + FONT_HEIGHT,
                        colours.BLACK
                    )

                    # Draw day
                    _ = graphics.DrawText(
                        self.canvas,
                        TEXT_FONT,
                        offset + 5,
                        DAY_POSITION,
                        DAY_COLOUR,
                        datetime.fromisoformat(day["startTime"].rstrip("Z")).strftime("%a")
                    )

                    # Draw the icon
                    icon = day["values"]["weatherCode"]
                    image = Image.open(f"icons/{icon}.png")

                    # Make image fit our screen.
                    image.thumbnail((ICON_SIZE, ICON_SIZE), Image.ANTIALIAS)
                    self.matrix.SetImage(image.convert('RGB'), offset + 5, ICON_POSITION)

                    # Draw min temperature
                    _ = graphics.DrawText(
                        self.canvas,
                        TEXT_FONT,
                        offset + 11,
                        TEMP_POSITION,
                        MIN_T_COLOUR,
                        f"{day['values']['temperatureMin']:.0f}"
                    )
                    # Draw max temperature
                    _ = graphics.DrawText(
                        self.canvas,
                        TEXT_FONT,
                        offset + 2,
                        TEMP_POSITION,
                        MAX_T_COLOUR,
                        f"{day['values']['temperatureMax']:.0f}"
                    )
                    offset += 22
