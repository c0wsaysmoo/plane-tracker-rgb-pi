from datetime import datetime, timezone
from utilities.temperature import grab_forecast
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics
import logging
from config import CLOCK_FORMAT

try:
    from utilities.rain import get_rain_alert, get_wind_info
except ImportError:
    get_rain_alert = lambda: None
    get_wind_info = lambda: None

try:
    from utilities.nws_alerts import get_active_alerts
except ImportError:
    get_active_alerts = lambda: []

try:
    from utilities.airport_status import get_airport_alerts
except ImportError:
    get_airport_alerts = lambda: []

try:
    from utilities.iss import get_iss_alert
except ImportError:
    get_iss_alert = lambda: None

try:
    from utilities.temperature import get_uv_index
except ImportError:
    get_uv_index = lambda: None

# Setup — normal clock (no alerts)
CLOCK_FONT = fonts.large_bold          # 8x13B
CLOCK_POSITION = (0, 11)
DAY_COLOUR = colours.LIGHT_ORANGE
NIGHT_COLOUR = colours.LIGHT_BLUE

# Alert mode — small clock + alert text below
CLOCK_SMALL_FONT = fonts.small         # 5x8
CLOCK_SMALL_POSITION = (0, 6)
ALERT_FONT = fonts.extrasmall          # 4x6
ALERT_POSITION = (0, 11)

# Alert rotation interval (seconds)
_ALERT_CYCLE_SECONDS = 4

# Color name → graphics.Color mapping for NWS alerts
_ALERT_COLOURS = {
    "red":    colours.RED,
    "orange": colours.LIGHT_ORANGE,
    "cyan":   colours.CYAN,
    "yellow": colours.YELLOW,
    "grey":   colours.GREY,
    "white":  colours.WHITE,
    "blue":   colours.LIGHT_BLUE,
}


class ClockScene(object):
    def __init__(self):
        super().__init__()
        self._last_time = None
        self.today_sunrise = None
        self.today_sunset = None
        self.last_fetch_date = None
        self._forecast_retry_after = 0
        self._alert_active = False
        self._last_alert_text = None
        self._alert_cycle_counter = 0

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
            if self.last_fetch_date != now.date():
                if datetime.now(timezone.utc).timestamp() < self._forecast_retry_after:
                    return self.today_sunrise, self.today_sunset

                forecast = grab_forecast(tag="ClockScene")
                if not forecast:
                    logging.error("Forecast data missing or API error.")
                    self._forecast_retry_after = datetime.now(timezone.utc).timestamp() + 300
                    return None, None

                for day in forecast:
                    forecast_date = day['startTime'][:10]
                    if forecast_date == now.strftime('%Y-%m-%d'):
                        utc_sunrise = datetime.fromisoformat(day['values']['sunriseTime'].replace("Z", "+00:00"))
                        utc_sunset = datetime.fromisoformat(day['values']['sunsetTime'].replace("Z", "+00:00"))

                        self.today_sunrise = utc_sunrise
                        self.today_sunset = utc_sunset
                        self.last_fetch_date = now.date()

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

    def _build_alert_items(self):
        """Build unified list of alert items from rain, NWS, sun, and wind.

        Returns list of (text, color) tuples.
        """
        items = []

        # Rain/snow/sleet alert
        try:
            rain = get_rain_alert()
        except Exception:
            rain = None
        if rain:
            type_labels = {"snow": "Snow", "sleet": "Sleet", "rain": "Rain"}
            label = type_labels.get(rain["type"], "Rain")
            action = rain.get("action", "")
            minutes = rain.get("minutes")
            if action == "starting" and minutes:
                text = f"{label} {minutes}m"
            elif action == "stopping" and minutes:
                text = f"Stop {minutes}m"
            elif action == "now":
                text = label
            else:
                text = None
            if text:
                color = _ALERT_COLOURS.get("white" if rain["type"] in ("snow", "sleet") else "blue")
                items.append((text, color))

        # Wind alert (from OWM data, already fetched by rain.py)
        try:
            wind = get_wind_info()
        except Exception:
            wind = None
        if wind:
            items.append((wind["text"], _ALERT_COLOURS.get(wind["color"], colours.WHITE)))

        # NWS alerts
        try:
            nws = get_active_alerts()
        except Exception:
            nws = []
        for a in nws:
            color = _ALERT_COLOURS.get(a.get("color", "grey"), colours.GREY)
            items.append((a["text"], color))

        # FAA airport delays
        try:
            faa = get_airport_alerts()
        except Exception:
            faa = []
        for a in faa:
            color = _ALERT_COLOURS.get(a.get("color", "grey"), colours.GREY)
            items.append((a["text"], color))

        # ISS overhead pass
        try:
            iss = get_iss_alert()
        except Exception:
            iss = None
        if iss:
            items.append((iss["text"], _ALERT_COLOURS.get(iss["color"], colours.WHITE)))

        # UV index alert
        try:
            uv = get_uv_index()
        except Exception:
            uv = None
        if uv is not None and uv > 0:
            uv_int = max(1, int(round(uv)))
            # EPA/WHO standard colors
            if uv_int >= 11:    color = colours.PURPLE        # Extreme
            elif uv_int >= 8:   color = colours.RED           # Very High
            elif uv_int >= 6:   color = colours.LIGHT_ORANGE  # High
            elif uv_int >= 3:   color = colours.YELLOW        # Moderate
            else:               color = colours.GREEN         # Low
            items.append((f"UV {uv_int}", color))

        # Sunrise/sunset countdown (within 30 min)
        try:
            if self.today_sunrise and self.today_sunset:
                now_utc = datetime.now(timezone.utc)
                to_sunset = (self.today_sunset - now_utc).total_seconds()
                to_sunrise = (self.today_sunrise - now_utc).total_seconds()
                if 0 < to_sunset <= 1800:
                    mins = int(to_sunset / 60)
                    items.append((f"Sun {mins}m", _ALERT_COLOURS["orange"]))
                elif 0 < to_sunrise <= 1800:
                    mins = int(to_sunrise / 60)
                    items.append((f"Rise {mins}m", _ALERT_COLOURS["yellow"]))
        except Exception:
            pass

        return items

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def clock(self, count):
        if getattr(self, '_iss_active', False):
            self._redraw_time = True
            return
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

        # Build unified alert list and pick current item
        alert_items = self._build_alert_items()
        self._alert_cycle_counter += 1

        if alert_items:
            # Speed up rotation when many alerts (3s per item if >4 items)
            cycle_secs = 3 if len(alert_items) > 4 else _ALERT_CYCLE_SECONDS
            slot = (self._alert_cycle_counter // cycle_secs) % len(alert_items)
            alert_text, alert_color = alert_items[slot]
        else:
            alert_text, alert_color = None, None

        alert_now_active = len(alert_items) > 0

        # Detect transitions
        mode_changed = alert_now_active != self._alert_active
        time_changed = self._last_time != current_time
        alert_text_changed = alert_text != self._last_alert_text
        needs_redraw = getattr(self, "_redraw_time", False)

        if mode_changed or needs_redraw:
            # Clear entire left region on mode switch or scene re-entry
            self.draw_square(0, 0, 40, 12, colours.BLACK)
        elif time_changed:
            # Just clear old clock text
            if self._last_time:
                old_font = CLOCK_SMALL_FONT if self._alert_active else CLOCK_FONT
                old_pos = CLOCK_SMALL_POSITION if self._alert_active else CLOCK_POSITION
                graphics.DrawText(self.canvas, old_font, old_pos[0], old_pos[1],
                                  colours.BLACK, self._last_time)

        if alert_text_changed and not mode_changed:
            # Clear old alert text only
            if self._last_alert_text:
                graphics.DrawText(self.canvas, ALERT_FONT, ALERT_POSITION[0],
                                  ALERT_POSITION[1], colours.BLACK, self._last_alert_text)

        # Draw clock
        if alert_now_active:
            graphics.DrawText(self.canvas, CLOCK_SMALL_FONT,
                              CLOCK_SMALL_POSITION[0], CLOCK_SMALL_POSITION[1],
                              clock_color, current_time)
        else:
            graphics.DrawText(self.canvas, CLOCK_FONT,
                              CLOCK_POSITION[0], CLOCK_POSITION[1],
                              clock_color, current_time)

        # Draw alert text
        if alert_text:
            graphics.DrawText(self.canvas, ALERT_FONT,
                              ALERT_POSITION[0], ALERT_POSITION[1],
                              alert_color, alert_text)

        self._last_time = current_time
        self._alert_active = alert_now_active
        self._last_alert_text = alert_text
        self._redraw_time = False
