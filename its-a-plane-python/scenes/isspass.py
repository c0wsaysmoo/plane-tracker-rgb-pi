"""
ISS Overhead Pass — full display takeover scene.

When the ISS is actively overhead (1-6 minutes), this scene takes over the
entire display with an animated ISS sprite, progress bar, and countdown.

Layout (32x64 LED matrix):
  Rows  0-4:  "ISS OVERHEAD" blinking text
  Rows  5-12: ISS sprite moving left-to-right (position = pass progress)
              with dim trail dots behind it
  Row   16:   Direction + elevation text (e.g., "NW > SE  88°")
  Rows 22-24: Progress bar (dashed: green flown, blue remaining, + marker)
  Rows 27-31: Countdown text (e.g., "3:42 LEFT")
"""

import logging
import os

from PIL import Image

from utilities.animator import Animator
from setup import colours, fonts, screen, frames
from rgbmatrix import graphics

logger = logging.getLogger(__name__)


# Fonts
TITLE_FONT = fonts.extrasmall       # 4x6
INFO_FONT = fonts.extrasmall        # 4x6
COUNTDOWN_FONT = fonts.small        # 5x8

# Colour themes — warm (visible) vs cool (not visible)
THEME_VISIBLE = {
    "title": colours.WHITE,
    "title_dim": colours.LIGHT_GREY,
    "trail": graphics.Color(60, 50, 20),        # gold
    "flown": colours.LIMEGREEN,
    "remaining": colours.LIGHT_BLUE,
    "marker": colours.WHITE,
    "info": colours.LIGHT_ORANGE,
    "countdown": colours.YELLOW,
}

THEME_DIM = {
    "title": graphics.Color(100, 130, 180),      # steel blue
    "title_dim": graphics.Color(50, 65, 90),      # dark blue
    "trail": graphics.Color(30, 35, 60),           # dim navy
    "flown": graphics.Color(40, 100, 100),         # dim teal
    "remaining": graphics.Color(60, 60, 70),       # dark grey
    "marker": graphics.Color(140, 140, 160),       # muted white
    "info": graphics.Color(120, 120, 130),          # slate grey
    "countdown": graphics.Color(80, 160, 170),      # dim cyan
}

# Layout positions
TITLE_Y = 5           # baseline for "ISS OVERHEAD"
SPRITE_Y = 6          # top of sprite region (sprite is 8px tall)
SPRITE_MID_Y = 10     # vertical center of sprite for trail dots
INFO_Y = 20           # baseline for direction + elevation
PROGRESS_Y = 23       # center row of progress bar
COUNTDOWN_Y = 31      # baseline for countdown text

# ISS sprite
_ISS_IMAGE = None
_ISS_W = 0
_ISS_H = 0


def _load_iss_sprite():
    """Load ISS.png once, return (pixels, width, height)."""
    global _ISS_IMAGE, _ISS_W, _ISS_H
    if _ISS_IMAGE is not None:
        return _ISS_IMAGE, _ISS_W, _ISS_H
    try:
        img_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logos", "ISS_STATION.png")
        img = Image.open(img_path).convert("RGBA")
        _ISS_IMAGE = img.load()
        _ISS_W, _ISS_H = img.size
    except Exception as e:
        logger.warning(f"Failed to load ISS sprite: {e}")
        _ISS_IMAGE = None
        _ISS_W, _ISS_H = 0, 0
    return _ISS_IMAGE, _ISS_W, _ISS_H


def _draw_plus_marker(canvas, x, y, colour):
    """Draw a + shaped marker (like trackedprogress.py plane marker)."""
    canvas.SetPixel(x, y, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x - 1, y, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x + 1, y, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x, y - 1, colour.red, colour.green, colour.blue)
    canvas.SetPixel(x, y + 1, colour.red, colour.green, colour.blue)


class ISSPassScene(object):
    def __init__(self):
        super().__init__()
        self._iss_plane_shown = False
        self._iss_was_active = False
        self._iss_active = False  # checked by other scenes to yield

    @Animator.KeyFrame.add(1)
    def iss_pass_scene(self, count):
        iss = self.overhead.iss_pass_data
        if not iss or not iss["is_active"]:
            if self._iss_was_active:
                # Pass just ended — wipe stale ISS pixels (sprite, progress
                # bar, countdown) that no other scene would overwrite.
                self.canvas.Clear()
                self._iss_was_active = False
                self._iss_plane_shown = False
            self._iss_active = False
            return

        self._iss_was_active = True

        # During ISS pass: allow ONE plane scroll cycle, then suppress
        # If a plane is in zone and we haven't shown it yet, let normal
        # plane display run for one full scroll cycle
        if len(self._data) > 0 and not self._iss_plane_shown:
            self._iss_active = False  # let other scenes draw during cameo
            return

        self._iss_active = True  # suppress other scenes

        # Clear the entire canvas — other scenes don't run during takeover,
        # and clear_screen (keyframe 0) only fires once at startup, so stale
        # clock/weather/forecast pixels persist unless we wipe them here.
        self.canvas.Clear()

        progress = iss["progress"]
        time_remaining = iss["time_remaining_sec"]

        # Real-time visibility check (warm theme if visible, cool if not)
        from utilities.iss import is_iss_visible_now
        import config as cfg
        visible = is_iss_visible_now(cfg.LOCATION_HOME[0], cfg.LOCATION_HOME[1])
        theme = THEME_VISIBLE if visible else THEME_DIM

        # --- 1. Title (rows 0-4) ---
        # Blink: bright 1s, dim 1s (count ticks every frame at 10fps)
        title_colour = theme["title"] if ((count // int(frames.PER_SECOND)) % 2 == 0) else theme["title_dim"]
        title_text = "ISS VISIBLE" if visible else "ISS OVERHEAD"
        title_width = len(title_text) * 4
        title_x = max(0, (screen.WIDTH - title_width) // 2)
        graphics.DrawText(self.canvas, TITLE_FONT, title_x, TITLE_Y, title_colour, title_text)

        # --- 2. ISS sprite moving left-to-right (rows 5-12) ---
        pixels, sprite_w, sprite_h = _load_iss_sprite()
        usable_width = screen.WIDTH - sprite_w
        sprite_x = int(progress * usable_width)
        sprite_x = max(0, min(usable_width, sprite_x))

        # Draw trail dots behind sprite
        trail = theme["trail"]
        for tx in range(0, sprite_x, 2):
            self.canvas.SetPixel(tx, SPRITE_MID_Y,
                                 trail.red, trail.green, trail.blue)

        # Draw ISS sprite
        if pixels:
            for py in range(sprite_h):
                for px in range(sprite_w):
                    r, g, b, a = pixels[px, py]
                    if a > 0:
                        self.canvas.SetPixel(sprite_x + px, SPRITE_Y + py, r, g, b)

        # --- 3. Direction + elevation (row 16) ---
        rise_dir = iss["rise_compass"]
        set_dir = iss["set_compass"]
        max_elev = int(iss["max_elevation"])
        info_text = f"{rise_dir}>{set_dir} {max_elev}\xb0"
        # Center the text
        info_width = len(info_text) * 4
        info_x = max(0, (screen.WIDTH - info_width) // 2)
        graphics.DrawText(self.canvas, INFO_FONT, info_x, INFO_Y, theme["info"], info_text)

        # --- 4. Progress bar (rows 22-24) ---
        bar_width = screen.WIDTH - 4  # leave 2px margin each side
        bar_start = 2
        flown_px = int(progress * bar_width)

        for x in range(bar_width):
            bx = bar_start + x
            if x < flown_px:
                colour = theme["flown"]
            else:
                colour = theme["remaining"]
            # Dashed line: draw every other 2px group
            if (x // 2) % 2 == 0:
                self.canvas.SetPixel(bx, PROGRESS_Y,
                                     colour.red, colour.green, colour.blue)

        # + marker at current position
        marker_x = bar_start + min(flown_px, bar_width - 1)
        _draw_plus_marker(self.canvas, marker_x, PROGRESS_Y, theme["marker"])

        # --- 5. Countdown (rows 27-31) ---
        mins = time_remaining // 60
        secs = time_remaining % 60
        countdown_text = f"{mins}:{secs:02d} LEFT"
        # Center: ~9 chars * 5px = 45px, offset = (64-45)/2 ≈ 10
        countdown_width = len(countdown_text) * 5
        countdown_x = max(0, (screen.WIDTH - countdown_width) // 2)
        graphics.DrawText(self.canvas, COUNTDOWN_FONT, countdown_x, COUNTDOWN_Y,
                          theme["countdown"], countdown_text)
