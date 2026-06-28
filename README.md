## Overview

A passion project years in the making. The tracker runs as a system service on a Raspberry Pi driving a 64×32 RGB LED matrix and is configured entirely through a web UI. It shows the time, weather, moon phases, overhead flights, active alerts, and can track specific flights end-to-end. Multiple clocks in the same house sync intelligently so you're never burning duplicate API credits.

---

## Web Configuration & Clock Mirror

Open a browser on any device on your network and go to **http://hostname.local:8080** (use the Pi's hostname, not your username).

From this page you can:

- Fill out and update all settings — including WiFi network — without touching any raw files
- View **system stats** and current API usage per service at the bottom
- **Mirror the clock display live** in your browser, with a pop-out option that floats always-on-top and is freely resizable — useful for keeping an eye on the display from another room or monitor

> **Upgrading from an older version?** Make a copy of your existing config file before pulling, then use it to fill in the new config format.

---

## Clock Screen

- Displays time, date, current temperature, and forecast
- **Forecast modes:**
  - **3-day forecast** — the default view showing three days at a glance
  - **Daily forecast** — breaks the current day into Morning / Afternoon / Evening / Night periods
  - **Hybrid** — automatically shows the daily breakdown during certain hours of the day and switches to the 3-day view the rest of the time; configure the time windows in the config
- Temperature color is based on current humidity (white → blue gradient)
- Time color shifts at sunrise and sunset
- Date line shows moon phase with a purple-to-white gradient that tracks the lunar cycle
- Display dims at predefined times set in the config
- Toggle between 12 hr / 24 hr and imperial / metric

---

## Alerts

When enabled, the clock scales down and shows active alerts beneath it. All alerts sync to a secondary screen if you run one.

- **FAA Airport Alerts** — ground stops, departure delays, and more for major US airports
- **NWS Weather Alerts** — real-time warnings for your local area
- **ISS Pass-by Countdown** — live countdown when the ISS is minutes away from passing overhead (works anywhere)

---

## Flight Tracker Screen

Detects aircraft in your bounding box every 30 seconds via OpenSky and displays:

- **Origin → Destination** airport codes with distances to each
- Airport codes color-coded by delay status:

  | Departure delay | Color |
  |---|---|
  | 0–20 min | Green |
  | 20–40 min | Yellow |
  | 40–60 min | Orange |
  | 1–4 hrs | Red |
  | 4–8 hrs | Purple |
  | 8+ hrs | Blue |

  | Arrival | Color |
  |---|---|
  | On time / early | Green |
  | 0–30 min late | Yellow |
  | 30–60 min late | Orange |
  | 1–4 hrs late | Red |
  | 4–8 hrs late | Purple |
  | 8+ hrs late | Blue |

  Grey = actual time not yet available (common near airports)

- Arrow between codes acts as a progress bar: red at departure, green near arrival
- Airline IATA name, flight number, abbreviated aircraft type, and distance / direction / altitude to your location
- Airline ICAO logo — especially useful for regional carriers operating under multiple brands (e.g., Republic Airways flying as United Express, Delta Connection, or American Eagle)

---

## Tracked Flight

Track a specific flight from departure to landing via the web UI at **http://hostname.local:8080**.

**How to use:**
- Enter a flight number (must use the 3-letter ICAO airline code — e.g., **UAL1134** not UA1134)
- If the flight is active, tracking starts immediately
- If it hasn't departed yet, save it and tracking begins automatically once ADSB confirms wheels-up — no credits burned until then
- Regional flights may not be supported due to API constraints

**What the display shows:**
- Airline logo, name, and route in the header
- Dynamic progress bar with a moving arrow icon (turns red when live position data is unavailable, e.g. over the ocean)
- Bottom line: time and distance remaining, aircraft type, airspeed, and altitude with a climb/descent indicator
- When the flight lands, the display automatically returns to the weather forecast
- Overhead flight detection continues normally throughout

---

## The "Waterfall" API Stack

The system exhausts free tiers before touching paid ones, and rotates through multiple keys automatically:

| Service | Role | Cost |
|---|---|---|
| **ADSB.lol/OpenSky Network** | Primary position scout — required | Free |
| **AirLabs** |  1,000 credits/mo | Free |
| **FlightAware** |  1,000 lookups | Free 5$ worth of credits a month |
| **FlightRadar24** | 30,000 lookups | 9$/mo |

Stack as many keys as you want across providers. The system rotates automatically. You can order them however you wish based on what accounts you have. You also can have 1 or all 3 providers. 

**Credit efficiency features:**
- ADSB signal must confirm takeoff before any route API is called
- ETA calculated locally from current airspeed, position, and descent pattern — no repeated polling
- Most flights consume **one credit total** for the entire tracking duration

---

## Master / Slave Multi-Clock Sync

For households with multiple clocks:

- One Pi is designated **Master** — it handles all API lookups and weather fetching
- **Slave** units pull data from the Master over the local network
- Single weather API key covers all clocks
- The loading pulse in the top-right corner tells you status at a glance:
  - **White** — pinged OpenSky
  - **Yellow** — route found via AirLabs
  - **Green** — route found via FR24 (or Slave connected to Master)
  - **Cyan** — route found via FlightAware
  - **Red** — Slave cannot reach Master

---

## Flight Logs & Email Alerts

The tracker logs the closest and farthest flights it detects:

- **Top N closest flights** to your location (configurable via `MAX_CLOSEST`)
- **Top N farthest flights** by origin or destination (configurable via `MAX_FARTHEST`)

Automatic email alerts fire when a new record is set, sent from **flight.tracker.alerts2025@gmail.com** with flight details and a link to an interactive map (link valid 30 days; maps also always viewable on your local web page). Emails can be disabled while keeping the log files and local web stats.

---

## Statistics Dashboard

The web UI includes a stats dashboard showing real-time analytics: average daily flight counts, per-service API usage, and projected monthly consumption — so you can pick the API combination that matches your local sky traffic.

---

## System Service

Run the tracker as a dedicated system service rather than via crontab:

```bash
sudo systemctl enable its-a-plane
sudo systemctl start its-a-plane
```

---

## Credits

Built on [Colin Waddell's its-a-plane-python](https://github.com/ColinWaddell/its-a-plane-python). Additional forks and inspiration from [a10kiloham](https://github.com/a10kiloham/plane-tracker-rgb-pi), [yashmulgaonkar](https://github.com/yashmulgaonkar/plane-tracker-rgb-pi), and [ajplotkin](https://github.com/ajplotkin/plane-tracker-rgb-pi-f24only) — thanks especially to ajplotkin for the alerts feature.

This is a solo passion project built entirely in my spare time. Every feature you see has been researched, coded, tested, and debugged by myself. As the feature set grows, so does the complexity of keeping everything working together. I do my best to keep things stable, but please be patient and kind if something isn't perfect as it's getting very time consuming to test each feature every time I update. If you'd like to show your appreciation (especially if I help you troubleshoot), consider getting me a coffee! I've shared this project in good faith—please don't take advantage of it.
[paypal.me/c0wsaysmoo](https://paypal.me/c0wsaysmoo)

Please please please reread the instructions carefully if you have any issues. Most issues are by not following them properly. If you absolutly can't figure it out shoot me a message. I am also on reddit under [Mediocre-Opposite225](https://old.reddit.com/user/Mediocre-Opposite225/)
 

https://github.com/user-attachments/assets/854f535a-4aa3-4a97-8ee5-4d9e60f76eaf

![PXL_20241019_155956016](https://github.com/user-attachments/assets/91532d4f-3b6f-4a1b-9a26-43ffe5c6093d)
<img width="4080" height="3072" alt="PXL_20260626_212416823" src="https://github.com/user-attachments/assets/53ea72ea-7153-4545-b03e-5affb98da00f" />
![PXL_20241019_165254031](https://github.com/user-attachments/assets/2e70bfcd-70ae-4acc-ba69-dde07c56a068)
![PXL_20241019_165305826](https://github.com/user-attachments/assets/5188780d-84ff-4111-8bde-9584d6a70df2)
![PXL_20241019_155500974](https://github.com/user-attachments/assets/5c3540e9-b699-41c8-8aef-32fb7a7f7b5d)
Had to remount the Pi since the display ribbon bumped into the panel 
![PXL_20241019_155518437](https://github.com/user-attachments/assets/2d6f4beb-59f1-4771-80ce-8bafd00cd1fc)
![PXL_20241019_155605121](https://github.com/user-attachments/assets/4b71b758-00c9-4586-a5a0-ad251696eb17)
![PXL_20241019_155629794](https://github.com/user-attachments/assets/f82088b8-e959-44e3-82f3-7207779cc659)
![PXL_20241019_155732297](https://github.com/user-attachments/assets/77a329c7-d9c2-4a33-ab07-b6f6a2bf6ded)
![signal-2025-12-01-080516_002](https://github.com/user-attachments/assets/887de831-c33f-4646-a97f-bf88dfb396d9)

The difference in size between P4 and P2.5 panel. I use P4 for the living room and P2.5 for my desk.

<img width="422" height="322" alt="distance" src="https://github.com/user-attachments/assets/354cda11-9f3d-4b04-ad8e-68ddfc3ec3e5" />

The close.txt file. Farthest.txt looks the same.
<img width="1878" height="1019" alt="flight" src="https://github.com/user-attachments/assets/4466a735-1b4d-4e28-b22b-4f171c5a58fd" />


Map will show the top 3 (by default) farthest flights, and the top 3 closest ping'd flights to your location. Solid lines is the flown section and dashed is unflown. Uses actual flight path travelled (if available) then uses calculated Great-circle distance for the remainder. If no flight path travelled available then uses Great-circle distance for both. (If you want to reset your maps to take advantage of the newer flight path data, delete the farthest.txt file and reboot)

![email](https://github.com/user-attachments/assets/491c5725-9c3d-413e-bee3-54d88ab9d696)

The email

<img width="1429" height="2204" alt="Screenshot 2026-06-26 163846" src="https://github.com/user-attachments/assets/76366f5c-b23c-48f9-9435-a2b594475180" />
<img width="1421" height="2384" alt="Screenshot 2026-06-26 163811" src="https://github.com/user-attachments/assets/d3b50f84-aad3-48f2-a8c7-55d8dfab6eed" />
<img width="1426" height="623" alt="Screenshot 2026-06-26 163740" src="https://github.com/user-attachments/assets/99a45a8b-41a2-489e-8717-82675771a243" />
<img width="1797" height="742" alt="Screenshot 2026-06-26 163642" src="https://github.com/user-attachments/assets/9d6f1c3c-ff1c-4d90-b34d-7d54800bfc40" />



The local webpage to track flights or to look at your maps/logs/stats. You can look at the overall stats or click on the dates at the bottom to look at stats for individual days



https://github.com/user-attachments/assets/1944d063-83e5-4118-aad3-f6a9678fa22f



How the display looks while it is tracking a flight

<img width="4080" height="3072" alt="PXL_20260610_194346394" src="https://github.com/user-attachments/assets/d20eb124-a8ba-4fb0-9311-bf2d497b4fb8" />
<img width="4080" height="3072" alt="PXL_20260610_184341409" src="https://github.com/user-attachments/assets/e2e34031-fb3a-4c24-bfb3-678e41ddaff4" />

Showing a weather alert and an airport alert. If there are multiple alerts it cycles between them every 4 seconds.


---

## Hardware Overview:

This is what I used to make mine. Other than the Pi and the Bonnet you can use whatever you want. You will need a computer with a SD card reader to setup the Pi and to do the install. You won't need it after it is setup.
- [Raspberry Pi 3A+](https://www.adafruit.com/product/4027) You can use the Pi 3B+/Pi 4 as well. (If you use a Pi 4 you'll need to adjust the "GPIO_SLOWDOWN" in the config file since it's more powerful than the Pi 3). It's just more expensive and you don't need the ethernet jack. You can also get them at [Microcenter](https://www.microcenter.com/product/514076/raspberry-pi-3-model-a-board). I tried with a Pi Zero, but couldn't get rid of the flicking completely even with soldering. I have not tried with a Pi 5, it requires different instructions with the Bonnet. If someone gets it running on the Pi 5 please let me know and I'll update the instructions. 
- [Adafruit bonnet](https://www.adafruit.com/product/3211)
- [64x32 RGB P4 panel](https://www.adafruit.com/product/2278) (I used a P4 panel measuring approximately 10 inches by 5 inches. If you prefer a smaller screen, you can opt for P3 or P2.5 panels etc, as long as they are 64x32 in size. These are available on Amazon and other websites. If the colors appear inverted, adjust the display file by changing 'RGB' to 'RBG.')
- [Tinted acrylic](https://www.adafruit.com/product/4749) makes the screen so much easier to read and looks nicer 10/10 recommend. Keep in mind that the acrylic panel is slightly larger than the P4 screen when you make the case.
- [double sided tape](https://www.amazon.com/EZlifego-Multipurpose-Removable-Transparent-Household/dp/B07VNSXY31) (I use it to attach the acrylic to the panel)
- MicroSD card (any size)
- [5V 4A power supply](https://www.amazon.com/Facmogu-Switching-Transformer-Compatible-5-5x2-1mm/dp/B087LY41PV) (powers both the Pi and the bonnet)
- [CPU heatsink](https://www.adafruit.com/product/3084) (this is the smaller heatsink)
- [2x20 pin extender](https://www.microcenter.com/product/480891/schmartboard-inc-schmartboard-inc-short-2x20-female-stackable-headers-qty-4) to prevent the bonnet from resting on it (the smaller heatsink you may not need the extender, but a normal size heatsink you will)
- [Optional power button](https://www.microcenter.com/product/420422/mcm-electronics-push-button-switch-spst-red) (though not really necessary)
- Soldering iron only required for PWM bridge or power button. The Pi 3 seems to be ok without the PWM bridge, but anything less and you'll want to.
- The case I built using a strip of 2in x 1/4in wood that I clampted and glued togother.
- However my friend [made this case](https://makerworld.com/en/models/819892#profileId-762764) that you can 3D print. 
- M2.5 machine screws to screw the bonnet onto the Pi and to screw the Pi onto the case from Ace Hardware.

---

## Getting Your API Keys

Before starting the setup, sign up for the following APIs. The free tiers are sufficient to get started.

| Service | Purpose | Sign Up |
|---|---|---|
| **Tomorrow.io** | Weather data | [app.tomorrow.io/signup](https://app.tomorrow.io/signup) |
| **OpenSky Network** | Primary flight detection (required) | [opensky-network.org](https://opensky-network.org/) |
| **AirLabs** | Route & status data (1,000 free credits/mo) | [airlabs.co/signup](https://airlabs.co/signup) |
| **FlightAware AeroAPI** | Route lookup fallback ($5 free credit/mo which is about 1,000 calls) | [flightaware.com/aeroapi/signup/personal](https://www.flightaware.com/aeroapi/signup/personal) |
| **Flight Radar 24** | Route lookup fallback ($9 subscription a month but 30,000 calls.) | [fr24api.flightradar24.com/docs/getting-started](https://fr24api.flightradar24.com/docs/getting-started). |

Have these keys handy — you'll enter them in the config file during Step 13.

Each provider offers free credits that the system will automatically rotate through before moving to the next tier. You can stack multiple keys from the same provider to extend your capacity.

- **Single key for both AirLabs + FlightAware** → ~66 flights/day
- **Two keys for both AirLabs + FlightAware** → ~130 flights/day, and so on

FlightRadar24 is a paid tier at $9/mo for 30,000 calls — more than enough for several users sharing a single key, especially when combined with AirLabs and FlightAware free credits.

The config page (`hostname.local:8080/config`) keeps a running tally of your API usage for each service, and the stats page shows your daily flight average so you can estimate how many credits you'll need per month. If you run out of credits the tracker will still work, but route information won't be displayed.

---

# Plane Tracker RGB Pi Setup Guide

Once you get your Raspberry Pi up and running, you can follow [this guide](https://linuxconfig.org/enabling-ssh-on-raspberry-pi-a-comprehensive-guide) to set up the project. 


### 1. Install Raspberry Pi OS Lite
Using the official Raspberry Pi Imager, go to `Other` and select **Raspberry Pi OS 64-bit Lite** (the Pi Zero only supports 32-bit Lite). **Note:** These instructions are for **Bookworm** and **Trixie**.

When using the Imager make sure these settings are selected to enable SSH and make sure your WiFi information is typed in EXACTLY or it won't connect when turned on.

![edit](https://github.com/user-attachments/assets/3141a507-6746-4741-84ba-2c5a6f319004)
![wifi](https://github.com/user-attachments/assets/0669de7a-cb9c-4c2a-9129-8b044c088f9f)

Make sure you select the correct timezone since that is what is displayed on the clock. You can always change it later.
![ssh](https://github.com/user-attachments/assets/67d6fa8f-5ae3-4bf9-9f47-fbf78017ad78)

### 2. Connect via SSH
I use **[MobaXterm](https://mobaxterm.mobatek.net/)** on Windows to SSH into the Pi since it allows you to see the folder structure and edit files directly without using the command prompt. After [SSH-ing into the Pi](https://www.fromdev.com/2025/04/how-to-ssh-into-raspberry-pi-a-step-by-step-guide.html), proceed with the following steps.

### 3. Install prerequisites and build the RGB Matrix library

```bash
sudo apt-get update
sudo apt-get install -y git python3-dev python3-pip python3-pillow cython3 python3-setuptools build-essential
```

Clone and build hzeller's library:

```bash
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix
make
```
### 4. Increase swap size (Pi 3 only)

The wheel compilation requires more memory than the Pi 3's 1GB provides. Without this it will crash mid-install.

```bash
sudo apt-get install -y dphys-swapfile
sudo dphys-swapfile swapoff
sudo nano /etc/dphys-swapfile
```

Add this line:
CONF_SWAPSIZE=512

Save with `Ctrl+O`, Enter, `Ctrl+X`, then:

```bash
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

Verify with `free -h` — you should see ~512MB (or more) under Swap. You can disable swap after installation is complete to reduce SD card wear:

```bash
sudo dphys-swapfile swapoff
sudo systemctl disable dphys-swapfile
```

### 5. Install the Adafruit RGB Matrix Bonnet

Install the Python installer dependency and run the Adafruit setup script:

```bash
sudo pip3 install adafruit-python-shell --break-system-packages
wget https://github.com/adafruit/Raspberry-Pi-Installer-Scripts/raw/main/rgb-matrix.py
python3 -m venv --system-site-packages env
source env/bin/activate
sudo -E env PATH=$PATH python3 rgb-matrix.py
```

You can solder a bridge between GPIO 4 and 18 to enable PWM for less screen flicker and smoother scrolling. It is optional — it will work without the bridge.

**During the script:**
- Interface board type: **Bonnet** (Option 1)
- **Quality** if you soldered the jumper, **Convenience** if not
  
### 6. Test the panel

**Test to make sure the panel works before doing anything else.** You're looking for a "HELLO WORLD" yellow happy face, with HELLO in green and WORLD in red. If it's only partially displaying or showing colors in the wrong place, reattach the bonnet to the Pi. Do not continue unless the test runs perfectly.

First build the C examples:
```bash
cd ~/rpi-rgb-led-matrix
make
```

Then run the test:
```bash
cd examples-api-use
```

If you did **not** solder:
```bash
sudo ./demo -D 1 runtext.ppm --led-rows=32 --led-cols=64 --led-limit-refresh=60 --led-slowdown-gpio=2 --led-gpio-mapping=adafruit-hat
```

If you **did** solder:
```bash
sudo ./demo -D 1 runtext.ppm --led-rows=32 --led-cols=64 --led-limit-refresh=60 --led-slowdown-gpio=2 --led-gpio-mapping=adafruit-hat-pwm
```

### 7. Build and install Python bindings

```bash
cd ~/rpi-rgb-led-matrix/bindings/python
make
sudo pip3 install . --break-system-packages
```

### 8. Git the tracker

Clone the tracker:
```
cd ~
git clone https://github.com/c0wsaysmoo/plane-tracker-rgb-pi
```
If the bridge on the bonnet is soldered, you'll need to set HAT_PWM_ENABLED=True in the config file. It's False by default

After cloning the files, move everything to the main folder, as some files need to be in /home/path/ rather than /home/path/plane-tracker-rgb-pi/ You'll need to combine the two logos folders since Github only allows 1,000 files per folder so I had to split them.
```
mv ~/plane-tracker-rgb-pi/* ~/
mkdir -p ~/logos
mv ~/logo/* ~/logos/
mv ~/logo2/* ~/logos/
rmdir ~/logo ~/logo2
```

# 9. Install Python dependencies

```
pip install pytz requests beautifulsoup4 folium selenium pillow flask --break-system-packages
```
If **Bookworm**
```
sudo setcap 'cap_sys_nice=eip' /usr/bin/python3.11
```

If **Trixie**

```
sudo setcap 'cap_sys_nice=eip' /usr/bin/python3.13
```

# 10. Make the Script Executable

```
chmod +x ~/its-a-plane-python/its-a-plane.py
```

# 11. Run the Script
Test the script manually by running

```
~/its-a-plane-python/its-a-plane.py
```
# 12. Find your project path

Open a terminal on your Pi and run:

```bash
cd ~/its-a-plane-python
pwd
```

Copy the path it shows — you'll need it in the next step. It will look something like `/home/pi/its-a-plane-python` or `/home/flight/its-a-plane-python`.

---

# 13. Create the service file

Run these commands **from inside your project folder** (after the `cd` above):

```bash
cat > /tmp/its-a-plane.service << EOF
[Unit]
Description=Plane Tracker
After=network.target

[Service]
User=$(whoami)
WorkingDirectory=$HOME
ExecStart=$(pwd)/its-a-plane.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

> ⚠️ **Important:** Run the `cd` command first or the paths will be wrong!

---

# 14. Install and start the service

```bash
sudo cp /tmp/its-a-plane.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable its-a-plane
sudo systemctl start its-a-plane
```

Check it's running:

```bash
sudo systemctl status its-a-plane
```

You should see `Active: active (running)` in green. If it shows an error, jump to the Troubleshooting section below.

---


# 15. Fill in the Config file.

You can only do so **IF** the clock is running. So start it and then in a broswer connected to the network go to http://hostname.local:8080 and click on "Configuration" After you fill in the config file save and reboot. Remember that "hostname" is the name of your PI (not your username)

# 16. Enable the web UI restart button

If you want the **Restart App** button in the web config page to work, you need to allow your user to restart the service without a password:

```bash
sudo visudo
```

This opens a text editor. Scroll to the very bottom and add this line (replace `pi` with your actual username — same as what `whoami` showed you):

```
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart its-a-plane
# nmcli (NetworkManager / Raspberry Pi OS Bookworm+)
pi ALL=(ALL) NOPASSWD: /usr/bin/nmcli

```

Save and exit (ctrl x, y, enter). Now the web UI restart button will work.

---

## Useful commands

| What you want to do | Command |
|---|---|
| Check if it's running | `sudo systemctl status its-a-plane` |
| Restart it | `sudo systemctl restart its-a-plane` |
| Stop it | `sudo systemctl stop its-a-plane` |
| Start it | `sudo systemctl start its-a-plane` |
| Watch live logs | `sudo journalctl -u its-a-plane -f` |
| See last 50 log lines | `sudo journalctl -u its-a-plane -n 50` |
| See logs since last crash | `sudo journalctl -u its-a-plane -b` |

---
Optional: Add a Power Button
If you'd like to add a power button, you can solder the button to the **GND/SCL** pins on the bonnet. Then, run the following commands:
```
git clone https://github.com/Howchoo/pi-power-button.git
./pi-power-button/script/install
```
