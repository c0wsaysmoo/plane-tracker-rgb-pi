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
        self._last_hour = None

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def day(self, count):
        if len(self._data):
            # Ensure redraw when there's new data
            self._last_hour = None

        else:
            # If there's no data to display
            # then draw the day
            current_hour = datetime.now().hour

            # Only draw if time needs updated
            if self._last_hour != current_hour:
                # Clear space if last day is different from current
                if not self._last_hour is None:
                    self.draw_square(
                        0,
                        DISTANCE_FROM_TOP - FORECAST_SIZE,
                        screen.WIDTH,
                        FORECAST_SIZE,
                        colours.BLACK,
                    )
                self._last_hour = current_hour

                forecast = grab_forecast()
                
                if forecast is not None:
                    offset = 0
                    for forecastday in forecast["forecastday"]: 
                        
                        # Draw day
                        _ = graphics.DrawText(
                            self.canvas,
                            TEXT_FONT,
                            offset+5,
                            DAY_POSITION,
                            DAY_COLOUR,
                            datetime.fromisoformat(forecastday["date"]).strftime("%a")
                        )
                        #print(forecastday)

                        # Draw the icon
                        icon = forecastday["day"]["condition"]["icon"].split("/")[-1].split(".")[0]
                        image = Image.open(f"icons/{icon}.png")

                        # Make image fit our screen.
                        image.thumbnail((ICON_SIZE, ICON_SIZE), Image.ANTIALIAS)
                        self.matrix.SetImage(image.convert('RGB'), offset+5, ICON_POSITION)

                        # Draw min temperature
                        _ = graphics.DrawText(
                            self.canvas,
                            TEXT_FONT,
                            offset+11,
                            TEMP_POSITION,
                            MIN_T_COLOUR,
                            f"{forecastday['day']['mintemp_f']:.0f}"
                        )
                        # Draw max temperature
                        _ = graphics.DrawText(
                            self.canvas,
                            TEXT_FONT,
                            offset+2,
                            TEMP_POSITION,
                            MAX_T_COLOUR,
                            f"{forecastday['day']['maxtemp_f']:.0f}"
                        )
                        offset += 22