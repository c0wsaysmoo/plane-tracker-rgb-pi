from utilities.animator import Animator
from setup import colours, fonts
from rgbmatrix import graphics

# Attempt to load config data
try:
    from config import JOURNEY_CODE_SELECTED

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    JOURNEY_CODE_SELECTED = "GLA"

try:
    from config import JOURNEY_BLANK_FILLER

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    JOURNEY_BLANK_FILLER = " ? "

# Setup
JOURNEY_POSITION = (17, 0)
JOURNEY_HEIGHT = 13
JOURNEY_WIDTH = 48
JOURNEY_SPACING = 5
JOURNEY_FONT = fonts.regularplus
JOURNEY_FONT_SELECTED = fonts.regularplus_bold
ARROW_COLOUR = colours.GREY

# Element Positions
ARROW_POINT_POSITION = (41, 8)
ARROW_WIDTH = 3
ARROW_HEIGHT = 6


class JourneyScene(object):
    def __init__(self):
        super().__init__()

    @Animator.KeyFrame.add(0)
    def journey(self):
        # Guard against no data
        if len(self._data) == 0:
            return

        # Grab Airport codes
        origin = self._data[self._data_index]["origin"]
        destination = self._data[self._data_index]["destination"]
        
        # Additional time-related data
        time_scheduled_departure = self._data[self._data_index]["time_scheduled_departure"]
        time_real_departure = self._data[self._data_index]["time_real_departure"]
        time_scheduled_arrival = self._data[self._data_index]["time_scheduled_arrival"]
        time_estimated_arrival = self._data[self._data_index]["time_estimated_arrival"]
        
        # Calculate departure and arrival delays in minutes
        departure_delay_minutes = (
            (time_real_departure - time_scheduled_departure) / 60
            if time_real_departure is not None and time_scheduled_departure is not None
            else 0
        )
        arrival_delay_minutes = (
            (time_estimated_arrival - time_scheduled_arrival) / 60
            if time_estimated_arrival is not None and time_scheduled_arrival is not None
            else 0
        )
        
        # Print time differences for debugging
        #print("Departure Delay (minutes):", departure_delay_minutes)
        #print("Arrival Delay (minutes):", arrival_delay_minutes)
        
        # Set colors based on departure and arrival delays
        if departure_delay_minutes <= 20:
            origin_color = colours.GREEN
        elif 0 < departure_delay_minutes <= 40:
            origin_color = colours.YELLOW
        elif 30 < departure_delay_minutes <= 60:
            origin_color = colours.ORANGE
        else:
            origin_color = colours.RED
        
        if arrival_delay_minutes <= 0:
            destination_color = colours.GREEN
        elif 0 < arrival_delay_minutes <= 30:
            destination_color = colours.YELLOW
        elif 30 < arrival_delay_minutes <= 60:
            destination_color = colours.ORANGE
        else:
            destination_color = colours.RED
        
        # Draw background with the chosen color
        self.draw_square(
            JOURNEY_POSITION[0],
            JOURNEY_POSITION[1],
            JOURNEY_POSITION[0] + JOURNEY_WIDTH - 1,
            JOURNEY_POSITION[1] + JOURNEY_HEIGHT - 1,
            colours.BLACK,
        )

        # Draw origin with the chosen color
        text_length = graphics.DrawText(
            self.canvas,
            JOURNEY_FONT_SELECTED if origin == JOURNEY_CODE_SELECTED else JOURNEY_FONT,
            JOURNEY_POSITION[0],
            JOURNEY_HEIGHT,
            origin_color,
            origin if origin else JOURNEY_BLANK_FILLER,
        )

        # Draw destination with the chosen color
        _ = graphics.DrawText(
            self.canvas,
            JOURNEY_FONT_SELECTED
            if destination == JOURNEY_CODE_SELECTED
            else JOURNEY_FONT,
            JOURNEY_POSITION[0] + text_length + JOURNEY_SPACING,
            JOURNEY_HEIGHT,
            destination_color,
            destination if destination else JOURNEY_BLANK_FILLER,
        )

    @Animator.KeyFrame.add(0)
    def journey_arrow(self):
        # Guard against no data
        if len(self._data) == 0:
            return

        # Black area before arrow
        self.draw_square(
            ARROW_POINT_POSITION[0] - ARROW_WIDTH,
            ARROW_POINT_POSITION[1] - (ARROW_HEIGHT // 2),
            ARROW_POINT_POSITION[0],
            ARROW_POINT_POSITION[1] + (ARROW_HEIGHT // 2),
            colours.BLACK,
        )

        # Starting positions for filled in arrow
        x = ARROW_POINT_POSITION[0] - ARROW_WIDTH
        y1 = ARROW_POINT_POSITION[1] - (ARROW_HEIGHT // 2)
        y2 = ARROW_POINT_POSITION[1] + (ARROW_HEIGHT // 2)

        # Tip of arrow
        self.canvas.SetPixel(
            ARROW_POINT_POSITION[0],
            ARROW_POINT_POSITION[1],
            ARROW_COLOUR.red,
            ARROW_COLOUR.green,
            ARROW_COLOUR.blue,
        )

        # Draw using columns
        for col in range(0, ARROW_WIDTH):
            graphics.DrawLine(
                self.canvas,
                x,
                y1,
                x,
                y2,
                ARROW_COLOUR,
            )

            # Calculate next column's data
            x += 1
            y1 += 1
            y2 -= 1
