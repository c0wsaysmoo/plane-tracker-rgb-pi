from datetime import datetime, timedelta
import requests as r
import pytz
import time

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

if TEMPERATURE_UNITS != "metric" and TEMPERATURE_UNITS != "imperial":
    TEMPERATURE_UNITS = "metric"

from config import TEMPERATURE_LOCATION

# Weather API
TOMORROW_API_URL = "https://api.tomorrow.io/v4/"

def grab_temperature_and_humidity(retries=3, delay=1):
    current_temp, humidity = None, None

    for attempt in range(retries):
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
            data = request.json()["data"]["values"]
            current_temp, humidity = data["temperature"], data["humidity"]
            break  # If successful, exit the loop and return the temperature and humidity
        except r.exceptions.RequestException as e:
            print(f"Attempt {attempt + 1} failed. Error: {e}")
            if attempt < retries - 1:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)

    return current_temp, humidity

def grab_forecast():
    # Get the current time
    current_time = datetime.utcnow()
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
                "weatherCodeDay"
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
    
# Example usage
temperature, humidity = grab_temperature_and_humidity(retries=3, delay=2)
if temperature is not None:
    print(f"Current temperature: {temperature} \u00b0C")  # Or \u00b0F based on TEMPERATURE_UNITS
else:
    print("Failed to retrieve temperature.")

if humidity is not None:
    print(f"Current humidity: {humidity}%")
else:
    print("Failed to retrieve humidity.")

forecast_data = grab_forecast()
if forecast_data is not None:
    print("Weather forecast:")
    for interval in forecast_data:
        temperature_min = interval["values"]["temperatureMin"]
        temperature_max = interval["values"]["temperatureMax"]
        weather_code_day = interval["values"]["weatherCodeDay"]
        print(f"Date: {interval['startTime'][:10]}, Min Temp: {temperature_min}, Max Temp: {temperature_max}, Weather Code: {weather_code_day}")
else:
    print("Failed to retrieve forecast.")
