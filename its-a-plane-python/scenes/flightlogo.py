from PIL import Image

from utilities.animator import Animator
from setup import colours

LOGO_SIZE = 16
DEFAULT_IMAGE = "default"


def _draw_image_on_canvas(canvas, image, x_offset=0, y_offset=0):
    """Draw a PIL image pixel-by-pixel (avoids Pillow/rgbmatrix unsafe_ptrs crash)."""
    rgb = image.convert("RGB")
    pixels = rgb.load()
    width, height = rgb.size
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            canvas.SetPixel(x + x_offset, y + y_offset, r, g, b)


class FlightLogoScene:
    @Animator.KeyFrame.add(0)
    def logo_details(self):

        # Guard against no data
        if len(self._data) == 0:
            return

        # Clear the whole area
        self.draw_square(
            0,
            0,
            LOGO_SIZE,
            LOGO_SIZE,
            colours.BLACK,
        )

        icao = self._data[self._data_index]["owner_icao"]
        if icao in ("", "N/A"):
            icao = DEFAULT_IMAGE

        # Open the file
        try:
            image = Image.open(f"logos/{icao}.png")
        except FileNotFoundError:
            image = Image.open(f"logos/{DEFAULT_IMAGE}.png")

        # Make image fit our screen.
        try:
            resample = Image.Resampling.LANCZOS  # Pillow 10+
        except AttributeError:
            resample = Image.ANTIALIAS          # Pillow <10
        image.thumbnail((LOGO_SIZE, LOGO_SIZE), resample)

        # Draw pixel-by-pixel (avoids Pillow/rgbmatrix SetImage incompatibility)
        _draw_image_on_canvas(self.canvas, image)
