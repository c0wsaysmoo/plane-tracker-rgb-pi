from datetime import datetime
from utilities.temperature import grab_forecast
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics
import logging
from config import CLOCK_FORMAT, NIGHT_END, NIGHT_START

# Configure logging
#logging.basicConfig(filename='myapp.log', level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Setup
CLOCK_FONT = fonts.large_bold
CLOCK_POSITION = (0, 11)
DAY_COLOUR = colours.LIGHT_ORANGE
NIGHT_COLOUR = colours.LIGHT_BLUE


# Convert NIGHT_START and NIGHT_END to datetime objects 
NIGHT_START_TIME = datetime.strptime(NIGHT_START, "%H:%M") 
NIGHT_END_TIME = datetime.strptime(NIGHT_END, "%H:%M")

class ClockScene(object):
    def __init__(self):
        super().__init__()
        self._last_time = None
        self.today_sunrise = None
        self.today_sunset = None
        self.last_fetch_date = None  # Store the date of the last forecast fetch

    def calculate_sunrise_sunset(self):
        now = datetime.now()

        try:
            # Only fetch forecast if it's a new day or if no cached data
            if self.last_fetch_date != now.date():
                forecast = grab_forecast(tag="ClockScene")
                if not forecast:  # None or empty list
                    logging.error("Forecast data missing or API error.")
                    return None, None

                for day in forecast:
                    forecast_date = day['startTime'][:10]
                    if forecast_date == now.strftime('%Y-%m-%d'):
                        # Parse UTC sunrise and sunset times
                        utc_sunrise = datetime.strptime(day['values']['sunriseTime'], '%Y-%m-%dT%H:%M:%SZ')
                        utc_sunset = datetime.strptime(day['values']['sunsetTime'], '%Y-%m-%dT%H:%M:%SZ')

                        # Cache values
                        self.today_sunrise = utc_sunrise
                        self.today_sunset = utc_sunset
                        self.last_fetch_date = now.date()

        except Exception as e:
            logging.error(f"Error fetching forecast: {e}")
            return None, None

        return self.today_sunrise, self.today_sunset

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def clock(self, count):
        if len(self._data):
            self._redraw_time = True
            return

        now = datetime.now()
        clock_format = "%l:%M" if CLOCK_FORMAT == "12hr" else "%H:%M"
        current_time = now.strftime(clock_format)

        utc_sunrise, utc_sunset = self.calculate_sunrise_sunset()
        now_utc = datetime.utcnow()

        if utc_sunrise is None or utc_sunset is None:
            clock_color = colours.RED
        elif utc_sunrise <= now_utc < utc_sunset:
            clock_color = DAY_COLOUR
        else:
            clock_color = NIGHT_COLOUR

        if self._last_time and (self._last_time != current_time or getattr(self, "_redraw_time", False)):
            graphics.DrawText(
                self.canvas,
                CLOCK_FONT,
                CLOCK_POSITION[0],
                CLOCK_POSITION[1],
                colours.BLACK,
                self._last_time,
            )

        self._last_time = current_time

        graphics.DrawText(
            self.canvas,
            CLOCK_FONT,
            CLOCK_POSITION[0],
            CLOCK_POSITION[1],
            clock_color,
            current_time,
        )

        self._redraw_time = False