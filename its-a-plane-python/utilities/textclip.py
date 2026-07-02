import os

# Column-clipped glyph rendering.
#
# graphics.DrawText draws whole glyphs only, so text scrolling toward a
# software boundary (the page indicator zone at x=52) either bleeds past
# it or pops in as full characters. This module parses the BDF fonts
# directly and draws the boundary-straddling character pixel-by-pixel
# with SetPixel, clipped at the boundary column.
#
# Pixel placement matches the library's Font::DrawGlyph (bdf-font.cc)
# exactly, so clipped characters line up with DrawText output:
#   - column = bitmap column + BBX x_offset, drawn only while < DWIDTH
#   - top row = baseline - BBX height - BBX y_offset
#   - advance = DWIDTH

REPLACEMENT_CODEPOINT = 0xFFFD


class ClipFont(object):
    def __init__(self, path):
        # codepoint -> (device_width, height, y_offset, pixels)
        # pixels: tuple of (col, row) offsets, x_offset already applied
        self.glyphs = {}
        self._load(path)

    def _load(self, path):
        encoding = None
        dwidth = None
        bbx = None
        rows = None
        with open(path, "r") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                key = parts[0]
                if key == "ENCODING":
                    encoding = int(parts[1])
                elif key == "DWIDTH":
                    dwidth = int(parts[1])
                elif key == "BBX":
                    bbx = tuple(int(v) for v in parts[1:5])
                elif key == "BITMAP":
                    rows = []
                elif key == "ENDCHAR":
                    if encoding is not None and dwidth is not None and bbx and rows is not None:
                        self.glyphs[encoding] = self._make_glyph(dwidth, bbx, rows)
                    encoding = dwidth = bbx = rows = None
                elif rows is not None and bbx and len(rows) < bbx[1]:
                    rows.append(parts[0])

    @staticmethod
    def _make_glyph(dwidth, bbx, rows):
        width, height, x_offset, y_offset = bbx
        pixels = []
        for row_index, hex_row in enumerate(rows):
            bits = int(hex_row, 16)
            n_bits = 4 * len(hex_row)
            for col in range(width):
                if bits >> (n_bits - 1 - col) & 1:
                    x = col + x_offset
                    if 0 <= x < dwidth:
                        pixels.append((x, row_index))
        # empty glyphs (space) get min_row=height: no pixels, no intrusion
        min_row = min((r for (_, r) in pixels), default=height)
        return (dwidth, height, y_offset, tuple(pixels), min_row)

    def advance(self, ch):
        glyph = self.glyphs.get(ord(ch)) or self.glyphs.get(REPLACEMENT_CODEPOINT)
        return glyph[0] if glyph else 0

    def glyph_top(self, ch, baseline):
        """Topmost pixel row this character paints when drawn at baseline.

        Lets callers detect glyphs that intrude into the line above
        (e.g. '@' in 5x8 uses bitmap row 0) without drawing anything.
        """
        glyph = self.glyphs.get(ord(ch)) or self.glyphs.get(REPLACEMENT_CODEPOINT)
        if not glyph:
            return baseline
        device_width, height, y_offset, pixels, min_row = glyph
        return baseline - height - y_offset + min_row

    def draw_char_clipped(self, canvas, x, y, colour, ch, x_max=None, y_min=None):
        """Draw one character at baseline (x, y), skipping columns >= x_max
        and rows < y_min.

        Returns the advance width, same as graphics.DrawText.
        """
        glyph = self.glyphs.get(ord(ch)) or self.glyphs.get(REPLACEMENT_CODEPOINT)
        if not glyph:
            return 0
        device_width, height, y_offset, pixels, _ = glyph
        top = y - height - y_offset
        red, green, blue = colour.red, colour.green, colour.blue
        for px, py in pixels:
            col = x + px
            row = top + py
            if col < 0 or (x_max is not None and col >= x_max):
                continue
            if y_min is not None and row < y_min:
                continue
            canvas.SetPixel(col, row, red, green, blue)
        return device_width


_DIR_PATH = os.path.dirname(os.path.realpath(__file__))
small = ClipFont(os.path.join(_DIR_PATH, "..", "fonts", "5x8.bdf"))
extrasmall = ClipFont(os.path.join(_DIR_PATH, "..", "fonts", "4x6.bdf"))
