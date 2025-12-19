from PIL import Image

from utilities.animator import Animator
from setup import colours

LOGO_SIZE = 16
DEFAULT_IMAGE = "default"

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
        image.thumbnail((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)

        self.matrix.SetImage(image.convert('RGB'))
