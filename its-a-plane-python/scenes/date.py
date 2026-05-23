import logging
from datetime import datetime
from utilities.temperature import grab_forecast
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics

# Setup
DATE_FONT = fonts.extrasmall
DATE_POSITION = (40, 11)

# Tide colors
TIDE_HIGH_COLOUR = graphics.Color(0, 255, 255)     # Cyan
TIDE_LOW_COLOUR = graphics.Color(66, 164, 244)      # Light blue

# Cycle timing: 5 seconds per item (called once per second)
_CYCLE_SECONDS = 5

class DateScene(object):
    def __init__(self):
        super().__init__()
        self._last_date = None
        self._last_display_text = None  # track what's currently drawn for clearing
        self.today_moonphase = None
        self.last_fetched_moonphase = None
        self._cycle_counter = 0  # increments each second
        self._cached_tides = None
        self._tide_fetch_date = None


    def moonphase(self):
        now = datetime.now()

        # Only fetch forecast if it's a new day
        if self.last_fetched_moonphase != now.day:
            try:
                forecast = grab_forecast(tag="DateScene")
                if not forecast:
                    logging.error("Forecast data missing or API error (moon phase).")
                    return self.today_moonphase

                for day in forecast:
                    forecast_date = day['startTime'][:10]
                    if forecast_date == now.strftime('%Y-%m-%d'):
                        utc_moonphase = int(day["values"]["moonPhase"])
                        self.today_moonphase = utc_moonphase
                        self.last_fetched_moonphase = now.day
                        break

            except Exception as e:
                logging.error(f"Error fetching forecast for moon phase: {e}")
                return self.today_moonphase

        return self.today_moonphase

    def map_moon_phase_to_color(self, moonphase):
        colors = [
            [colours.DARK_PURPLE, colours.DARK_PURPLE],
            [colours.DARK_PURPLE, colours.DARK_MID_PURPLE],
            [colours.DARK_PURPLE, colours.WHITE],
            [colours.DARK_MID_PURPLE, colours.WHITE],
            [colours.GREY, colours.GREY],
            [colours.WHITE, colours.DARK_MID_PURPLE],
            [colours.WHITE, colours.DARK_PURPLE],
            [colours.DARK_MID_PURPLE, colours.DARK_PURPLE],
        ]
        moonphase = min(max(moonphase, 0), 7)
        return colors[moonphase]

    def draw_gradient_text(self, text, x, y, start_color, end_color):
        text_length = len(text)
        char_width = 4
        for i, char in enumerate(text):
            position = i / max(1, text_length - 1)
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

    def _get_tides(self):
        """Fetch tide data once per day, cached."""
        today = str(datetime.now().date())
        if self._tide_fetch_date == today and self._cached_tides is not None:
            return self._cached_tides
        try:
            from utilities.tides import get_next_tides
            self._cached_tides = get_next_tides()
            self._tide_fetch_date = today
        except Exception:
            self._cached_tides = None
        return self._cached_tides

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def date(self, count):
        now = datetime.now()
        current_date = now.strftime("%b %d")

        # Flag for forced redraw if new data arrived
        if len(self._data):
            self._redraw_date = True
            return

        # Increment cycle counter
        self._cycle_counter += 1

        # Build display items: date always, tides if available
        tides = self._get_tides()
        items = [("date", current_date)]
        if tides:
            if tides.get("high"):
                items.append(("high", f"H{tides['high']}"))
            if tides.get("low"):
                items.append(("low", f"L{tides['low']}"))

        # Pick current item based on cycle
        cycle_len = len(items) * _CYCLE_SECONDS
        slot = (self._cycle_counter // _CYCLE_SECONDS) % len(items)
        item_type, display_text = items[slot]

        # Get moon phase colors (used for date, neutral for tides)
        moon_phase_value = self.moonphase()
        if moon_phase_value is None:
            start_color = end_color = colours.RED
        else:
            start_color, end_color = self.map_moon_phase_to_color(moon_phase_value)

        # Clear previous text if it changed
        if self._last_display_text and self._last_display_text != display_text:
            graphics.DrawText(
                self.canvas,
                DATE_FONT,
                DATE_POSITION[0],
                DATE_POSITION[1],
                colours.BLACK,
                self._last_display_text,
            )

        # Also clear on scene re-entry
        if getattr(self, "_redraw_date", False) and self._last_display_text:
            graphics.DrawText(
                self.canvas,
                DATE_FONT,
                DATE_POSITION[0],
                DATE_POSITION[1],
                colours.BLACK,
                self._last_display_text,
            )

        self._last_display_text = display_text
        self._last_date = current_date

        # Draw with appropriate color
        if item_type == "date":
            self.draw_gradient_text(display_text, DATE_POSITION[0], DATE_POSITION[1], start_color, end_color)
        elif item_type == "high":
            graphics.DrawText(self.canvas, DATE_FONT, DATE_POSITION[0], DATE_POSITION[1], TIDE_HIGH_COLOUR, display_text)
        elif item_type == "low":
            graphics.DrawText(self.canvas, DATE_FONT, DATE_POSITION[0], DATE_POSITION[1], TIDE_LOW_COLOUR, display_text)

        self._redraw_date = False
