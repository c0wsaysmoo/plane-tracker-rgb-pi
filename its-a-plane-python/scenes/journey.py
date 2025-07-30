from utilities.animator import Animator
from setup import colours, fonts
from rgbmatrix import graphics
from config import DISTANCE_UNITS

try:
    from config import JOURNEY_CODE_SELECTED
except (ModuleNotFoundError, NameError, ImportError):
    JOURNEY_CODE_SELECTED = "GLA"

try:
    from config import JOURNEY_BLANK_FILLER
except (ModuleNotFoundError, NameError, ImportError):
    JOURNEY_BLANK_FILLER = " ? "

JOURNEY_POSITION = (17, 0)
JOURNEY_HEIGHT = 10
JOURNEY_WIDTH = 48
JOURNEY_SPACING = 5
JOURNEY_FONT = fonts.regularplus
JOURNEY_FONT_SELECTED = fonts.regularplus_bold
ARROW_COLOUR = colours.GREY
DISTANCE_ORIGIN_COLOUR = colours.LIGHT_GREEN
DISTANCE_DESTINATION_COLOUR = colours.LIGHT_LIGHT_RED
DISTANCE_COLOUR = colours.LIGHT_TEAL
DISTANCE_MEASURE = colours.LIGHT_DARK_TEAL
DISTANCE_POSITION = (17, 16)
DISTANCE_WIDTH = 48
DISTANCE_FONT = fonts.extrasmall

ARROW_POINT_POSITION = (42, 5)
ARROW_WIDTH = 5
ARROW_HEIGHT = 8
    
class JourneyScene(object):
    def __init__(self):
        super().__init__()
        self._last_debug_print = None

    @Animator.KeyFrame.add(0)
    def journey(self):
        if len(self._data) == 0:
            return

        origin = self._data[self._data_index]["origin"]
        destination = self._data[self._data_index]["destination"]

        dist_origin = self._data[self._data_index]["distance_origin"]
        dist_destination = self._data[self._data_index]["distance_destination"]
        time_estimated_arrival = self._data[self._data_index]["time_estimated_arrival"]
        time_scheduled_arrival = self._data[self._data_index]["time_scheduled_arrival"]
        time_real_departure = self._data[self._data_index]["time_real_departure"]
        time_scheduled_departure = self._data[self._data_index]["time_scheduled_departure"]

        if DISTANCE_UNITS == "imperial":
            distance_units = "mi"
        elif DISTANCE_UNITS == "metric":
            distance_units = "KM"
        else:
            distance_units = "Units"

        distance_origin_text = f'{dist_origin:.0f}{distance_units}'
        distance_destination_text = f'{dist_destination:.0f}{distance_units}'

        departure_delay_minutes = (
            (time_real_departure - time_scheduled_departure) / 60
            if time_real_departure is not None and time_scheduled_departure is not None
            else None
        )

        arrival_delay_minutes = (
            (time_estimated_arrival - time_scheduled_arrival) / 60
            if time_estimated_arrival is not None and time_scheduled_arrival is not None
            else None
        )
        
        # Print time differences for debugging
        #print(f"Origin: {origin}, Departure Delay (minutes): {departure_delay_minutes}")
        #print(f"Destination: {destination}, Arrival Delay (minutes): {arrival_delay_minutes}")
        
        if departure_delay_minutes is None:
            destination_color = colours.LIGHT_GREY
        elif departure_delay_minutes <= 20:
            origin_color = colours.LIGHT_MID_GREEN
        elif 20 < departure_delay_minutes <= 40:
            origin_color = colours.LIGHT_YELLOW
        elif 40 < departure_delay_minutes <= 60:
            origin_color = colours.LIGHT_MID_ORANGE
        elif 60 < departure_delay_minutes <= 240:
            origin_color = colours.LIGHT_RED
        elif 240 < departure_delay_minutes <= 480:
            origin_color = colours.LIGHT_PURPLE
        else:
            origin_color = colours.LIGHT_DARK_BLUE

        if arrival_delay_minutes is None:
            destination_color = colours.LIGHT_GREY
        elif arrival_delay_minutes <= 0:
            destination_color = colours.LIGHT_MID_GREEN
        elif 0 < arrival_delay_minutes <= 30:
            destination_color = colours.LIGHT_YELLOW
        elif 30 < arrival_delay_minutes <= 60:
            destination_color = colours.LIGHT_MID_ORANGE
        elif 60 < arrival_delay_minutes <= 240:
            destination_color = colours.LIGHT_RED
        elif 240 < arrival_delay_minutes <= 480:
            destination_color = colours.LIGHT_PURPLE
        else:
            destination_color = colours.LIGHT_DARK_BLUE

        self.draw_square(
            JOURNEY_POSITION[0],
            JOURNEY_POSITION[1],
            JOURNEY_POSITION[0] + JOURNEY_WIDTH - 1,
            JOURNEY_POSITION[1] + JOURNEY_HEIGHT - 1,
            colours.BLACK,
        )

        text_length = graphics.DrawText(
            self.canvas,
            JOURNEY_FONT_SELECTED if origin == JOURNEY_CODE_SELECTED else JOURNEY_FONT,
            JOURNEY_POSITION[0],
            JOURNEY_HEIGHT,
            origin_color,
            origin if origin else JOURNEY_BLANK_FILLER,
        )

        _ = graphics.DrawText(
            self.canvas,
            JOURNEY_FONT_SELECTED if destination == JOURNEY_CODE_SELECTED else JOURNEY_FONT,
            JOURNEY_POSITION[0] + text_length + JOURNEY_SPACING + 1,
            JOURNEY_HEIGHT,
            destination_color,
            destination if destination else JOURNEY_BLANK_FILLER,
        )

        center_x = (16 + 64) // 2
        half_width = (64 - 16) // 2
        font_character_width = 4

        distance_origin_text_width = len(distance_origin_text) * font_character_width
        distance_destination_text_width = len(distance_destination_text) * font_character_width

        distance_origin_x = center_x - half_width + (half_width - distance_origin_text_width) // 2
        distance_destination_x = center_x + (half_width - distance_destination_text_width) // 2

        distance_origin_text_length = 0
        for ch in distance_origin_text:
            ch_length = graphics.DrawText(
                self.canvas,
                DISTANCE_FONT,
                distance_origin_x + distance_origin_text_length,
                DISTANCE_POSITION[1],
                DISTANCE_COLOUR if ch.isnumeric() else DISTANCE_MEASURE,
                ch,
            )
            distance_origin_text_length += ch_length

        distance_destination_text_length = 0
        for ch in distance_destination_text:
            ch_length = graphics.DrawText(
                self.canvas,
                DISTANCE_FONT,
                distance_destination_x + distance_destination_text_length,
                DISTANCE_POSITION[1],
                DISTANCE_COLOUR if ch.isnumeric() else DISTANCE_MEASURE,
                ch,
            )
            distance_destination_text_length += ch_length

    @Animator.KeyFrame.add(0)
    def journey_arrow(self):
        if len(self._data) == 0:
            return

        self.draw_square(
            ARROW_POINT_POSITION[0] - ARROW_WIDTH,
            ARROW_POINT_POSITION[1] - (ARROW_HEIGHT // 2),
            ARROW_POINT_POSITION[0],
            ARROW_POINT_POSITION[1] + (ARROW_HEIGHT // 2),
            colours.BLACK,
        )

        x = ARROW_POINT_POSITION[0] - ARROW_WIDTH + 1
        y1 = ARROW_POINT_POSITION[1] - (ARROW_HEIGHT // 2)
        y2 = ARROW_POINT_POSITION[1] + (ARROW_HEIGHT // 2)

        distance_origin = int(self._data[self._data_index]["distance_origin"])
        distance_destination = int(self._data[self._data_index]["distance_destination"])

        if distance_origin == 0 and distance_destination == 0:
            for _ in range(ARROW_WIDTH):
                graphics.DrawLine(self.canvas, x, y1, x, y2, ARROW_COLOUR)
                x += 1
                y1 += 1
                y2 -= 1
        elif distance_origin == 0 or distance_destination == 0:
            for _ in range(ARROW_WIDTH):
                graphics.DrawLine(self.canvas, x, y1, x, y2, ARROW_COLOUR)
                x += 1
                y1 += 1
                y2 -= 1
        else:
            total_distance = distance_origin + distance_destination
            origin_ratio = distance_origin / total_distance

            total_pixels = ARROW_WIDTH

            if origin_ratio <= 0.10:
                origin_pixels = 0
            elif origin_ratio <= 0.30:
                origin_pixels = 1
            elif origin_ratio <= 0.50:
                origin_pixels = 2
            elif origin_ratio <= 0.70:
                origin_pixels = 3
            elif origin_ratio <= 0.90:
                origin_pixels = 4
            else:
                origin_pixels = 5

            destination_pixels = total_pixels - origin_pixels

            for _ in range(origin_pixels):
                graphics.DrawLine(self.canvas, x, y1, x, y2, DISTANCE_ORIGIN_COLOUR)
                x += 1
                y1 += 1
                y2 -= 1

            for _ in range(destination_pixels):
                graphics.DrawLine(self.canvas, x, y1, x, y2, DISTANCE_DESTINATION_COLOUR)
                x += 1
                y1 += 1
                y2 -= 1
