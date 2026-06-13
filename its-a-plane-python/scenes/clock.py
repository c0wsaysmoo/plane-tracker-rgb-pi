from datetime import datetime
from utilities.temperature import grab_forecast, _load_file_cache, _save_file_cache
from utilities.animator import Animator
from setup import colours, fonts, frames
from rgbmatrix import graphics
import logging
import os
import time
from config import CLOCK_FORMAT, NIGHT_END, NIGHT_START

try:
    from utilities.airport_status import get_airport_alerts
except ImportError:
    get_airport_alerts = lambda: []

try:
    from utilities.iss import get_iss_alert
except ImportError:
    get_iss_alert = lambda: None

try:
    from utilities.nws import get_nws_alerts
except ImportError:
    get_nws_alerts = lambda: []

# ── Slave mode: fetch all alerts from master via /clock/json ─────────────────
try:
    from config import MASTER_TRACKER as _MASTER_TRACKER
except (ImportError, AttributeError):
    _MASTER_TRACKER = ""

if _MASTER_TRACKER:
    import requests as _requests
    from requests.exceptions import RequestException as _RequestException

    _slave_alerts_cache: list = []
    _slave_alerts_ts: float = 0.0
    _SLAVE_ALERTS_TTL = 60  # seconds

    def _slave_clock_url():
        host = _MASTER_TRACKER.rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}.local:8080"
        return f"{host}/clock/json"

    def _fetch_slave_alerts():
        global _slave_alerts_cache, _slave_alerts_ts
        now = time.time()
        if _slave_alerts_ts and (now - _slave_alerts_ts) < _SLAVE_ALERTS_TTL:
            return _slave_alerts_cache
        try:
            r = _requests.get(_slave_clock_url(), timeout=10)
            r.raise_for_status()
            _slave_alerts_cache = r.json().get("alerts", [])
            _slave_alerts_ts = now
        except _RequestException as e:
            logging.error(f"[Slave/Clock] Cannot reach master: {e}")
        return _slave_alerts_cache

    logging.info(f"[Clock] Slave mode — fetching alerts from master at {_slave_clock_url()}")

# Setup
CLOCK_FONT = fonts.large_bold
CLOCK_POSITION = (0, 11)
DAY_COLOUR = colours.LIGHT_ORANGE
NIGHT_COLOUR = colours.LIGHT_BLUE

# Alert mode — small clock + alert text below
CLOCK_SMALL_FONT = fonts.small         # 5x8
CLOCK_SMALL_POSITION = (0, 6)
ALERT_FONT = fonts.extrasmall         # 4x6
ALERT_POSITION = (0, 11)

_DISPLAY_WIDTH = 40  # clock region width (0–39, right side is another scene)

# Alert rotation interval (seconds)
_ALERT_CYCLE_SECONDS = 4

# File cache settings
_SUN_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "suntimes.json"
)
_SUN_CACHE_TTL = 86400  # 24 hours

# Color name → graphics.Color mapping
_ALERT_COLOURS = {
    "red":    colours.LIGHT_RED,
    "orange": colours.LIGHT_ORANGE,
    "yellow": colours.YELLOW,
    "white":  colours.WHITE,
}

# Convert NIGHT_START and NIGHT_END to datetime objects
NIGHT_START_TIME = datetime.strptime(NIGHT_START, "%H:%M")
NIGHT_END_TIME = datetime.strptime(NIGHT_END, "%H:%M")


class ClockScene(object):
    def __init__(self):
        super().__init__()
        self._last_time = None
        self.today_sunrise = None
        self.today_sunset = None
        self.last_fetch_date = None
        self._alert_active = False
        self._last_alert_text = None
        self._alert_cycle_counter = 0

        # Pre-load from disk cache so sun times show immediately on reboot
        cached, ts = _load_file_cache(_SUN_CACHE_FILE)
        if cached is not None and (time.time() - ts) < _SUN_CACHE_TTL:
            try:
                self.today_sunrise = datetime.strptime(cached['sunrise'], '%Y-%m-%dT%H:%M:%SZ')
                self.today_sunset = datetime.strptime(cached['sunset'], '%Y-%m-%dT%H:%M:%SZ')
                from datetime import datetime as _dt
                self.last_fetch_date = _dt.fromtimestamp(ts).date()
            except Exception as e:
                logging.error(f"Error parsing cached sun times: {e}")
                self.today_sunrise = None
                self.today_sunset = None
                self.last_fetch_date = None
        else:
            self.today_sunrise = None
            self.today_sunset = None
            self.last_fetch_date = None

    def calculate_sunrise_sunset(self):
        now = datetime.now()

        try:
            if self.last_fetch_date != now.date():
                forecast = grab_forecast(tag="ClockScene")
                if not forecast:
                    logging.error("Forecast data missing or API error.")
                    return None, None

                for day in forecast:
                    forecast_date = day['startTime'][:10]
                    if forecast_date == now.strftime('%Y-%m-%d'):
                        utc_sunrise = datetime.strptime(day['values']['sunriseTime'], '%Y-%m-%dT%H:%M:%SZ')
                        utc_sunset = datetime.strptime(day['values']['sunsetTime'], '%Y-%m-%dT%H:%M:%SZ')

                        self.today_sunrise = utc_sunrise
                        self.today_sunset = utc_sunset
                        self.last_fetch_date = now.date()

                        cache_data = {
                            'sunrise': day['values']['sunriseTime'],
                            'sunset': day['values']['sunsetTime']
                        }
                        _save_file_cache(_SUN_CACHE_FILE, cache_data)
                        break

        except Exception as e:
            logging.error(f"Error fetching forecast: {e}")
            return None, None

        return self.today_sunrise, self.today_sunset

    def _build_alert_items(self):
        """Build list of (text, color) alert tuples from FAA, ISS, and NWS."""
        items = []

        if _MASTER_TRACKER:
            # Secondary: pull pre-computed alerts from master in one request
            for a in _fetch_slave_alerts():
                color = _ALERT_COLOURS.get(a.get("color", "orange"), colours.LIGHT_ORANGE)
                items.append((a["text"], color))
            return items

        # FAA airport delays
        try:
            faa = get_airport_alerts()
        except Exception:
            faa = []
        for a in faa:
            color = _ALERT_COLOURS.get(a.get("color", "orange"), colours.LIGHT_ORANGE)
            items.append((a["text"], color))

        # ISS overhead pass warning
        try:
            iss = get_iss_alert()
        except Exception:
            iss = None
        if iss:
            items.append((iss["text"], _ALERT_COLOURS.get(iss["color"], colours.WHITE)))

        # NWS weather alerts
        try:
            wx_alerts = get_nws_alerts()
        except Exception:
            wx_alerts = []
        for wx in wx_alerts:
            color = _ALERT_COLOURS.get(wx.get("color", "orange"), colours.LIGHT_ORANGE)
            items.append((wx["text"], color))

        return items

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

        # Build alert list and pick current item
        alert_items = self._build_alert_items()
        self._alert_cycle_counter += 1

        if alert_items:
            slot = (self._alert_cycle_counter // _ALERT_CYCLE_SECONDS) % len(alert_items)
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
            # Clear old clock text only
            if self._last_time:
                old_font = CLOCK_SMALL_FONT if self._alert_active else CLOCK_FONT
                old_pos = CLOCK_SMALL_POSITION if self._alert_active else CLOCK_POSITION
                if self._alert_active:
                    old_width = graphics.DrawText(self.canvas, old_font, 0, 0, colours.BLACK, self._last_time)
                    old_x = max(0, (_DISPLAY_WIDTH - old_width) // 2)
                else:
                    old_x = old_pos[0]
                graphics.DrawText(self.canvas, old_font, old_x, old_pos[1],
                                  colours.BLACK, self._last_time)

        if alert_text_changed and not mode_changed:
            # Clear old alert text only
            if self._last_alert_text:
                old_alert_width = graphics.DrawText(self.canvas, ALERT_FONT, 0, 0, colours.BLACK, self._last_alert_text)
                old_alert_x = max(0, (_DISPLAY_WIDTH - old_alert_width) // 2)
                graphics.DrawText(self.canvas, ALERT_FONT, old_alert_x,
                                  ALERT_POSITION[1], colours.BLACK, self._last_alert_text)

        # Draw clock (small+bold+centered when alert active, large when not)
        if alert_now_active:
            text_width = graphics.DrawText(self.canvas, CLOCK_SMALL_FONT, 0, 0, colours.BLACK, current_time)
            x = max(0, (_DISPLAY_WIDTH - text_width) // 2)
            graphics.DrawText(self.canvas, CLOCK_SMALL_FONT,
                              x, CLOCK_SMALL_POSITION[1],
                              clock_color, current_time)
        else:
            graphics.DrawText(self.canvas, CLOCK_FONT,
                              CLOCK_POSITION[0], CLOCK_POSITION[1],
                              clock_color, current_time)

        # Draw alert text below clock
        if alert_text:
            alert_width = graphics.DrawText(self.canvas, ALERT_FONT, 0, 0, colours.BLACK, alert_text)
            alert_x = max(0, (_DISPLAY_WIDTH - alert_width) // 2)
            graphics.DrawText(self.canvas, ALERT_FONT,
                              alert_x, ALERT_POSITION[1],
                              alert_color, alert_text)

        self._last_time = current_time
        self._alert_active = alert_now_active
        self._last_alert_text = alert_text
        self._redraw_time = False
