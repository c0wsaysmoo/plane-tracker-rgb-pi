from PIL import Image
from utilities.animator import Animator
from setup import colours, fonts, screen
from rgbmatrix import graphics

LINE1_Y       = 19
FONT          = fonts.small
LOGO_SIZE     = 8
DEFAULT_IMAGE = "default"
LOGO_GAP      = 2   # pixels between logo and text

NUMERIC_COLOUR = colours.LIGHT_ORANGE
ARROW_COLOUR   = colours.WHITE


def _delay_colour(real, scheduled, is_arrival=False):
    if real is None or scheduled in (None, 0):
        return colours.LIGHT_GREY
    delay = (real - scheduled) / 60
    if is_arrival:
        if delay <= 0:     return colours.LIGHT_MID_GREEN
        elif delay <= 30:  return colours.LIGHT_YELLOW
        elif delay <= 60:  return colours.LIGHT_MID_ORANGE
        elif delay <= 240: return colours.LIGHT_RED
        elif delay <= 480: return colours.LIGHT_PURPLE
        else:              return colours.LIGHT_DARK_BLUE
    else:
        if delay <= 20:    return colours.LIGHT_MID_GREEN
        elif delay <= 40:  return colours.LIGHT_YELLOW
        elif delay <= 60:  return colours.LIGHT_MID_ORANGE
        elif delay <= 240: return colours.LIGHT_RED
        elif delay <= 480: return colours.LIGHT_PURPLE
        else:              return colours.LIGHT_DARK_BLUE


def _load_logo(airline_icao):
    """Load and resize logo to LOGO_SIZE x LOGO_SIZE, return as RGB pixel list."""
    icao = airline_icao if airline_icao and airline_icao not in ("", "N/A") else DEFAULT_IMAGE
    try:
        image = Image.open(f"logos/{icao}.png")
    except FileNotFoundError:
        try:
            image = Image.open(f"logos/{DEFAULT_IMAGE}.png")
        except FileNotFoundError:
            return None
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.ANTIALIAS
    image = image.resize((LOGO_SIZE, LOGO_SIZE), resample).convert("RGB")
    return image


def _draw_logo_at(canvas, image, x, y_top):
    """Draw logo pixel by pixel at scroll position x, clipped to screen bounds."""
    if image is None:
        return
    for py in range(LOGO_SIZE):
        for px in range(LOGO_SIZE):
            sx = x + px
            if 0 <= sx < screen.WIDTH:
                r, g, b = image.getpixel((px, py))
                canvas.SetPixel(sx, y_top + py, r, g, b)


class TrackedRouteScene(object):
    def __init__(self):
        super().__init__()
        self._tr_pos = screen.WIDTH
        self._tr_len = 0
        self._tr_logo = None
        self._tr_last_icao = None

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

        airline_name = tracked.get("airline_name", "")
        number       = tracked.get("number", tracked.get("callsign", ""))
        # Extract just the numeric part e.g. "UA1583" -> "1583"
        flight_num   = ''.join(ch for ch in number if ch.isnumeric())
        display_name = f"{airline_name} {flight_num}".strip() if airline_name else number
        origin       = tracked.get("origin", "???")
        destination  = tracked.get("destination", "???")
        airline_icao = tracked.get("callsign", "")[:3]

        # Cache logo — only reload if airline changed
        if airline_icao != self._tr_last_icao:
            self._tr_logo = _load_logo(airline_icao)
            self._tr_last_icao = airline_icao

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

        # Build text chars
        chars = []
        for ch in display_name:
            chars.append((ch, NUMERIC_COLOUR if ch.isnumeric() else colours.LIGHT_PURPLE))
        chars.append((" ", ARROW_COLOUR))
        for ch in origin:
            chars.append((ch, origin_colour))
        chars.append((" \u2192 ", ARROW_COLOUR))
        for ch in destination:
            chars.append((ch, destination_colour))

        # Clear row
        self.draw_square(0, LINE1_Y - LOGO_SIZE + 1, screen.WIDTH, LINE1_Y, colours.BLACK)

        # Draw logo at scroll position (scrolls with text)
        logo_x = self._tr_pos
        y_top  = LINE1_Y - LOGO_SIZE + 1
        _draw_logo_at(self.canvas, self._tr_logo, logo_x, y_top)

        # Draw text after logo + gap
        text_start = logo_x + LOGO_SIZE + LOGO_GAP
        total_len = 0
        for ch, colour in chars:
            w = graphics.DrawText(
                self.canvas, FONT,
                text_start + total_len, LINE1_Y,
                colour, ch,
            )
            total_len += w

        # Total scrollable width = logo + gap + text
        self._tr_len = LOGO_SIZE + LOGO_GAP + total_len

        self._tr_pos -= 1

        if self._tr_pos + self._tr_len < 0:
            self._tr_pos = screen.WIDTH
