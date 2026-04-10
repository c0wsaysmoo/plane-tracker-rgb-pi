from utilities.animator import Animator
from setup import colours, screen
from config import NIGHT_START, NIGHT_END
from datetime import datetime

LINE2_Y = 23

FLOWN_COLOUR    = colours.LIGHT_GREEN       # dashes behind plane (flown)
REMAINING_COLOUR = colours.LIGHT_LIGHT_RED     # dashes ahead of plane (remaining)
PLANE_COLOUR    = colours.WHITE           # plane icon

NIGHT_START_TIME = datetime.strptime(NIGHT_START, "%H:%M")
NIGHT_END_TIME   = datetime.strptime(NIGHT_END,   "%H:%M")


def _calc_progress(data):
    dist_remaining = data.get("dist_remaining")
    total_distance = data.get("total_distance")
    if dist_remaining is None or not total_distance or total_distance <= 0:
        return 0.0
    dist_flown = total_distance - dist_remaining
    return max(0.0, min(1.0, dist_flown / total_distance))


def _draw_plane(canvas, x, y):
    """
    Draw a small white plane icon (7px wide, 3px tall) at position x, y (centre).
    Shape (roughly):
      . ^ .
      >>>>.
      . v .
    Pixel layout:
      row y-1:  x+2
      row y:    x, x+1, x+2, x+3, x+4
      row y+1:  x+2
    Looks like a side-on plane pointing right.
    """
    c = PLANE_COLOUR
    # Fuselage (horizontal line)
    for px in range(x, x + 4):
        canvas.SetPixel(px, y, c.red, c.green, c.blue)
    # Wing (vertical pixel above and below centre)
    canvas.SetPixel(x + 2, y - 1, c.red, c.green, c.blue)
    canvas.SetPixel(x + 2, y + 1, c.red, c.green, c.blue)


PLANE_WIDTH = 4   # must match _draw_plane fuselage width


class TrackedProgressScene(object):
    def __init__(self):
        super().__init__()
        self._tp_redraw = True
 
    @Animator.KeyFrame.add(0)
    def reset_tracked_progress(self):
        self.draw_square(0, LINE2_Y - 3, screen.WIDTH, LINE2_Y + 1, colours.BLACK)
        self._tp_redraw = True
 
    @Animator.KeyFrame.add(1)
    def tracked_progress(self, count):
        # Force redraw at brightness transition times
        now = datetime.now().replace(microsecond=0).time()
        if now == NIGHT_START_TIME.time() or now == NIGHT_END_TIME.time():
            self._tp_redraw = True
            return
 
        if len(self._data) > 0:
            self._tp_redraw = True
            return
 
        tracked = self.overhead.tracked_data
        if not tracked:
            self._tp_redraw = True
            return
 
        progress = _calc_progress(tracked)
        width  = screen.WIDTH
        mid_y  = LINE2_Y - 1
        usable = width - PLANE_WIDTH
        plane_x = max(0, min(usable, int(progress * usable)))
 
        self.draw_square(0, LINE2_Y - 3, width, LINE2_Y + 1, colours.BLACK)
 
        for x in range(plane_x):
            if (x // 2) % 2 == 0:
                self.canvas.SetPixel(x, mid_y,
                    FLOWN_COLOUR.red, FLOWN_COLOUR.green, FLOWN_COLOUR.blue)
 
        for x in range(plane_x + PLANE_WIDTH, width):
            if (x // 2) % 2 == 0:
                self.canvas.SetPixel(x, mid_y,
                    REMAINING_COLOUR.red, REMAINING_COLOUR.green, REMAINING_COLOUR.blue)
 
        _draw_plane(self.canvas, plane_x, mid_y)
        self._tp_redraw = False