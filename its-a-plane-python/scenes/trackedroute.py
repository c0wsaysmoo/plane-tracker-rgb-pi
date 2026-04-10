from utilities.animator import Animator
from setup import colours, fonts, screen
from rgbmatrix import graphics

LINE1_Y = 19
FONT = fonts.small

NUMERIC_COLOUR = colours.LIGHT_ORANGE
ARROW_COLOUR   = colours.WHITE


def _delay_colour(real, scheduled, is_arrival=False):
    if real is None or scheduled in (None, 0):
        return colours.LIGHT_GREY

    delay = (real - scheduled) / 60

    if is_arrival:
        if delay <= 0:
            return colours.LIGHT_MID_GREEN
        elif delay <= 30:
            return colours.LIGHT_YELLOW
        elif delay <= 60:
            return colours.LIGHT_MID_ORANGE
        elif delay <= 240:
            return colours.LIGHT_RED
        elif delay <= 480:
            return colours.LIGHT_PURPLE
        else:
            return colours.LIGHT_DARK_BLUE
    else:
        if delay <= 20:
            return colours.LIGHT_MID_GREEN
        elif delay <= 40:
            return colours.LIGHT_YELLOW
        elif delay <= 60:
            return colours.LIGHT_MID_ORANGE
        elif delay <= 240:
            return colours.LIGHT_RED
        elif delay <= 480:
            return colours.LIGHT_PURPLE
        else:
            return colours.LIGHT_DARK_BLUE


class TrackedRouteScene(object):
    def __init__(self):
        super().__init__()
        self._tr_pos = screen.WIDTH
        self._tr_len = 0

    @Animator.KeyFrame.add(0)
    def reset_tracked_route_scroll(self):
        self._tr_pos = screen.WIDTH
        self._tr_len = 0

    @Animator.KeyFrame.add(1)
    def tracked_route(self, count):
        if len(self._data) > 0:
            return

        tracked = self.overhead.tracked_data
        if not tracked:
            return

        number      = tracked.get("number", tracked.get("callsign", ""))
        origin      = tracked.get("origin", "???")
        destination = tracked.get("destination", "???")

        origin_colour = _delay_colour(
            tracked.get("time_real_departure"),
            tracked.get("time_scheduled_departure"),
            is_arrival=False,
        )
        destination_colour = _delay_colour(
            tracked.get("time_estimated_arrival"),
            tracked.get("time_scheduled_arrival"),
            is_arrival=True,
        )

        # Build per-character colour list for the line
        chars = []
        for ch in number:
            chars.append((ch, NUMERIC_COLOUR if ch.isnumeric() else colours.LIGHT_PURPLE))
        chars.append((" ", ARROW_COLOUR))
        for ch in origin:
            chars.append((ch, origin_colour))
        chars.append((" \u2192 ", ARROW_COLOUR))
        for ch in destination:
            chars.append((ch, destination_colour))

        # Clear row
        self.draw_square(0, LINE1_Y - 6, screen.WIDTH, LINE1_Y, colours.BLACK)

        # Draw scrolling text
        total_len = 0
        for ch, colour in chars:
            w = graphics.DrawText(
                self.canvas, FONT,
                self._tr_pos + total_len, LINE1_Y,
                colour, ch,
            )
            total_len += w
        self._tr_len = total_len

        self._tr_pos -= 1

        if self._tr_pos + self._tr_len < 0:
            self._tr_pos = screen.WIDTH
