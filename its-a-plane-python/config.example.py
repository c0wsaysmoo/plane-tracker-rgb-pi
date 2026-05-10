# =============================================================================
# LOCATION
# =============================================================================

ZONE_HOME = {
    "tl_y": xx.xxxxxx, # Top-Left Latitude (deg) https://www.latlong.net/ or google maps. The bigger the zone, the more planes you'll get. My zone is ~3.5 miles in each direction or 10mi corner to corner. 
    "tl_x": xx.xxxxxx, # Top-Left Longitude (deg)
    "br_y": xx.xxxxxx, # Bottom-Right Latitude (deg)
    "br_x": xx.xxxxxx # Bottom-Right Longitude (deg)
}

LOCATION_HOME = [xx.xxx, xx.xxx]     # Home lat/lon
TEMPERATURE_LOCATION = "xx.xxx,xx.xxx" #same as location home
JOURNEY_CODE_SELECTED = "XXX"              # Home airport IATA code
JOURNEY_BLANK_FILLER  = " ? "

# =============================================================================
# DISPLAY
# =============================================================================

CLOCK_FORMAT     = "12hr"       # 12hr or 24hr
TEMPERATURE_UNITS = "imperial"  # imperial or metric
DISTANCE_UNITS   = "imperial"   # imperial or metric
SPEED_UNITS      = "imperial"   # imperial (mph), metric (kph), or knots (kts)
BRIGHTNESS       = 100
BRIGHTNESS_NIGHT = 50
NIGHT_BRIGHTNESS = False #True for on False for off
NIGHT_START      = "22:00"
NIGHT_END        = "06:00"
GPIO_SLOWDOWN    = 2 #depends what Pi you have I use 2 for Pi 3 and 1 for Pi Zero
HAT_PWM_ENABLED  = False #only if you haven't soldered the PWM bridge use True if you did
FORECAST_DAYS    = 3            # Today + next N-1 days
MIN_ALTITUDE     = 2000         #feet above sea level. If you live at 1000ft then you'd want to make yours ~3000 etc. I use 2000 to weed out some of the smaller general aviation traffic. 

# =============================================================================
# MASTER / SLAVE
# =============================================================================

MASTER_TRACKER          = ""                  # Leave "" if this IS the master. IF this is the Slave than put the hostname of the Master in the ""
OTHER_TRACKER_HOSTNAMES = [""]      # If this is the master and you have a slave, put the slave hostname in the "" If there is no slave than leave as is

# =============================================================================
# FLIGHT DATA
# =============================================================================

# OpenSky � positions (free, get credentials at opensky-network.org)
OPENSKY_CLIENT_ID     = "xxx"
OPENSKY_CLIENT_SECRET = "xxxx"

# FlightAware AeroAPI � route fallback (get key at flightaware.com/aeroapi)
FLIGHTAWARE_API_KEYS      = "xxx", "xxx" # if more than one key format like ["key1", "key2"] https://www.flightaware.com/aeroapi/signup/personalflight radar 24 api signup

FLIGHTAWARE_MONTHLY_LIMIT = 5.00   # Stop calling FA when this $ limit is reached

# AirLabs � route fallback (get key at airlabs.co)
AIRLABS_API_KEYS = "xxx" #if you have multiple keys than format like ["key1", "key2"] https://airlabs.co/signup

# Flight Radar24 
FLIGHTRADAR24_KEY = "xxx" #https://fr24api.flightradar24.com/docs/getting-started

# =============================================================================
# NOTIFICATIONS & LOGGING
# =============================================================================

EMAIL       = "xxx@xxx.com"  #insert your email address between the " ie "example@example.com" to recieve emails when there is a new top 3 flight. Leave "" to recieve no emails. It will log/local webpage regardless
MAX_CLOSEST = 3 #the amount of furthest flights you want in your log
MAX_FARTHEST = 3 #3 #the amount of closest flights to your house you want in your log

# =============================================================================
# API KEYS � third party
# =============================================================================

TOMORROW_API_KEY = "xxx"   # tomorrow.io to get your API 
