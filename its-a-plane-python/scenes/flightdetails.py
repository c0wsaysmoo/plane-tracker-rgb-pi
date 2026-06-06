from utilities.animator import Animator
from setup import colours, fonts, screen

from rgbmatrix import graphics

# Setup
FLIGHT_NO_DISTANCE_FROM_TOP = 24
FLIGHT_NO_TEXT_HEIGHT = 8  # based on font size
FLIGHT_NO_FONT = fonts.small

FLIGHT_NUMBER_ALPHA_COLOUR = colours.LIGHT_PURPLE
FLIGHT_NUMBER_NUMERIC_COLOUR = colours.LIGHT_ORANGE

DATA_INDEX_POSITION = (52, 24)
DATA_INDEX_FONT = fonts.extrasmall
DATA_INDEX_COLOUR = colours.GREY


class FlightDetailsScene(object):
    def __init__(self):
        super().__init__()

    @Animator.KeyFrame.add(1)
    def flight_details(self, count):

        # Guard against no data or ISS takeover
        if len(self._data) == 0 or getattr(self, '_iss_active', False):
            return

        # Clear the whole area
        self.draw_square(
            0,
            FLIGHT_NO_DISTANCE_FROM_TOP - FLIGHT_NO_TEXT_HEIGHT,
            screen.WIDTH,
            FLIGHT_NO_DISTANCE_FROM_TOP,
            colours.BLACK,
        )

        # Draw flight number if available
        flight_no_text_length = 0
        callsign = self._data[self._data_index]["callsign"]
        owner_icao = self._data[self._data_index]["owner_icao"]

        if callsign and callsign != "N/A":
            # Remove icao from flight number to get numeric part
            if owner_icao and callsign.startswith(owner_icao):
                flight_no = callsign[len(owner_icao):]
            else:
                flight_no = callsign

            # Use IATA flight number if available (e.g., "BA123")
            iata_flight = self._data[self._data_index].get("flight_number", "")
            if iata_flight:
                flight_no = iata_flight

            # Prepend airline name if available
            airline = self._data[self._data_index].get("airline", "")
            if airline:
                main_text = f"{airline} {flight_no}"
            else:
                main_text = flight_no

            # Colour: airline name + IATA designator in alpha colour,
            # trailing digits in numeric colour.
            # When iata_flight is set (e.g. "B61234"), the designator is
            # always the first 2 chars (IATA codes are 2 chars: B6, AA, F9).
            # When it's a raw callsign with ICAO stripped, it's already numeric-only.
            designator_len = 2 if iata_flight else 0
            alpha_len = (len(airline) + 1 if airline else 0) + designator_len

            for i, ch in enumerate(main_text):
                if i < alpha_len:
                    colour = FLIGHT_NUMBER_ALPHA_COLOUR
                else:
                    colour = (FLIGHT_NUMBER_NUMERIC_COLOUR
                              if ch.isnumeric()
                              else FLIGHT_NUMBER_ALPHA_COLOUR)
                ch_length = graphics.DrawText(
                    self.canvas,
                    FLIGHT_NO_FONT,
                    self._scroll_pos + flight_no_text_length,
                    FLIGHT_NO_DISTANCE_FROM_TOP,
                    colour,
                    ch,
                )
                flight_no_text_length += ch_length

        # Draw page indicator (N/M) for multiple flights
        if len(self._data) > 1:
            # Clear area where N of M might have been
            self.draw_square(
                DATA_INDEX_POSITION[0],
                FLIGHT_NO_DISTANCE_FROM_TOP - FLIGHT_NO_TEXT_HEIGHT,
                screen.WIDTH,
                FLIGHT_NO_DISTANCE_FROM_TOP,
                colours.BLACK,
            )

            # Draw text (fixed position, not part of scroll width)
            graphics.DrawText(
                self.canvas,
                DATA_INDEX_FONT,
                DATA_INDEX_POSITION[0],
                DATA_INDEX_POSITION[1],
                DATA_INDEX_COLOUR,
                f"{self._data_index + 1}/{len(self._data)}",
            )

        # Report width to shared scroll driver
        self.report_scroll_width("flight_details", flight_no_text_length)

    @Animator.KeyFrame.add(0)
    def reset_flight_details_scroll(self):
        pass  # Called by reset_scene(); scroll position owned by Display._scroll_pos
