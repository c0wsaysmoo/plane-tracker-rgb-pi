from collections import Counter
from datetime import datetime, timedelta
from PIL import Image

from utilities.animator import Animator
from setup import colours, fonts, frames, screen
from utilities.temperature import grab_forecast, grab_hourly_forecast
from config import NIGHT_START, NIGHT_END
from rgbmatrix import graphics

# Setup
DAY_COLOUR = colours.LIGHT_PINK
MIN_T_COLOUR = colours.LIGHT_MID_BLUE
MAX_T_COLOUR = colours.LIGHT_DARK_ORANGE
TEXT_FONT = fonts.extrasmall
FONT_HEIGHT = 5
DISTANCE_FROM_TOP = 32
ICON_SIZE = 10
FORECAST_SIZE = FONT_HEIGHT * 2 + ICON_SIZE
DAY_POSITION = DISTANCE_FROM_TOP - FONT_HEIGHT - ICON_SIZE
ICON_POSITION = DISTANCE_FROM_TOP - FONT_HEIGHT - ICON_SIZE
TEMP_POSITION = DISTANCE_FROM_TOP
NIGHT_START_TIME = datetime.strptime(NIGHT_START, "%H:%M")
NIGHT_END_TIME = datetime.strptime(NIGHT_END, "%H:%M")

# Time-of-day periods: (label, start_hour_inclusive, end_hour_inclusive)
_PERIODS = [
    ("Nite",  0,  5),
    ("Morn", 6, 11),
    ("Aft", 12, 17),
    ("Eve", 18, 23),
]

def _period_index(hour):
    for i, (_, start, end) in enumerate(_PERIODS):
        if start <= hour <= end:
            return i
    return 1  # fallback to Morning


def _parse_hhmm(s, default):
    try:
        return datetime.strptime(s, "%H:%M").time()
    except (ValueError, TypeError):
        return datetime.strptime(default, "%H:%M").time()


def _load_sun_map():
    """Build {date: (sunrise_dt, sunset_dt)} from the daily forecast cache
    (kept populated by the clock scene). Times are local, tz-aware."""
    sun_map = {}
    try:
        from utilities.temperature import _load_file_cache, _FORECAST_CACHE_FILE
        cached, _ = _load_file_cache(_FORECAST_CACHE_FILE)
        if cached and isinstance(cached, list):
            for day in cached:
                vals = day.get("values", {})
                sr, ss = vals.get("sunriseTime"), vals.get("sunsetTime")
                if not (sr and ss):
                    continue
                sr_dt = datetime.fromisoformat(sr.replace("Z", "+00:00")).astimezone()
                ss_dt = datetime.fromisoformat(ss.replace("Z", "+00:00")).astimezone()
                sun_map[sr_dt.date()] = (sr_dt, ss_dt)
    except Exception:
        pass
    return sun_map


def _is_hour_daytime(dt, sun_map):
    """True if dt falls between that day's sunrise and sunset (from the daily
    forecast cache), else a simple 6am–8pm fallback when sun times are missing."""
    times = sun_map.get(dt.date())
    if times:
        return times[0] <= dt <= times[1]
    return 6 <= dt.hour < 20


def _build_hourly_slots(intervals):
    """
    Given a list of 1h intervals, group into period buckets and return the
    next 3 consecutive periods starting from the current period.
    Each slot: {"label": str, "weatherCode": int, "temperatureMin": float, "temperatureMax": float}
    """
    now = datetime.now().astimezone()
    today = now.date()
    sun_map = _load_sun_map()

    # Bucket hourly intervals into (date, period_index) groups
    buckets = {}
    for entry in intervals:
        raw = entry.get("startTime", "")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.astimezone()

        # Skip hours before the current hour (keep the in-progress hour so the
        # current period stays until its true end, e.g. Aft until 18:00 sharp)
        if dt < now.replace(minute=0, second=0, microsecond=0):
            continue

        vals = dict(entry.get("values", {}))
        # Tag each hour with whether it's daytime (per real sunrise/sunset)
        vals["_is_day"] = _is_hour_daytime(dt, sun_map)

        pi = _period_index(dt.hour)
        key = (dt.date(), pi)
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(vals)

    if not buckets:
        return []

    # Build ordered list of (date, period_index) starting from current period
    current_pi = _period_index(now.hour)
    slots = []
    # Iterate over today + tomorrow, all 4 periods
    for day_offset in range(3):
        d = today + timedelta(days=day_offset)
        for pi in range(4):
            if day_offset == 0 and pi < current_pi:
                continue
            key = (d, pi)
            if key in buckets:
                values_list = buckets[key]
                temps = [v["temperature"] for v in values_list if "temperature" in v]
                base_codes = [int(v["weatherCode"]) for v in values_list if "weatherCode" in v]
                label, _, _ = _PERIODS[pi]

                # Weather type: most common base (4-digit) code for the period
                base = Counter(base_codes).most_common(1)[0][0] if base_codes else None

                # Day/night: majority of the period's hours (real sun times).
                # On a tie, lean by period: start-of-day (Morn/Aft) -> day,
                # end-of-day (Eve/Ngt) -> night.
                day_votes   = sum(1 for v in values_list if v.get("_is_day"))
                night_votes = len(values_list) - day_votes
                if day_votes != night_votes:
                    is_day = day_votes > night_votes
                else:
                    is_day = pi in (1, 2)  # Morn, Aft

                if base is not None:
                    code = base if base >= 10000 else base * 10 + (0 if is_day else 1)
                else:
                    code = None

                slots.append({
                    "label": label,
                    "temperatureMin": min(temps) if temps else None,
                    "temperatureMax": max(temps) if temps else None,
                    "weatherCode": code,
                })
            if len(slots) == 3:
                return slots

    return slots


class DaysForecastScene(object):
    def __init__(self):
        super().__init__()
        self._redraw_forecast = True
        self._cached_forecast = None

        try:
            from config import FORECAST_MODE as _mode
            self._forecast_mode = _mode
        except (ImportError, NameError):
            self._forecast_mode = "daily"

        # Hourly window (used only when forecast_mode == "scheduled")
        try:
            from config import FORECAST_HOURLY_START, FORECAST_HOURLY_END
            self._hourly_start = _parse_hhmm(FORECAST_HOURLY_START, "05:00")
            self._hourly_end   = _parse_hhmm(FORECAST_HOURLY_END, "09:00")
        except (ImportError, NameError):
            self._hourly_start = _parse_hhmm("05:00", "05:00")
            self._hourly_end   = _parse_hhmm("09:00", "09:00")

        # Which underlying mode is active right now ("hourly" or "daily")
        self._active_mode = self._effective_mode()

        # Choose cache file based on the active mode
        if self._active_mode == "hourly":
            from utilities.temperature import _HOURLY_CACHE_FILE
            _cache_file = _HOURLY_CACHE_FILE
        else:
            from utilities.temperature import _FORECAST_CACHE_FILE
            _cache_file = _FORECAST_CACHE_FILE

        # Pre-load from disk cache
        from utilities.temperature import _load_file_cache
        import time as _time
        from datetime import datetime as _dt
        cached, ts = _load_file_cache(_cache_file)
        if cached and (_time.time() - ts) < 7200:
            self._cached_forecast = cached
            self._last_hour = _dt.fromtimestamp(ts).hour
        else:
            self._last_hour = None

    def _effective_mode(self):
        """Resolve the active sub-mode. For "scheduled", show hourly inside the
        configured window and daily the rest of the time."""
        if self._forecast_mode != "scheduled":
            return "hourly" if self._forecast_mode == "hourly" else "daily"

        now = datetime.now().time()
        start, end = self._hourly_start, self._hourly_end
        if start <= end:
            in_window = start <= now < end
        else:  # window wraps past midnight
            in_window = now >= start or now < end
        return "hourly" if in_window else "daily"

    @Animator.KeyFrame.add(frames.PER_SECOND * 1)
    def day(self, count):
        now = datetime.now().replace(microsecond=0).time()
        if now == NIGHT_START_TIME.time() or now == NIGHT_END_TIME.time():
            self._redraw_forecast = True
            return

        if len(self._data):
            self._redraw_forecast = True
            return

        if self.overhead.tracked_data is not None:
            if not self._redraw_forecast:
                self.draw_square(0, 12, 64, 32, colours.BLACK)
                self._redraw_forecast = True
            return

        current_hour = datetime.now().hour
        eff_mode = self._effective_mode()
        mode_changed = eff_mode != self._active_mode

        need_fetch = (
            self._cached_forecast is None
            or self._last_hour != current_hour
            or mode_changed
        )

        if self._last_hour != current_hour or self._redraw_forecast or mode_changed:

            if self._last_hour is not None:
                self.draw_square(0, 12, 64, 32, colours.BLACK)

            self._last_hour = current_hour
            self._active_mode = eff_mode

            if need_fetch:
                if eff_mode == "hourly":
                    forecast = grab_hourly_forecast(tag="days")
                else:
                    forecast = grab_forecast(tag="days")

                if not forecast:
                    # Don't fall back to a cache of the *other* mode's shape
                    if self._cached_forecast and not mode_changed:
                        forecast = self._cached_forecast
                    else:
                        self._cached_forecast = None
                        return
                else:
                    self._cached_forecast = forecast
            else:
                forecast = self._cached_forecast

            self._redraw_forecast = False

            if eff_mode == "hourly":
                self._render_hourly(forecast)
            else:
                self._render_daily(forecast)

    def _render_daily(self, forecast):
        offset = 1
        space_width = screen.WIDTH // 3
        now = datetime.now().astimezone()
        today_local = now.date()

        for day in forecast:
            raw_start = day["startTime"]
            local_time = datetime.fromisoformat(raw_start)
            entry_date = local_time.date()

            if entry_date < today_local:
                continue

            day_name = local_time.strftime("%a")
            icon = day["values"]["weatherCodeFullDay"]
            min_temp = f"{day['values']['temperatureMin']:.0f}"
            max_temp = f"{day['values']['temperatureMax']:.0f}"

            self._draw_slot(offset, space_width, day_name, icon, min_temp, max_temp)
            offset += space_width
            if offset >= screen.WIDTH:
                break

    def _render_hourly(self, intervals):
        slots = _build_hourly_slots(intervals)
        if not slots:
            return

        offset = 1
        space_width = screen.WIDTH // 3

        for slot in slots:
            label = slot["label"]
            icon = slot["weatherCode"]
            min_t = slot["temperatureMin"]
            max_t = slot["temperatureMax"]

            if icon is None or min_t is None or max_t is None:
                offset += space_width
                continue

            min_temp = f"{min_t:.0f}"
            max_temp = f"{max_t:.0f}"

            self._draw_slot(offset, space_width, label, icon, min_temp, max_temp)
            offset += space_width
            if offset >= screen.WIDTH:
                break

    def _draw_slot(self, offset, space_width, label, icon, min_temp, max_temp):
        min_temp_width = len(min_temp) * 4
        max_temp_width = len(max_temp) * 4

        temp_x = offset + (space_width - min_temp_width - max_temp_width - 1) // 2 + 1
        max_temp_x = temp_x
        min_temp_x = temp_x + max_temp_width

        icon_x = offset + (space_width - ICON_SIZE) // 2
        label_width = len(label) * 4
        day_x = offset + (space_width - label_width) // 2 + 1

        graphics.DrawText(self.canvas, TEXT_FONT, day_x, DAY_POSITION, DAY_COLOUR, label)

        # Try the exact icon; if a 5-digit day/night variant is missing,
        # fall back to its 4-digit base code (always present for daily mode).
        image = None
        try:
            image = Image.open(f"icons/{icon}.png")
        except FileNotFoundError:
            try:
                base = int(icon) // 10
                image = Image.open(f"icons/{base}.png")
            except (FileNotFoundError, ValueError, TypeError):
                image = None

        if image is not None:
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.ANTIALIAS
            image.thumbnail((ICON_SIZE, ICON_SIZE), resample)
            rgb = image.convert("RGB")
            pixels = rgb.load()
            w, h = rgb.size
            for py in range(h):
                for px in range(w):
                    r, g, b = pixels[px, py]
                    self.canvas.SetPixel(px + icon_x, py + ICON_POSITION, r, g, b)

        graphics.DrawText(self.canvas, TEXT_FONT, max_temp_x, TEMP_POSITION, MAX_T_COLOUR, max_temp)
        graphics.DrawText(self.canvas, TEXT_FONT, min_temp_x, TEMP_POSITION, MIN_T_COLOUR, min_temp)
