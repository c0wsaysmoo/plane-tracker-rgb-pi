import urllib.request
import json

# Attempt to load config data
try:
    from config import OPENWEATHER_API_KEY

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    OPENWEATHER_API_KEY = None

try:
    from config import TEMPERATURE_UNITS

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    TEMPERATURE_UNITS = "metric"


if TEMPERATURE_UNITS == "metric":
    TEMPERATURE_MIN = 0
    TEMPERATURE_MAX = 25
elif TEMPERATURE_UNITS == "imperial":
    TEMPERATURE_MIN = 32
    TEMPERATURE_MAX = 77

if TEMPERATURE_UNITS != "metric" and TEMPERATURE_UNITS != "imperial":
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

# Weather API
WEATHER_API_URL = "https://taps-aff.co.uk/api/"
OPENWEATHER_API_URL = "https://api.openweathermap.org/data/2.5/"



def grab_temperature():
    if OPENWEATHER_API_KEY:
        return grab_temp_openweather()
    else:
        return grab_temp_taps()



def grab_temp_taps():
    current_temp = None

    try:
        request = urllib.request.Request(WEATHER_API_URL + TEMPERATURE_LOCATION)
        raw_data = urllib.request.urlopen(request).read()
        content = json.loads(raw_data.decode("utf-8"))
        current_temp = content["temp_c"]

    except:
        pass

    if TEMPERATURE_UNITS == "imperial":
        current_temp = (current_temp * (9.0 / 5.0)) + 32

    return current_temp


def grab_temp_openweather():
    current_temp = None

    try:
        request = urllib.request.Request(
            OPENWEATHER_API_URL
            + "weather?q="
            + TEMPERATURE_LOCATION
            + "&appid="
            + OPENWEATHER_API_KEY
            + "&units="
            + TEMPERATURE_UNITS
        )
        raw_data = urllib.request.urlopen(request).read()
        content = json.loads(raw_data.decode("utf-8"))
        current_temp = content["main"]["temp"]

    except:
        pass

    return current_temp


def grab_forecast():
    content = None

    try:
        request = urllib.request.Request(
            OPENWEATHER_API_URL
            + "forecast?q="
            + TEMPERATURE_LOCATION
            + "&appid="
            + OPENWEATHER_API_KEY
            + "&units="
            + TEMPERATURE_UNITS
        )
        raw_data = urllib.request.urlopen(request).read()
        content = json.loads(raw_data.decode("utf-8"))

    except:
        pass

    return content