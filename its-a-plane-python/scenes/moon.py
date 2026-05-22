from datetime import datetime
import requests as r
from config import MOON_KEY, LOCATION_HOME

def grab_moon_data():
    print(f"[{datetime.now()}] grab_moon_data() called")

    # Force failure for testing
    #return None, None, None, None, None

    def to_datetime(time_str):
        try:
            today = datetime.now().date()
            return datetime.strptime(f"{today} {time_str}", "%Y-%m-%d %H:%M")
        except Exception:
            return None

    try:
        lat, lon = LOCATION_HOME
        url = f"https://api.ipgeolocation.io/v2/astronomy?apiKey={MOON_KEY}&lat={lat}&long={lon}"
        response = r.get(url, timeout=10)
        response.raise_for_status()

        data = response.json().get("astronomy", {})

        sunrise_str = data.get("sunrise", "")
        sunset_str = data.get("sunset", "")
        moonrise_str = data.get("moonrise", "")
        moonset_str = data.get("moonset", "")
        illumination = data.get("moon_illumination_percentage", "NA")

        sunrise = to_datetime(sunrise_str)
        sunset = to_datetime(sunset_str)
        moonrise = to_datetime(moonrise_str)
        moonset = to_datetime(moonset_str)
        
        #print(f"[{datetime.now()}] Parsed values:")
        #print(f"  Sunrise: {sunrise}")
        #print(f"  Sunset: {sunset}")
        #print(f"  Moonrise: {moonrise}")
        #print(f"  Moonset: {moonset}")
        #print(f"  Illumination: {illumination}")

        return sunrise, sunset, moonrise, moonset, illumination

    except r.exceptions.RequestException as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Astronomy data request failed: {e}")
        return None, None, None, None, None