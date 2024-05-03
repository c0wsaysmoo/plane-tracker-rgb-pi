from datetime import datetime
from utilities.temperature import grab_forecast
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics
import logging
from config import NIGHT_START, NIGHT_END

# Configure logging
#logging.basicConfig(filename='myapp.log', level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Setup
DATE_FONT = fonts.extrasmall
DATE_POSITION = (40, 11)

# Convert NIGHT_START and NIGHT_END to datetime objects
NIGHT_START_TIME = datetime.strptime(NIGHT_START, "%H:%M")
NIGHT_END_TIME = datetime.strptime(NIGHT_END, "%H:%M")

class DateScene(object):
    def __init__(self):
        super().__init__()
        self._last_date = None
        self.today_moonphase = None
        self.last_fetched_moonphase = None  # Store the date of the last forecast 


    def moonphase(self):
            now = datetime.now()
            
            #print("last fetch is", self.last_fetched_moonphase, "; now day is", now.day)
            if self.last_fetched_moonphase != now.day:
                #print("Fetching forecast data...")
                forecast = grab_forecast()
                for day in forecast:
                    forecast_date = day['startTime'][:10]
                    if forecast_date == now.strftime('%Y-%m-%d'):
                       utc_moonphase = int(day["values"]["moonPhase"])
                       self.today_moonphase = utc_moonphase  # Update moon phase
                       self.last_fetched_moonphase = now.day  # Update the last fetch date
                       #logging.info(f"Fetched forecast data for {forecast_date}, moonphase: {utc_moonphase}")
                       #print(f"Fetched forecast data for {forecast_date}, moonphase: {utc_moonphase}")
                       break 

          #Return the cached moon phase value
            return self.today_moonphase

    def map_moon_phase_to_color(self, moonphase):
        # Define the two colors for the specific moon phases
        colors = [
            [colours.PINK_DARK, colours.PINK_DARK],  # Moon phase 0
            [colours.PINK_DARK, colours.MIDDLE_PURPLE],  # Moon phase 1
            [colours.PINK_DARK, colours.WHITE],  # Moon phase 2
            [colours.MIDDLE_PURPLE, colours.WHITE],  # Moon phase 3
            [colours.GREY, colours.GREY],  # Moon phase 4
            [colours.WHITE, colours.MIDDLE_PURPLE],  # Moon phase 5
            [colours.WHITE, colours.PINK_DARK],  # Moon phase 6
            [colours.MIDDLE_PURPLE, colours.PINK_DARK]  # Moon phase 7
            # Define colors for the remaining phases as needed
        ]

        # Ensure moonphase is within the valid range
        moonphase = min(max(moonphase, 0), 7)

        # Get the corresponding colors for the moon phase
        gradient_start_color, gradient_end_color = colors[moonphase]

        return gradient_start_color, gradient_end_color  # Return both colors

    def draw_gradient_text(self, text, x, y, start_color, end_color):
        text_length = len(text)
        char_width = 4  # Width of each character
        for i, char in enumerate(text):
            position = i / (text_length - 1)
            r = int(start_color.red + (end_color.red - start_color.red) * position)
            g = int(start_color.green + (end_color.green - start_color.green) * position)
            b = int(start_color.blue + (end_color.blue - start_color.blue) * position)
            char_color = graphics.Color(r, g, b)
            char_x = x + (i * char_width)
            _ = graphics.DrawText(
                self.canvas,
                DATE_FONT,
                char_x,
                y,
                char_color,
                char,
            )

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def date(self, count):
        #redraws the screen at night start and end so it'll adjust the brightness
        now = datetime.now().replace(microsecond=0).time()
        if now == NIGHT_START_TIME.time() or now == NIGHT_END_TIME.time():
            self._last_date = None
            return
    
        if len(self._data):
            # Ensure redraw when there's new data
            self._last_date = None
        else:
            # If there's no data to display
            # then draw the date
            now = datetime.now()
            current_date = now.strftime("%b %d")

            # Get the moon phase colors based on the current moon phase
            start_color, end_color = self.map_moon_phase_to_color(self.moonphase())

            # Only draw if the date needs updating
            if self._last_date != current_date:
                # Undraw the last date if different from the current date
                if not self._last_date is None:
                    _ = graphics.DrawText(
                        self.canvas,
                        DATE_FONT,
                        DATE_POSITION[0],
                        DATE_POSITION[1],
                        colours.BLACK,
                        self._last_date,
                    )
                self._last_date = current_date

                # Draw the date with a gradient color
                self.draw_gradient_text(current_date, DATE_POSITION[0], DATE_POSITION[1], start_color, end_color)
