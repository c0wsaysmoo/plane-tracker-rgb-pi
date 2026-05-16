from utilities.animator import Animator
from setup import colours

# Setup
BLINKER_POSITION = (63, 0)
BLINKER_STEPS    = 30
COLOUR_OK        = colours.GREEN  # FR24 working
COLOUR_ERROR     = colours.RED        # FR24 blocked/failed


class LoadingPulseScene(object):
    def __init__(self):
        super().__init__()

    @Animator.KeyFrame.add(2)
    def loading_pulse(self, count):
        reset_count = True

        if self.overhead.processing:
            # Pick colour based on FR24 status
            blinker_colour = COLOUR_OK if self.overhead.fr24_ok else COLOUR_ERROR

            # Calculate pulsing brightness
            brightness = (1 - (count / BLINKER_STEPS)) / 2
            brightness = 0 if (brightness < 0 or brightness > 1) else brightness

            self.canvas.SetPixel(
                BLINKER_POSITION[0],
                BLINKER_POSITION[1],
                int(brightness * blinker_colour.red),
                int(brightness * blinker_colour.green),
                int(brightness * blinker_colour.blue),
            )
            reset_count = count == (BLINKER_STEPS - 1)
        else:
            # Not processing — blank the pixel
            self.canvas.SetPixel(BLINKER_POSITION[0], BLINKER_POSITION[1], 0, 0, 0)

        return reset_count
