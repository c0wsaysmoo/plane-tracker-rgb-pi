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
SPEED_UNITS = "imperial"  # imperial (mph), metric (km/h), or knots (knts)
CLOCK_FORMAT = "12hr" #use 12hr or 24hr
MIN_ALTITUDE = 2000 #feet above sea level. If you live at 1000ft then you'd want to make yours ~3000 etc. I use 2000 to weed out some of the smaller general aviation traffic. 
BRIGHTNESS = 100
BRIGHTNESS_NIGHT = 50
NIGHT_BRIGHTNESS = False #True for on False for off
NIGHT_START = "22:00" #dims screen between these hours
NIGHT_END = "06:00"
GPIO_SLOWDOWN = 2 #depends what Pi you have I use 2 for Pi 3 and 1 for Pi Zero
JOURNEY_CODE_SELECTED = "XXX" #your home airport code ALL CAPS ie ORD
JOURNEY_BLANK_FILLER = " ? " #what to display if theres no airport code
HAT_PWM_ENABLED = False #only if you haven't soldered the PWM bridge use True if you did
FORECAST_DAYS = 3 #today plus the next two days
EMAIL = "" #insert your email address between the " ie "example@example.com" to recieve emails when there is a new top 3 flight. Leave "" to recieve no emails. It will log/local webpage regardless
MAX_FARTHEST = 3 #the amount of furthest flights you want in your log
MAX_CLOSEST = 3 #the amount of closest flights to your house you want in your log
SEARCH_RADIUS_NM = 10 #nautical miles from LOCATION_HOME to search for aircraft. If not set, computed from ZONE_HOME bounding box (minimum 10nm)

# --- Route Fallback Chain ---
# Sources tried in order when adsbdb fails or returns stale data.
# Available sources and their tradeoffs:
#   "fr24"         — FR24 official REST API (flight-summary/light). $9/month Explorer plan (30K credits).
#                    Best coverage. Has schedule times (delay colors). 1 credit/live flight.
#   "airlabs"      — AirLabs /flight endpoint. Free, 1000 calls/month per key.
#                    Good commercial coverage. No schedule times. No GA/private.
#   "flightaware"  — FlightAware AeroAPI. $5/month free credit (~830 calls at $0.006/call).
#                    Best GA/private coverage. Has schedule times.
# Without any keys configured, the system works on adsb.lol + adsbdb alone (~70% route coverage).
#ROUTE_FALLBACK_CHAIN = ["airlabs", "flightaware"]  # free tiers only (default if keys set)
#ROUTE_FALLBACK_CHAIN = ["fr24"]  # FR24 official REST API ($9/month)
#ROUTE_FALLBACK_CHAIN = ["fr24", "airlabs", "flightaware"]  # try everything
#FLIGHTRADAR24_KEY = "" #optional: Bearer token from https://fr24api.flightradar24.com/ ($9/month Explorer)
#AIRLABS_API_KEY = "" #optional: free API key from https://airlabs.co/ (1000 calls/month)
#FLIGHTAWARE_API_KEY = "" #optional: API key from https://www.flightaware.com/aeroapi/portal/ ($5/month free credit)
#FLIGHTAWARE_MONTHLY_LIMIT = 4.50 #optional: stop calling FlightAware after this many dollars spent
