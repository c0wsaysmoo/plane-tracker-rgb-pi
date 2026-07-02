from utilities.animator import Animator
from utilities import textclip
from setup import colours, fonts, screen

from rgbmatrix import graphics

# Setup
FLIGHT_NO_DISTANCE_FROM_TOP = 24
FLIGHT_NO_TEXT_HEIGHT = 8  # based on font size
FLIGHT_NO_FONT = fonts.small
FLIGHT_NO_CLIP_FONT = textclip.small  # same 5x8.bdf, for boundary clipping

FLIGHT_NUMBER_ALPHA_COLOUR = colours.LIGHT_PURPLE
FLIGHT_NUMBER_NUMERIC_COLOUR = colours.LIGHT_ORANGE

DATA_INDEX_POSITION = (52, 24)
DATA_INDEX_TEXT_HEIGHT = 7
DATA_INDEX_FONT = fonts.extrasmall
DATA_INDEX_CLIP_FONT = textclip.extrasmall  # width measurement only

DATA_INDEX_COLOUR = colours.GREY

# The canvas is live (sync() discards SwapOnVSync's return, so self.canvas
# IS the displayed framebuffer). Every write is visible immediately —
# flicker-free rendering requires that the indicator zone (x >= 52) is
# written at most once per page change, never per frame. Scroll text is
# therefore clipped at the zone edge instead of drawn through and stamped.


class FlightDetailsScene(object):
    def __init__(self):
        super().__init__()
        self.flight_position = screen.WIDTH
        self.flight_details_complete = False
        self._data_all_looped = False
        self._indicator_state = "reset"

    @Animator.KeyFrame.add(1)
    def flight_details(self, count):

        # Guard against no data
        if len(self._data) == 0:
            # Other scenes own the canvas; force an indicator redraw
            # when flight data returns.
            self._indicator_state = "reset"
            return

        # Skip rendering after scroll complete (waiting for other regions)
        if self.flight_details_complete:
            return

        has_indicator = len(self._data) > 1

        # Scroll text may use the full width when there is no page
        # indicator; with one there, it is clipped at the zone edge so
        # the zone is never touched by per-frame draws.
        boundary = DATA_INDEX_POSITION[0] if has_indicator else screen.WIDTH

        # Clear the scroll zone only — never the indicator zone
        self.draw_square(
            0,
            FLIGHT_NO_DISTANCE_FROM_TOP - FLIGHT_NO_TEXT_HEIGHT,
            boundary,
            FLIGHT_NO_DISTANCE_FROM_TOP,
            colours.BLACK,
        )

        # Draw bar: once per page change, then leave untouched. On a live
        # canvas any per-frame rewrite here is visible flicker. Runs before
        # the text draw so a multi->single transition can't stamp black
        # over text just drawn at full width.
        indicator_text_length = 0
        if has_indicator:
            indicator_text = f"{self._data_index + 1}/{len(self._data)}"
            # Same value DrawText would return (advance = DWIDTH); counted
            # into the line length below so scroll timing is unchanged
            indicator_text_length = sum(
                DATA_INDEX_CLIP_FONT.advance(ch) for ch in indicator_text
            )
            indicator_state = (self._data_index, len(self._data))
        else:
            indicator_state = None

        if self._indicator_state != indicator_state:
            # Clear area where N of M might have been
            self.draw_square(
                DATA_INDEX_POSITION[0],
                FLIGHT_NO_DISTANCE_FROM_TOP - FLIGHT_NO_TEXT_HEIGHT,
                screen.WIDTH,
                FLIGHT_NO_DISTANCE_FROM_TOP,
                colours.BLACK,
            )

            if has_indicator:
                # Draw text
                graphics.DrawText(
                    self.canvas,
                    DATA_INDEX_FONT,
                    DATA_INDEX_POSITION[0],
                    DATA_INDEX_POSITION[1],
                    DATA_INDEX_COLOUR,
                    indicator_text,
                )

            self._indicator_state = indicator_state

        # Draw flight number if available, clipped per pixel column at the
        # boundary. Characters enter column-by-column at x=52 exactly as
        # they do at the hardware edge in single-flight mode.
        flight_no_text_length = 0
        callsign = self._data[self._data_index]["callsign"]
        owner_icao = self._data[self._data_index]["owner_icao"]

        if callsign and callsign != "N/A":
            airline = self._data[self._data_index]["airline"]

            # For private flights keep the full registration; for airlines strip the ICAO prefix
            if airline == "Private":
                flight_no = callsign
            elif owner_icao and callsign.startswith(owner_icao):
                flight_no = callsign[len(owner_icao):]
            else:
                flight_no = callsign

            if airline:
                flight_no = f"{airline} {flight_no}"

            for ch in flight_no:
                colour = (
                    FLIGHT_NUMBER_NUMERIC_COLOUR
                    if ch.isnumeric()
                    else FLIGHT_NUMBER_ALPHA_COLOUR
                )

                char_x = self.flight_position + flight_no_text_length
                advance = FLIGHT_NO_CLIP_FONT.advance(ch)

                if char_x + advance <= boundary:
                    # Fully left of the boundary — fast C++ draw
                    graphics.DrawText(
                        self.canvas,
                        FLIGHT_NO_FONT,
                        char_x,
                        FLIGHT_NO_DISTANCE_FROM_TOP,
                        colour,
                        ch,
                    )
                elif char_x < boundary:
                    # Straddles the boundary — draw only columns < boundary
                    FLIGHT_NO_CLIP_FONT.draw_char_clipped(
                        self.canvas,
                        char_x,
                        FLIGHT_NO_DISTANCE_FROM_TOP,
                        colour,
                        ch,
                        x_max=boundary,
                    )
                # else: fully inside the indicator zone — draw nothing, but
                # still count the advance so scroll timing is unchanged

                flight_no_text_length += advance

        # Count the whole line length
        flight_no_text_length += indicator_text_length

        # Handle scrolling
        self.flight_position -= 1
        if self.flight_position + flight_no_text_length < 0:
            if len(self._data) > 1:
                # Mark complete and wait for other regions
                self.flight_details_complete = True
                self.mark_scroll_complete("flight_details")
            else:
                self.flight_position = screen.WIDTH

    @Animator.KeyFrame.add(0)
    def reset_flight_details_scroll(self):
        self.flight_position = screen.WIDTH
        self.flight_details_complete = False
        # reset_scene() also runs clear_screen(), which wipes the canvas —
        # the indicator must repaint on the next frame even when the page
        # state tuple is unchanged (e.g. new flight list, index still 0)
        self._indicator_state = "reset"

