import urllib.request
import json

# Attempt to load config data
try:
    from config import WHEATHER_API_API_KEY
    from config import TEMPERATURE_UNITS
    from config import FORECAST_DAYS

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    WHEATHER_API_API_KEY = None
    TEMPERATURE_UNITS = "metric"
    FORECAST_DAYS = "3"

if TEMPERATURE_UNITS == "metric":
    TEMPERATURE_MIN = 0
    TEMPERATURE_MAX = 25
elif TEMPERATURE_UNITS == "imperial":
    TEMPERATURE_MIN = 32
    TEMPERATURE_MAX = 90

if TEMPERATURE_UNITS != "metric" and TEMPERATURE_UNITS != "imperial":
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

# Weather API
WEATHER_API_URL = "http://api.weatherapi.com/v1"



def grab_temperature():
    current_temp = None

    try:
        request = urllib.request.Request(
            WEATHER_API_URL
            + "/current.json"
            + "?q="
            + TEMPERATURE_LOCATION
            + "&key="
            + WHEATHER_API_API_KEY
        )
        raw_data = urllib.request.urlopen(request).read()
        current_temp = json.loads(raw_data.decode(TIMEZONE))["current"]["temp_f"]

    except:
        pass

    return current_temp

def grab_forecast():
    forecast = None

    try:
        request = urllib.request.Request(
            WEATHER_API_URL
            + "/forecast.json"
            + "?q="
            + TEMPERATURE_LOCATION
            + "&days="
            + FORECAST_DAYS
            + "&key="
            + WHEATHER_API_API_KEY
        )
        raw_data = urllib.request.urlopen(request).read()
        forecast = json.loads(raw_data.decode(TIMEZONE))["forecast"]

    except:
        pass

    return forecast
