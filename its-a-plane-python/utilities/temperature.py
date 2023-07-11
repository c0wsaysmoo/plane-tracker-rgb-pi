from datetime import datetime, timedelta
import requests as r
import pytz

# Attempt to load config data
try:
    from config import TOMORROW_API_KEY
    from config import TEMPERATURE_UNITS
    from config import FORECAST_DAYS

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    TOMORROW_API_KEY = None
    TEMPERATURE_UNITS = "metric"
    FORECAST_DAYS = "3"

if TEMPERATURE_UNITS == "metric":
    TEMPERATURE_MIN = 0
    TEMPERATURE_MAX = 40
elif TEMPERATURE_UNITS == "imperial":
    TEMPERATURE_MIN = 32
    TEMPERATURE_MAX = 100

if TEMPERATURE_UNITS != "metric" and TEMPERATURE_UNITS != "imperial":
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

# Weather API
TOMORROW_API_URL = "https://api.tomorrow.io/v4/"

def grab_temperature():
    current_temp = None
    request = r.get(
        f"{TOMORROW_API_URL}/weather/realtime",
        params={
            "location": TEMPERATURE_LOCATION,
            "units": TEMPERATURE_UNITS,
            "apikey": TOMORROW_API_KEY
        }
    )
    try:
        current_temp = request.json()["data"]["values"]["temperature"]
    except Exception as e:
        print(f"An error... {e}")
    return current_temp

def grab_forecast():
    # Get the current time
    current_time = datetime.now()
    # Add 6 hours to the current time
    dt = current_time + timedelta(hours=6)
    forecast = None
    resp = r.post(
        f"{TOMORROW_API_URL}/timelines",
        headers={
            "Accept-Encoding": "gzip",
            "accept": "application/json",
            "content-type": "application/json"
        },
        params={"apikey": TOMORROW_API_KEY}, 
        json={
            "location": TEMPERATURE_LOCATION,
            "units": TEMPERATURE_UNITS,
            "fields": [
                "temperatureMin",
                "temperatureMax",
                "weatherCode"
            ],
            "timesteps": [
                "1d"
            ],
            "startTime": dt.isoformat(),
            "endTime": (dt+timedelta(days=int(FORECAST_DAYS))).isoformat()
        }
    )    
    try:
        forecast = resp.json()["data"]["timelines"][0]["intervals"]
    except Exception as e:
        print(f"An error... {e}")
    return forecast
