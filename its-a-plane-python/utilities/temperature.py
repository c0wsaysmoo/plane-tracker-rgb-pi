from datetime import datetime, timedelta
import requests as r
import pytz
import time
import json 

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
TOMORROW_API_URL = "https://api.tomorrow.io/v4/"

def grab_temperature_and_humidity(delay=2):
    current_temp, humidity = None, None

    while True:
        try:
            request = r.get(
                f"{TOMORROW_API_URL}/weather/realtime",
                params={
                    "location": TEMPERATURE_LOCATION,
                    "units": TEMPERATURE_UNITS,
                    "apikey": TOMORROW_API_KEY
                }
            )
            request.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
            
            # Print the raw JSON response for debugging
            #print("Raw JSON Response:")
            #print(json.dumps(request.json(), indent=4))
            
            # Safely extract data
            data = request.json().get("data", {}).get("values", {})
            current_temp = data.get("temperature")
            humidity = data.get("humidity")

            # Retry if temperature or humidity is missing
            if current_temp is None or humidity is None:
                print("Temperature or humidity data not available in response.")
                raise KeyError("Missing temperature or humidity in API response.")
            
            # If the data is valid, exit the loop
            break

        except (r.exceptions.RequestException, KeyError) as e:
            print(f"Request failed. Error: {e}")
            print(f"Retrying in {delay} seconds...")
            time.sleep(delay)

    return current_temp, humidity

def grab_forecast(delay=2):
    while True:
        try:
            current_time = datetime.utcnow()
            dt = current_time + timedelta(hours=6)
            
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
                    "timesteps": [
                        "1d"
                    ],
                    "startTime": dt.isoformat(),
                    "endTime": (dt + timedelta(days=int(FORECAST_DAYS))).isoformat()
                }
            )    
            resp.raise_for_status()  # Raise an exception for 4xx or 5xx status codes

            # Safely access the JSON response to avoid KeyError
            data = resp.json().get("data", {})
            timelines = data.get("timelines", [])

            if not timelines:
                raise KeyError("Timelines not found in response.")

            forecast = timelines[0].get("intervals", [])

            if not forecast:
                raise KeyError("Forecast intervals not found in timelines.")

            return forecast

        except (r.exceptions.RequestException, KeyError) as e:
            logging.error(f"Request failed. Error: {e}")
            logging.info(f"Retrying in {delay} seconds...")
            time.sleep(delay)
    
    return None
    
#forecast_data = grab_forecast()
#if forecast_data is not None:
#    print("Weather forecast:")
#    for interval in forecast_data:
#        temperature_min = interval["values"]["temperatureMin"]
#        temperature_max = interval["values"]["temperatureMax"]
#        weather_code_day = interval["values"]["weatherCodeFullDay"]
#        sunrise = interval["values"]["sunriseTime"]
#        sunset = interval["values"]["sunsetTime"]
#        moon_phase = interval["values"]["moonPhase"]
#        print(f"Date: {interval['startTime'][:10]}, Min Temp: {temperature_min}, Max Temp: {temperature_max}, Weather Code: {weather_code_day}, Sunrise: {sunrise}, Sunset: {sunset}, Moon Phase: {moon_phase}")
#else:
#    print("Failed to retrieve forecast.")
