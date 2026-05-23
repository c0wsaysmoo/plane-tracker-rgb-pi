from datetime import datetime, timezone
from utilities.temperature import grab_forecast
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics
import logging
from config import CLOCK_FORMAT

try:
    from utilities.rain import get_rain_alert
except ImportError:
    get_rain_alert = lambda: None

# Setup — normal clock (no rain)
CLOCK_FONT = fonts.large_bold          # 8x13B
CLOCK_POSITION = (0, 11)
DAY_COLOUR = colours.LIGHT_ORANGE
NIGHT_COLOUR = colours.LIGHT_BLUE

# Rain mode — small clock + alert text below
CLOCK_SMALL_FONT = fonts.small         # 5x8
CLOCK_SMALL_POSITION = (0, 6)
RAIN_FONT = fonts.extrasmall           # 4x6
RAIN_POSITION = (0, 11)
RAIN_COLOUR = colours.LIGHT_BLUE
SNOW_COLOUR = colours.WHITE


class ClockScene(object):
    def __init__(self):
        super().__init__()
        self._last_time = None
        self.today_sunrise = None
        self.today_sunset = None
        self.last_fetch_date = None  # Store the date of the last forecast fetch
        self._forecast_retry_after = 0  # Epoch time: don't retry before this
        self._rain_active = False
        self._last_rain_text = None

        # Pre-load sunrise/sunset from disk cache (survives reboots).
        # Concept from c0wsaysmoo/plane-tracker-rgb-pi.
        try:
            from utilities.temperature import _load_file_cache
            import os, time as _time
            cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache")
            suntimes_file = os.path.join(cache_dir, "suntimes.json")
            cached, ts = _load_file_cache(suntimes_file)
            if cached and (_time.time() - ts) < 86400:  # 24-hour TTL
                sr = datetime.fromisoformat(cached["sunrise"].replace("Z", "+00:00"))
                ss = datetime.fromisoformat(cached["sunset"].replace("Z", "+00:00"))
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
                        utc_sunrise = datetime.fromisoformat(day['values']['sunriseTime'].replace("Z", "+00:00"))
                        utc_sunset = datetime.fromisoformat(day['values']['sunsetTime'].replace("Z", "+00:00"))

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

    def _format_rain_text(self, alert):
        """Format rain alert dict into short display text."""
        if not alert:
            return None
        type_labels = {"snow": "Snow", "sleet": "Sleet", "rain": "Rain"}
        label = type_labels.get(alert["type"], "Rain")
        action = alert.get("action", "")
        minutes = alert.get("minutes")
        if action == "starting" and minutes:
            return f"{label} {minutes}m"
        elif action == "stopping" and minutes:
            return f"Stop {minutes}m"
        elif action == "now":
            return label
        return None

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def clock(self, count):
        if len(self._data):
            self._redraw_time = True
            return

        now = datetime.now()
        clock_format = "%l:%M" if CLOCK_FORMAT == "12hr" else "%H:%M"
        current_time = now.strftime(clock_format).lstrip()

        utc_sunrise, utc_sunset = self.calculate_sunrise_sunset()
        now_utc = datetime.now(timezone.utc)

        if utc_sunrise is None or utc_sunset is None:
            clock_color = colours.RED
        elif utc_sunrise <= now_utc < utc_sunset:
            clock_color = DAY_COLOUR
        else:
            clock_color = NIGHT_COLOUR

        # Check for rain alert
        try:
            alert = get_rain_alert()
        except Exception:
            alert = None

        rain_text = self._format_rain_text(alert)
        rain_now_active = rain_text is not None

        # Detect transition between rain/no-rain modes
        mode_changed = rain_now_active != self._rain_active
        time_changed = self._last_time != current_time
        rain_text_changed = rain_text != self._last_rain_text
        needs_redraw = getattr(self, "_redraw_time", False)

        if mode_changed or needs_redraw:
            # Clear entire left region on mode switch or scene re-entry
            self.draw_square(0, 0, 40, 12, colours.BLACK)
        elif time_changed:
            # Just clear old clock text
            if self._last_time:
                old_font = CLOCK_SMALL_FONT if self._rain_active else CLOCK_FONT
                old_pos = CLOCK_SMALL_POSITION if self._rain_active else CLOCK_POSITION
                graphics.DrawText(self.canvas, old_font, old_pos[0], old_pos[1],
                                  colours.BLACK, self._last_time)

        if rain_text_changed and not mode_changed:
            # Clear old rain text only
            if self._last_rain_text:
                graphics.DrawText(self.canvas, RAIN_FONT, RAIN_POSITION[0],
                                  RAIN_POSITION[1], colours.BLACK, self._last_rain_text)

        # Draw clock
        if rain_now_active:
            graphics.DrawText(self.canvas, CLOCK_SMALL_FONT,
                              CLOCK_SMALL_POSITION[0], CLOCK_SMALL_POSITION[1],
                              clock_color, current_time)
        else:
            graphics.DrawText(self.canvas, CLOCK_FONT,
                              CLOCK_POSITION[0], CLOCK_POSITION[1],
                              clock_color, current_time)

        # Draw rain text
        if rain_text:
            rain_color = SNOW_COLOUR if alert and alert["type"] in ("snow", "sleet") else RAIN_COLOUR
            graphics.DrawText(self.canvas, RAIN_FONT,
                              RAIN_POSITION[0], RAIN_POSITION[1],
                              rain_color, rain_text)

        self._last_time = current_time
        self._rain_active = rain_now_active
        self._last_rain_text = rain_text
        self._redraw_time = False
