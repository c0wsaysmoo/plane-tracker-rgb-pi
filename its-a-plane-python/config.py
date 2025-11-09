ZONE_HOME = {
    "tl_y": xx.xxxxxx, # Top-Left Latitude (deg) https://www.latlong.net/ or google maps. The bigger the zone, the more planes you'll get. My zone is ~3.5 miles in each direction or 10mi corner to corner. 
    "tl_x": xx.xxxxxx, # Top-Left Longitude (deg)
    "br_y": xx.xxxxxx, # Bottom-Right Latitude (deg)
    "br_x": xx.xxxxxx # Bottom-Right Longitude (deg)
}
LOCATION_HOME = [
    xx.xxxxxx, # Latitude (deg)
    xx.xxxxxx # Longitude (deg)
]
TEMPERATURE_LOCATION = "xx.xxxxxx,xx.xxxxxx" #same as location home
TOMORROW_API_KEY = "xxxxxxx" # Get an API key from https://tomorrow.io they only allows 25 pulls an hour, if you reach the limit you'll need to wait until the next hour 
TEMPERATURE_UNITS = "imperial" #can use "metric" if you want, same for distance 
DISTANCE_UNITS = "imperial"
CLOCK_FORMAT = "12hr" #use 12hr or 24hr
MIN_ALTITUDE = 2600 #feet above sea level. If you live at 1000ft then you'd want to make yours ~3600 etc. I use 2600 to weed out some of the smaller general aviation traffic. 
BRIGHTNESS = 100
BRIGHTNESS_NIGHT = 50
NIGHT_BRIGHTNESS = False #True for on False for off
NIGHT_START = "22:00" #dims screen between these hours
NIGHT_END = "06:00"
GPIO_SLOWDOWN = 2 #depends what Pi you have I use 2 for Pi 3 and 1 for Pi Zero
JOURNEY_CODE_SELECTED = "xxx" #your home airport code
JOURNEY_BLANK_FILLER = " ? " #what to display if theres no airport code
HAT_PWM_ENABLED = False #only if you haven't soldered the PWM bridge use True if you did
FORECAST_DAYS = 3 #today plus the next two days
EMAIL = "" #insert your email address between the " ie "example@example.com" to recieve emails when there is a new closest flight on the tracker. Leave "" to recieve no emails. It will log regardless
MAX_FARTHEST = 3 #the amount of furthest flights you want in your log

