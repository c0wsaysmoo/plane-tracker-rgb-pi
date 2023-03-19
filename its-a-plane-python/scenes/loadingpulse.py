from utilities.animator import Animator
from setup import colours

# Setup
BLINKER_POSITION = (63, 0)
BLINKER_STEPS = 10
BLINKER_COLOUR = colours.WHITE


class LoadingPulseScene(object):
    def __init__(self):
        super().__init__()

    @Animator.KeyFrame.add(2)
    def loading_pulse(self, count):
        reset_count = True
        if self.overhead.processing:
            # Calculate the brightness scaler and
            # ensure it's within a sensible range
            brightness = (1 - (count / BLINKER_STEPS)) / 2
            brightness = 0 if (brightness < 0 or brightness > 1) else brightness

            self.canvas.SetPixel(
                BLINKER_POSITION[0],
                BLINKER_POSITION[1],
                brightness * BLINKER_COLOUR.red,
                brightness * BLINKER_COLOUR.green,
                brightness * BLINKER_COLOUR.blue,
            )

            # Only count 0 -> (BLINKER_STEPS - 1)
            reset_count = count == (BLINKER_STEPS - 1)
        else:
            # Not processing, blank the square
            self.canvas.SetPixel(BLINKER_POSITION[0], BLINKER_POSITION[1], 0, 0, 0)

        return reset_count
