import sys
import json
import os
from datetime import datetime
from setup import frames
from utilities.animator import Animator
from utilities.overhead import Overhead

from scenes.temperature import TemperatureScene
from scenes.flightdetails import FlightDetailsScene
from scenes.flightlogo import FlightLogoScene
from scenes.journey import JourneyScene
from scenes.loadingpulse import LoadingPulseScene
from scenes.clock import ClockScene
from scenes.planedetails import PlaneDetailsScene
from scenes.daysforecast import DaysForecastScene
from scenes.date import DateScene
from scenes.trackedroute import TrackedRouteScene
from scenes.trackedprogress import TrackedProgressScene
from scenes.trackedstats import TrackedStatsScene

from rgbmatrix import graphics
from rgbmatrix import RGBMatrix, RGBMatrixOptions


def flight_updated(flights_a, flights_b):
    get_callsigns = lambda flights: [(f["callsign"], f["direction"]) for f in flights]
    updatable_a = set(get_callsigns(flights_a))
    updatable_b = set(get_callsigns(flights_b))
    return updatable_a == updatable_b


try:
    from config import (
        BRIGHTNESS,
        GPIO_SLOWDOWN,
        HAT_PWM_ENABLED,
        BRIGHTNESS_NIGHT,
        NIGHT_START,
        NIGHT_END,
        NIGHT_BRIGHTNESS,
    )
    NIGHT_START = datetime.strptime(NIGHT_START, "%H:%M")
    NIGHT_END = datetime.strptime(NIGHT_END, "%H:%M")

except (ModuleNotFoundError, NameError):
    BRIGHTNESS = 100
    GPIO_SLOWDOWN = 1
    HAT_PWM_ENABLED = True
    NIGHT_BRIGHTNESS = False


def adjust_brightness(matrix):
    if NIGHT_BRIGHTNESS is False:
        return

    now = datetime.now().time().replace(second=0, microsecond=0)
    night_start_time = NIGHT_START.time().replace(second=0, microsecond=0)
    night_end_time = NIGHT_END.time().replace(second=0, microsecond=0)

    if night_end_time <= now < night_start_time:
        new_brightness = BRIGHTNESS
    else:
        new_brightness = BRIGHTNESS_NIGHT

    if matrix.brightness != new_brightness:
        matrix.brightness = new_brightness


class Display(
    TemperatureScene,
    FlightDetailsScene,
    FlightLogoScene,
    JourneyScene,
    LoadingPulseScene,
    PlaneDetailsScene,
    ClockScene,
    DaysForecastScene,
    TrackedRouteScene,
    TrackedProgressScene,
    TrackedStatsScene,
    DateScene,
    Animator,
):
    def __init__(self):
        options = RGBMatrixOptions()
        options.hardware_mapping = "adafruit-hat-pwm" if HAT_PWM_ENABLED else "adafruit-hat"
        options.rows = 32
        options.cols = 64
        options.chain_length = 1
        options.parallel = 1
        options.row_address_type = 0
        options.multiplexing = 0
        options.pwm_bits = 11
        options.brightness = BRIGHTNESS
        options.pwm_lsb_nanoseconds = 160
        options.led_rgb_sequence = "RGB"
        options.pixel_mapper_config = ""
        options.show_refresh_rate = 0
        options.gpio_slowdown = GPIO_SLOWDOWN
        options.disable_hardware_pulsing = True
        options.drop_privileges = True
        options.limit_refresh_rate_hz = 120
        self.matrix = RGBMatrix(options=options)

        self.canvas = self.matrix.CreateFrameCanvas()
        self.canvas.Clear()

        self._data_index = 0
        self._data = []

        # Single Overhead instance handles both zone and tracked flight
        self.overhead = Overhead()
        self.overhead.grab_data()

        super().__init__()

        self.delay = frames.PERIOD

    def draw_square(self, x0, y0, x1, y1, colour):
        for x in range(x0, x1):
            _ = graphics.DrawLine(self.canvas, x, y0, x, y1, colour)

    @Animator.KeyFrame.add(0)
    def clear_screen(self):
        self.canvas.Clear()

    @Animator.KeyFrame.add(frames.PER_SECOND * 5)
    def check_for_loaded_data(self, count):
        if self.overhead.new_data:
            there_is_data = len(self._data) > 0 or not self.overhead.data_is_empty
            new_data = self.overhead.data
            data_is_different = not flight_updated(self._data, new_data)

            if data_is_different:
                self._data_index = 0
                self._data_all_looped = False
                self._data = new_data

            reset_required = there_is_data and data_is_different

            if reset_required:
                self.reset_scene()

    @Animator.KeyFrame.add(1)
    def sync(self, count):
        _ = self.matrix.SwapOnVSync(self.canvas)
        adjust_brightness(self.matrix)

    @Animator.KeyFrame.add(frames.PER_SECOND * 30)
    def grab_new_data(self, count):
        # One call to overhead.grab_data() handles both zone scan
        # and tracked flight lookup (tracked only if zone is empty)
        if not (self.overhead.processing and self.overhead.new_data) and (
            self._data_all_looped or len(self._data) <= 1
        ):
            self.overhead.grab_data()

    def run(self):
        try:
            print("Press CTRL-C to stop")
            self.play()
        except KeyboardInterrupt:
            print("Exiting\n")
            sys.exit(0)
