import os

# Load .env file if present (secrets should live in .env, not in this file)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass  # python-dotenv not installed; rely on system environment variables

ZONE_HOME = {
    "tl_y": 51.595, # Top-Left Latitude (deg) https://www.latlong.net/ or google maps. The bigger the zone, the more planes you'll get. My zone is ~3.5 miles in each direction or 10mi corner to corner.
    "tl_x": -0.314, # Top-Left Longitude (deg)
    "br_y": 51.47, # Bottom-Right Latitude (deg)
    "br_x": -0.111 # Bottom-Right Longitude (deg)
}
LOCATION_HOME = [
    51.55864, # Latitude (deg)
    -0.177332 # Longitude (deg)
]
TEMPERATURE_LOCATION = "51.55864,-0.177332" #same as location home
TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY", "") # Get an API key from https://tomorrow.io they only allows 25 pulls an hour, if you reach the limit you'll need to wait until the next hour
TEMPERATURE_UNITS = "metric" #can use "metric" if you want, same for distance
DISTANCE_UNITS = "imperial"
CLOCK_FORMAT = "12hr" #use 12hr or 24hr
MIN_ALTITUDE = 1000 #feet above sea level. If you live at 1000ft then you'd want to make yours ~3000 etc. I use 2000 to weed out some of the smaller general aviation traffic.
BRIGHTNESS = 100
BRIGHTNESS_NIGHT = 50
NIGHT_BRIGHTNESS = True #True for on False for off
NIGHT_START = "20:00" #dims screen between these hours
NIGHT_END = "06:00"
GPIO_SLOWDOWN = 2 #depends what Pi you have I use 2 for Pi 3 and 1 for Pi Zero
JOURNEY_CODE_SELECTED = "LHR" #your home airport code ALL CAPS ie ORD
JOURNEY_BLANK_FILLER = " ? " #what to display if theres no airport code
HAT_PWM_ENABLED = True #only if you haven't soldered the PWM bridge use True if you did
FORECAST_DAYS = 3 #today plus the next two days
EMAIL = "" #"robk@robk.com" #insert your email address between the " ie "example@example.com" to recieve emails when there is a new top 3 flight. Leave "" to recieve no emails. It will log/local webpage regardless
MAX_FARTHEST = 3 #the amount of furthest flights you want in your log
MAX_CLOSEST = 3 #the amount of closest flights to your house you want in your log
FR24_API_KEY = os.environ.get("FR24_API_KEY", "") # FlightRadar24 official API key (format: "subscription_key|token")