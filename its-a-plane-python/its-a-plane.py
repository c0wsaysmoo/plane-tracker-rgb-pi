#!/usr/bin/python3
import subprocess
import os
import json

def _ensure_config():
    """
    Create default config files on first run if they don't exist.
    This lets the web UI load even before the user has configured anything.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_dir  = os.path.join(base_dir, "config")
    os.makedirs(cfg_dir, exist_ok=True)

    cfg_path = os.path.join(cfg_dir, "config.json")
    sec_path = os.path.join(cfg_dir, "secrets.json")

    default_cfg = {
        "location": {
            "zone_home": {
                "tl_y": 41.953371,
                "tl_x": -87.746000,
                "br_y": 41.889507,
                "br_x": -87.623695
            },
            "location_home": [41.926550, -87.695440],
            "temperature_location": "41.926550,-87.695440",
            "temperature_units": "imperial",
            "distance_units": "imperial",
            "speed_units": "imperial",
            "clock_format": "12hr",
            "journey_code": "ORD",
            "journey_blank_filler": " ? "
        },
        "display": {
            "brightness": 100,
            "brightness_night": 50,
            "night_brightness": False,
            "night_start": "22:00",
            "night_end": "06:00",
            "gpio_slowdown": 2,
            "hat_pwm_enabled": True,
            "forecast_days": 3
        },
        "flights": {
            "min_altitude": 2600,
            "max_farthest": 3,
            "max_closest": 3,
            "email": ""
        },
        "master_slave": {
            "master_tracker": "",
            "other_tracker_hostnames": []
        },
        "route_cache": {
            "enabled": True,
            "days": 30
        },
        "api_sources": {
            "order": ["FlightStats", "AirLabs", "FlightAware", "FR24"],
            "enabled": {
                "FlightStats": True,
                "AirLabs": True,
                "FlightAware": True,
                "FR24": False
            }
        },
        "sports": {
            "enabled": True,
            "priority": ["mlb", "nfl", "nba", "nhl", "mls",
                         "college_football", "college_basketball", "epl"],
            "teams": {},
            "cycle_seconds": 10
        }
    }

    default_sec = {
        "tomorrow_api_key": "",
        "opensky_client_id": "",
        "opensky_client_secret": "",
        "airlabs_api_keys": [],
        "flightaware_api_keys": [],
        "flightaware_monthly_limit": 4.00,
        "flightradar24_key": "",
        "moon_key": ""
    }

    if not os.path.exists(cfg_path):
        with open(cfg_path, "w") as f:
            json.dump(default_cfg, f, indent=2)
        print("[Setup] Created default config/config.json — edit via web UI at :8080/config")

    if not os.path.exists(sec_path):
        with open(sec_path, "w") as f:
            json.dump(default_sec, f, indent=2)
        print("[Setup] Created default config/secrets.json — add your API keys via web UI at :8080/config")


if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Ensure config files exist before anything else loads
    _ensure_config()

    # Build path to web/app.py
    app_path = os.path.join(base_dir, "web", "app.py")

    # Start Flask server in background
    subprocess.Popen(["python3", app_path])

    # Start display loop
    from display import Display
    run_text = Display()
    run_text.run()
