from utilities.animator import Animator
from setup import colours

BLINKER_POSITION = (63, 0)
BLINKER_STEPS    = 30

SOURCE_COLOURS = {
    "AirLabs":     colours.YELLOW,
    "FlightAware": colours.CYAN,
    "FR24":        colours.GREEN,
    "MasterOK":    colours.GREEN,
    "MasterError": colours.RED,
}
COLOUR_OPENSKY = colours.WHITE


class LoadingPulseScene(object):
    def __init__(self):
        super().__init__()

    @Animator.KeyFrame.add(2)
    def loading_pulse(self, count):
        reset_count = True

        if self.overhead.processing:
            source = getattr(self.overhead, "last_source", None)
            blinker_colour = SOURCE_COLOURS.get(source, COLOUR_OPENSKY)

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
            self.canvas.SetPixel(BLINKER_POSITION[0], BLINKER_POSITION[1], 0, 0, 0)

        return reset_count
