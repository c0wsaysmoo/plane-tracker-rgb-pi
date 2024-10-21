Updated the Temperature.py file in the Utilities folder, so replace and reboot. If the Current Temperature or Current Humidity fields are missing then it defaults to 0. Seems like the issue on their end has been fixed as well. 

# Project Overview

This project is based on [Colin Waddell's work](https://github.com/ColinWaddell/its-a-plane-python), with some additional features I’ve added.

## Clock Screen:
- Displays time, date, current temperature, and a 3-day forecast.
- The current temperature color is based on the current humidity level on a gradient of white-blue.
- Time changes color at sunrise and sunset.
- The date shows moon phases with a purple-to-white gradient. It gradually becomes white on the right until the full moon, then fades white on the left as the moon wanes.
- The display dims at predefined times, set in the config file.
- You can switch between 12hr/24hr time and choose imperial or metric units.

## Flight Tracker Screen:
- Displays the origin and destination airport codes, with distances to both airports.
- Airport codes are color-coded based on the difference between the scheduled and actual departure times, as well as the scheduled and estimated arrival times.

  **Departure:**
  - 0-20 mins: Green
  - 20-40 mins: Yellow
  - 40-60 mins: Orange
  - 1-4 hrs: Red
  - 4-8 hrs: Purple
  - 8+ hrs: Blue
  
  **Arrival:**
  - On-time or early: Green
  - 0-30 mins late: Yellow
  - 30-60 mins late: Orange
  - 1-4 hrs late: Red
  - 4-8 hrs late: Purple
  - 8+ hrs late: Blue

- An arrow between the airport codes acts as a progress bar for the flight, starting red (just left) and turning green (almost complete).
- Below, the airline’s IATA name, flight number, abbreviated aircraft type, and the distance/direction to your location are displayed.
- The airline's ICAO code is shown in the logo, indicating which airline is operating the flight. This is especially useful for regional carriers, where an airline might operate flights for multiple brands (e.g., Republic Airways flying for American Eagle, Delta Connection, and United Express).

I've spent a LOT of time messing with this and adding things and trying to make it as easy to set up as possible. If you'd like to get me a coffee, I'd appreciate it!
[paypal.me/c0wsaysmoo](https://paypal.me/c0wsaysmoo)

![tracker](https://github.com/user-attachments/assets/802a6c43-31d2-48dc-816b-4eb0ca0367e1)
![PXL_20241019_155956016](https://github.com/user-attachments/assets/91532d4f-3b6f-4a1b-9a26-43ffe5c6093d)
![PXL_20241019_165254031](https://github.com/user-attachments/assets/2e70bfcd-70ae-4acc-ba69-dde07c56a068)
![PXL_20241019_165305826](https://github.com/user-attachments/assets/5188780d-84ff-4111-8bde-9584d6a70df2)
![PXL_20241019_155500974](https://github.com/user-attachments/assets/5c3540e9-b699-41c8-8aef-32fb7a7f7b5d)
Had to remount the Pi since the display ribbon bumped into the panel 
![PXL_20241019_155518437](https://github.com/user-attachments/assets/2d6f4beb-59f1-4771-80ce-8bafd00cd1fc)
![PXL_20241019_155605121](https://github.com/user-attachments/assets/4b71b758-00c9-4586-a5a0-ad251696eb17)
![PXL_20241019_155629794](https://github.com/user-attachments/assets/f82088b8-e959-44e3-82f3-7207779cc659)
![PXL_20241019_155732297](https://github.com/user-attachments/assets/77a329c7-d9c2-4a33-ab07-b6f6a2bf6ded)



---

## Hardware Overview:

This is what I used to make mine. Other than the Pi and the Bonnet you can use whatever you want. 
- Raspberry Pi 3A+ (Pi Zero had flickering, and Pi 5 isn’t compatible)
- [Adafruit bonnet](https://www.adafruit.com/product/3211)
- [64x32 RGB P4 panel](https://www.adafruit.com/product/2278) (I used a P4 panal measures roughly 10in x 5in, they make smaller screens P3/P2.5 etc if you want a smaller version. Can buy them from Amazon as well. If the colors look inverted you'll need to go to the display file and change 
"RGB" to "RBG")
- [An acrylic difusser](https://www.adafruit.com/product/4749) (makes it easier to read, this one is slightly larger than the P4 panel so keep that in mind when making the case)
- [double sided tape](https://www.amazon.com/EZlifego-Multipurpose-Removable-Transparent-Household/dp/B07VNSXY31) 
- MicroSD card (any size)
- [5V 4A power supply](https://www.amazon.com/Facmogu-Switching-Transformer-Compatible-5-5x2-1mm/dp/B087LY41PV) (powers both the Pi and the bonnet)
- [CPU heatsink](https://www.adafruit.com/product/3083)
- [2x20 pin extender](https://www.microcenter.com/product/480891/schmartboard-inc-schmartboard-inc-short-2x20-female-stackable-headers-qty-4) to prevent the bonnet from resting on it
- [Optional power button](https://www.microcenter.com/product/420422/mcm-electronics-push-button-switch-spst-red) (though not really necessary)
- Soldering iron only required for PWM bridge or power button (I've always soldered the bridge)
- The case I built using a strip of 2in x 1/4in wood that I clampted and glued togother.
- M2.5 machine screws to screw the bonnet onto the Pi and to screw the Pi onto the case from Ace Hardware.

---

# Plane Tracker RGB Pi Setup Guide

Once you get your Raspberry Pi up and running, you can follow [this guide](https://linuxconfig.org/enabling-ssh-on-raspberry-pi-a-comprehensive-guide) to set up the project.

### 1. Install Raspberry Pi OS Lite
Using the official Raspberry Pi Imager, go to `Other` and select **Raspberry Pi 64 OS Lite**. **Note** whether the version is **Bookworm** or **Bullseye** — this will matter later.

### 2. Connect via SSH
I use **MobaXterm** on Windows to SSH into the Pi. After SSH-ing into the Pi, proceed with the following steps.

### 3. Install the Adafruit Bonnet
[Install the bonnet](https://learn.adafruit.com/adafruit-rgb-matrix-bonnet-for-raspberry-pi/driving-matrices) by following the instructions provided by Adafruit.
Test to make sure the panel works before you do anything else. You're looking for "HELLO WORLD" yellow happy face. If it's only partially displaying than reattach the panel to the bonnet.
```
cd /home/path/rpi-rgb-led-matrix/examples-api-use/
sudo ./demo -D 1 runtext.ppm --led-rows=32 --led-cols=64 --led-limit-refresh=60 --led-slowdown-gpio=2
```


### 4. Install Git and Configure Your Info
You'll need Git for downloading the project files and other resources:

```bash
sudo apt-get install git
git config --global user.name "YOUR NAME"
git config --global user.email "YOUR EMAIL"
```
Clone the repository:
```
git clone https://github.com/c0wsaysmoo/plane-tracker-rgb-pi
```
If the bridge on the bonnet is not soldered, you'll need to set HAT_PWM_ENABLED=False in the config file.

After cloning the files, move everything to the main folder, as some files need to be in /home/path/ rather than /home/path/plane-tracker-rgb-pi/
```
mv /home/path/plane-tracker-rgb-pi/* /home/path/
mkdir /home/path/logos
mv /home/path/logo/* /home/path/logos/
mv /home/path/logo2/* /home/path/logos/
```

For Linux Bookworm:
```
sudo apt install python3-pip
sudo rm /usr/lib/python3.11/EXTERNALLY-MANAGED
pip3 install pytz requests
pip3 install FlightRadarAPI
sudo setcap 'cap_sys_nice=eip' /usr/bin/python3.11
```

For Linux Bullseye:
```
sudo apt install python3-pip
sudo pip3 install pytz requests
sudo pip3 install FlightRadarAPI
```

Make the Script Executable
```
chmod +x /home/path/its-a-plane-python/its-a-plane.py
```

Run the Script

For Bookworm
```
/home/path/its-a-plane-python/its-a-plane.py
```

For Bullseye
```
sudo /home/path/its-a-plane-python/its-a-plane.py
```

Set Up the Script to Run on Boot

To ensure the script runs on boot, use crontab -e to edit the cron jobs and add the following lines:

For Bookworm
```
@reboot sleep 60 && ./its-a-plane.py
```

For Bullseye 
```
@reboot sleep 60 && sudo ./its-a-plane.py
```

Optional: Add a Power Button
If you'd like to add a power button, you can solder the button to the **GND/SCL** pins on the bonnet. Then, run the following commands:
```
git clone https://github.com/Howchoo/pi-power-button.git
./pi-power-button/script/install
```


I'm on reddit under this name if you have any questions or let me know if you make this.



