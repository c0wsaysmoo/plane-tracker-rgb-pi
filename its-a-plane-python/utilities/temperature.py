from datetime import datetime, timedelta
import requests as r
import pytz
import time
import json 
import logging

# Attempt to load config data
try:
    from config import TOMORROW_API_KEY
    from config import TEMPERATURE_UNITS
    from config import FORECAST_DAYS

except (ModuleNotFoundError, NameError, ImportError):
    # If there's no config data
    TOMORROW_API_KEY = None
    TEMPERATURE_UNITS = "metric"
    FORECAST_DAYS = 3

if TEMPERATURE_UNITS != "metric" and TEMPERATURE_UNITS != "imperial":
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

# Weather API
TOMORROW_API_URL = "https://api.tomorrow.io/v4"

def grab_temperature_and_humidity():
    try:
        request = r.get(
            f"{TOMORROW_API_URL}/weather/realtime",
            params={
                "location": TEMPERATURE_LOCATION,
                "units": TEMPERATURE_UNITS,
                "apikey": TOMORROW_API_KEY
            },
            timeout=10
        )

        if request.status_code == 429:
            logging.error("Rate limit reached, returning error state")
            return None, None

        request.raise_for_status()

        data = request.json().get("data", {}).get("values", {})
        temperature = data.get("temperature")
        humidity = data.get("humidity")

        if temperature is None or humidity is None:
            logging.error("Incomplete data from API")
            return None, None

        #print(f"[Temp] {datetime.now()}: {temperature}{TEMPERATURE_UNITS}, {humidity}% RH")
        return temperature, humidity

    except (r.exceptions.RequestException, ValueError) as e:
        logging.error(f"Temperature request failed: {e}")
        return None, None
        
        
def grab_forecast(tag="unknown"):
    current_time = datetime.utcnow()
    dt = current_time + timedelta(hours=6)

    try:
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
                    "weatherCodeFullDay",
                    "sunriseTime",
                    "sunsetTime",
                    "moonPhase"
                ],
                "timesteps": ["1d"],
                "startTime": dt.isoformat(),
                "endTime": (dt + timedelta(days=int(FORECAST_DAYS))).isoformat()
            },
            timeout=10
        )

        resp.raise_for_status()

        data = resp.json().get("data", {})
        timelines = data.get("timelines", [])
        if not timelines:
            logging.error(f"[Forecast:{tag}] No timelines returned from API")
            return []

        intervals = timelines[0].get("intervals", [])
        if not intervals:
            logging.error(f"[Forecast:{tag}] Timelines returned but no intervals")
            return []

        #print(f"[Forecast:{tag}] {datetime.now()}: Retrieved {len(intervals)} days")
        return intervals

    except r.exceptions.RequestException as e:
        logging.error(f"[Forecast:{tag}] API request failed: {e}")
        return []
    except KeyError as e:
        logging.error(f"[Forecast:{tag}] Unexpected data format: {e}")
        return []
