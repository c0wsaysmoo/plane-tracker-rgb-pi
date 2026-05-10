# Deployment Notes

## Per-Device Setup

### 1. Place `.env` file
The `.env` file goes ONE DIRECTORY ABOVE `its-a-plane-python/`:
- If code is at `~/its-a-plane-python/`, `.env` goes at `~/.env`
- If code is at `/opt/plane-tracker/its-a-plane-python/`, `.env` goes at `/opt/plane-tracker/.env`

This is because `config.py` loads: `os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")`

### 2. Required directories
```bash
sudo mkdir -p /var/lib/plane-tracker/maps
sudo chmod 777 /var/lib/plane-tracker
```

### 3. Symlinks (logos + icons)
```bash
cd ~/its-a-plane-python
ln -sf ~/logos logos
ln -sf ~/icons icons
```

### 4. First-run: airports.json
Generated automatically on first run (downloads from GitHub). Delete to regenerate:
```bash
rm ~/its-a-plane-python/airports.json
```

### 5. Dependencies
```bash
sudo pip install fr24 httpx h2 python-dotenv flask folium Pillow requests protobuf grpcio --break-system-packages
```

### 6. Weather cache
Created automatically at `its-a-plane-python/.cache/temperature.json` and `.cache/forecast.json`.
Survives reboots. Delete `.cache/` to force fresh weather fetch.

## Running

### Direct (testing)
```bash
cd ~/its-a-plane-python
sudo PYTHONPATH=/home/USER/.local/lib/python3.13/site-packages python3 its-a-plane.py --led-rows=32 --led-cols=64 --led-slowdown-gpio=GPIO --led-row-addr-type=0
```

### With matrix-supervisor (ernie + nyc)
The supervisor handles MQTT nightlight switching. It spawns `its-a-plane.py` as a subprocess.
Service file: `/etc/systemd/system/matrix-supervisor.service`
```bash
sudo systemctl restart matrix-supervisor
```
The supervisor passes CLI args: `--led-rows=32 --led-cols=64 --led-slowdown-gpio=4 --led-row-addr-type=0`

**Important:** The new fork's `display/__init__.py` uses `drop_privileges=False` which means it stays as root after matrix init. The supervisor already runs via sudo so this is fine.

### Without supervisor (lauri, mari)
Service file: `/etc/systemd/system/flight-tracker.service`
```bash
sudo systemctl restart flight-tracker.service
```

## Device Notes

- `GPIO_SLOWDOWN`: 4 for Pi 3/4, 2 for Pi 3A+, 1 for Pi Zero
- `BONNET_TYPE`: "single" for standard Adafruit RGB Matrix Bonnet, "triple" for Triple Bonnet
- `SEARCH_RADIUS_NM`: 3-6nm depending on how busy your airspace is
- If using a supervisor (MQTT nightlight switching), the supervisor spawns `its-a-plane.py` as a subprocess
- Without a supervisor, run via systemd service directly

## .env Template
```
FR24_API_KEY=
TOMORROW_API_KEY=YOUR_KEY
ZONE_TL_LAT=
ZONE_TL_LON=
ZONE_BR_LAT=
ZONE_BR_LON=
HOME_LAT=
HOME_LON=
TEMPERATURE_LOCATION=LAT,LON
TEMPERATURE_UNITS=imperial
DISTANCE_UNITS=imperial
CLOCK_FORMAT=12hr
MIN_ALTITUDE=0
BRIGHTNESS=100
BRIGHTNESS_NIGHT=25
NIGHT_BRIGHTNESS=True
NIGHT_START=21:00
NIGHT_END=06:00
GPIO_SLOWDOWN=4
JOURNEY_CODE_SELECTED=HTO
JOURNEY_BLANK_FILLER= ?
HAT_PWM_ENABLED=False
FORECAST_DAYS=3
EMAIL=
MAX_FARTHEST=10
MAX_CLOSEST=10
SEARCH_RADIUS_NM=6
SPEED_UNITS=imperial
BONNET_TYPE=single
```

## Computing ZONE from SEARCH_RADIUS_NM
The zone bounding box should match the search radius. Calculate:
```python
import math
lat, lon = HOME_LAT, HOME_LON
radius_nm = SEARCH_RADIUS_NM
lat_delta = radius_nm / 60.0
lon_delta = radius_nm / (60.0 * math.cos(math.radians(lat)))
# ZONE_TL_LAT = lat + lat_delta (north)
# ZONE_TL_LON = lon - lon_delta (west)
# ZONE_BR_LAT = lat - lat_delta (south)
# ZONE_BR_LON = lon + lon_delta (east)
```

## Known Issues / Gotchas
1. `.env` path: MUST be `../` relative to `config.py`, not inside `its-a-plane-python/`
2. `airports.json`: his original code had lat/lon SWAPPED — we fixed this. If you see crazy distances, delete airports.json and let it regenerate.
3. Rate limiter: tracked flight search resets the 90s rate limiter to make a fresh wide-area call. This is intentional.
4. `drop_privileges=False` in display: required because fr24 thread needs network access after matrix init.
5. FR24_API_KEY=blank is fine — uses anonymous gRPC (4 fields, 1500 flights).
