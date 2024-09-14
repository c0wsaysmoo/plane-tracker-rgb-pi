# planefinal

Hello.
So the basis of this project came from [Colin Waddell](https://github.com/ColinWaddell/its-a-plane-python) and his instructions are way better than mine. Mine is running on a Pi3A+ with [adafruit bonnet](https://www.adafruit.com/product/3211) (not hat) and a [64x32 rgb panel](https://www.adafruit.com/product/2278) although any should work.
This will pull the logo from my database that matches the ICAO code. If there is no logo than it defaults to a blank plane. The airline name is the IATA code, meaning the logo is who is operating the flight and the name is who they are operating under. IE some regionals are partnered with multiple airlines. The airport codes are color coordinated to reflect the scheduled departure and arrival and actual and estimated. If the plane took off 0-20 from scheduled departure its green, 20-40 minutes is yellow, 40-60 minutes is orange, 1-4 hours is red, 4-8 hours is purple, and 8+ is blue. With the arrival if its estimated on time or early its green, 0-30 min late is yellow, 30-60 min late is orange, 1-4 hours is red, 4-8 hours is purple, and 8+ is blue. It now tells you the flights current distance from the origin airport and the distance left to the destination airport. It'll tell you the current distance and direction the plane is from your house as well and the distances and direction update the plane goes by. This has a 3 day forecast and the current temperature reflects the current humidity based on a white-blue gradient of 0% humid being white and 100% humid being blue. Since the logos are from my own collection I obviously don't have them all so if you find any missing add them or let me know so I can add them for others. IE a plane goes by and it says an airline but gives a blank plane picture. You can also choose to display the temperature to C or F, the distances in mi or KM, and the 12hr clock or 24hr clock in the config file. The clock changes color at sunrise and sunset, the date changes based on the current moonphase (white is full moon, purple is new moon, waning will have white on the left side, and waxing will have white on the right side), the distrance to origin and destination airport the units are now a different color to make it easier to read. The plane type and the distance/direction are now seperate colors. I changed the color palette as well.  I don't foresee many more updates do this, but who knows? Also I tried this on a pi zero and it worked(ish) there was a little bit of flicker I couldn't get to go away, but it works for sure on the pi3 A+. YMMV

I've spent a LOT of time messing with this and adding things and trying to make it as easy to setup as possible. If you'd like to get me a coffee I'd appreciate it!
paypal.me/c0wsaysmoo on paypal

once you get the pi going, you can use [this guide](https://linuxconfig.org/enabling-ssh-on-raspberry-pi-a-comprehensive-guide)

ssh into it and at the command prompt 

you'll want to [install the bonnet](https://learn.adafruit.com/adafruit-rgb-matrix-bonnet-for-raspberry-pi/driving-matrices) 

you'll need to install git and login with your stuff

sudo apt-get install git

git clone https://github.com/c0wsaysmoo/plane-tracker-rgb-pi

for Linux Bullseye

sudo apt install python3-pip 

sudo pip3 install pytz requests

sudo pip3 install FlightRadarAPI

for Linux Bookworm

sudo apt install python3-pip 

sudo rm /usr/lib/python3.11/EXTERNALLY-MANAGED

pip3 install pytz requests

pip3 install FlightRadarAPI

sudo setcap 'cap_sys_nice=eip' /usr/bin/python3.11




You'll have to make it executable by running chmod +x /home/path/its-a-plane-python/its-a-plane.py

you can test run it by the command prompt

sudo /home/path/its-a-plane-python/its-a-plane.py or /home/path/its-a-plane-python/its-a-plane.py

Although to get it to run on boot youll have to do a crontab -e and add @reboot sleep 60 && sudo ./its-a-plane.py #Bulleyes

 @reboot sleep 60 && ./its-a-plane.py #Bookworm

This also assumes the bridge is [soldered on the bonnet](https://learn.adafruit.com/assets/5772) if that's not the case youll have to be False under "HAT_PWM_ENABLED" in the config file

When you use git to pull these files you'll have to move everything into a folder up. logos and files must be in the main folder ie /home/xxx/ not /home/xxx/plane-tracker-rgb-pi

You'll have to move all the logos from logo2 and logo into logos

mkdir /home/path/logos

mv /home/path/logo/* /home/path/logos/

mv /home/path/logo2/* /home/path/logos/


You'll need to fill out the config file.


FYI the logos are going to be a little blurry, again they are 16x16 pixels so you can only do so much and since most were batch shrunk they haven't been touched up. If youd like to touch them up and add them that would be great OR add ones that are missing. Just save the new logo as XXX.png and 16x16 size and put it into the logo folder. I have most of them.


If you want to add a power button, you'll need to solder onto the bonnet pins on the GND/SCL then

git clone https://github.com/Howchoo/pi-power-button.git

./pi-power-button/script/install

(if you have bookworm you'll need to do the following)

sudo nano /boot/firmware/config.txt

and at the bottom under "all" add

dtoverlay=gpio-shutdown,gpio_pin=3,active_low=1,gpio_pull=up

I'm on reddit under this name if you have any questions or let me know if you make this.

![PXL_20231119_213716793](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/fb2e236c-bc9c-4469-adaa-6b59b7649bce)
![PXL_20231119_213727328 MP](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/a2121fe6-e051-4097-b4bd-3868c368a068)
![PXL_20231119_214846285](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/7889a9c0-8b4a-4bb7-bf67-2b2e7a29a16b)
![PXL_20231119_214848797](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/41a50f29-f12c-41db-b93b-2ef41a8e7805)
![PXL_20230623_194045200](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/b901fc67-098b-40d3-91cd-3acf335d06c3)
![PXL_20230623_194026493](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/ebfca26a-19c1-491c-a44c-93239c9a75f2)
![PXL_20230623_194018097](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/4505c237-88da-49a0-836a-f13e0c5d5631)



