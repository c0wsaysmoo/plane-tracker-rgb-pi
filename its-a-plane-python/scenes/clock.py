from datetime import datetime, timezone
from utilities.temperature import grab_forecast
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics
import logging
from config import CLOCK_FORMAT

# Setup
CLOCK_FONT = fonts.large_bold
CLOCK_POSITION = (0, 11)
DAY_COLOUR = colours.LIGHT_ORANGE
NIGHT_COLOUR = colours.LIGHT_BLUE

class ClockScene(object):
    def __init__(self):
        super().__init__()
        self._last_time = None
        self.today_sunrise = None
        self.today_sunset = None
        self.last_fetch_date = None  # Store the date of the last forecast fetch
        self._forecast_retry_after = 0  # Epoch time: don't retry before this

        # Pre-load sunrise/sunset from disk cache (survives reboots).
        # Concept from c0wsaysmoo/plane-tracker-rgb-pi.
        try:
            from utilities.temperature import _load_file_cache
            import os, time as _time
            cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
            suntimes_file = os.path.join(cache_dir, "suntimes.json")
            cached, ts = _load_file_cache(suntimes_file)
            if cached and (_time.time() - ts) < 86400:  # 24-hour TTL
                sr = datetime.strptime(cached["sunrise"], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                ss = datetime.strptime(cached["sunset"], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                self.today_sunrise = sr
                self.today_sunset = ss
                self.last_fetch_date = datetime.now().date()
                logging.info(f"Clock: loaded cached sunrise/sunset from disk")
        except Exception:
            pass  # First boot or corrupt cache — will fetch from API

    def calculate_sunrise_sunset(self):
        now = datetime.now()

        try:
            # Only fetch forecast if it's a new day or if no cached data
            if self.last_fetch_date != now.date():
                # Cooldown: don't hammer the API on repeated failures
                if datetime.now(timezone.utc).timestamp() < self._forecast_retry_after:
                    return self.today_sunrise, self.today_sunset

                forecast = grab_forecast(tag="ClockScene")
                if not forecast:  # None or empty list
                    logging.error("Forecast data missing or API error.")
                    self._forecast_retry_after = datetime.now(timezone.utc).timestamp() + 300  # 5 min
                    return None, None

                for day in forecast:
                    forecast_date = day['startTime'][:10]
                    if forecast_date == now.strftime('%Y-%m-%d'):
                        # Parse UTC sunrise and sunset times
                        utc_sunrise = datetime.strptime(day['values']['sunriseTime'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                        utc_sunset = datetime.strptime(day['values']['sunsetTime'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)

                        # Cache values
                        self.today_sunrise = utc_sunrise
                        self.today_sunset = utc_sunset
                        self.last_fetch_date = now.date()

                        # Persist to disk for reboot survival
                        try:
                            from utilities.temperature import _save_file_cache
                            import os
                            cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
                            suntimes_file = os.path.join(cache_dir, "suntimes.json")
                            _save_file_cache(suntimes_file, {
                                "sunrise": day['values']['sunriseTime'],
                                "sunset": day['values']['sunsetTime'],
                            })
                        except Exception:
                            pass

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
        now_utc = datetime.now(timezone.utc)

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