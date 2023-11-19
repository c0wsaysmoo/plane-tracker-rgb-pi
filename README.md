# planefinal
crap looks like in all the updates the default plane pic got taken out, so that is fixed now. found a better collection of airline logos that I should have most of them. found a weird issue where I had to disable the IVP6 on the raspberry pi or else it kept losing connection and would see the blinking light in the top right flashing nonstop so you may have to do that as well
More updates (I need a new hobby)
The clock changes color at sunrise and sunset, the date changes based on the current moonphase (white is full moon, purple is new moon, waning will have white on the left side, and waxing will have white on the right side), the distrance to origin and destination airport the units are now a different color to make it easier to read AND I put the 12/24clock preference in the config file.

Hello.
So the basis of this project came from [Colin Waddell](https://github.com/ColinWaddell/its-a-plane-python) and his instructions are way better than mine. Mine is running on a Pi3A+ with [adafruit bonnet](https://www.adafruit.com/product/3211) (not hat) and a [64x32 rgb panel](https://www.adafruit.com/product/2278) although any should work.
This will pull the logo from my database that matches the ICAO code. If there is no logo than it defaults to a blank plane. The airline name is the IATA code, meaning the logo is who is operating the flight and the name is who they are operating under. IE some regionals are partnered with multiple airlines. The airport codes are color coordinated to reflect the scheduled departure and arrival and actual and estimated. If the plane took off 0-20 from scheduled departure its green, 20-40 minutes is yellow, 40-60 minutes is orange, 1-4 hours is red, and 4+ hours is purple. With the arrival if its estimated on time or early its green, 0-30 min late is yellow, 30-60 min late is orange, 1-4 hours is red, and 4+ hours is purple. It now tells you the flights current distance from the origin airport and the distance left to the destination airport. It'll tell you the current distance and direction the plane is from your house as well and the distances and direction update the plane goes by. This has a 3 day forecast and the current temperature reflects the current humidity based on a white-blue gradient of 0% humid being white and 100% humid being blue. Since the logos are from my own collection I obviously don't have them all so if you find any missing add them or let me know so I can add them for others. IE a plane goes by and it says an airline but gives a blank plane picture. In the config file youll need the altitude of your house in feet. You can also choose to display the temperature to C or F and the distances in mi or KM in the config file. I don't foresee many more updates do this, but who knows? Also I tried this on a pi zero and it worked(ish) there was a little bit of flicker I couldn't get to go away, but it works for sure on the pi3 A+. YMMV

once you get the pi going, you can use [this guide](https://linuxconfig.org/enabling-ssh-on-raspberry-pi-a-comprehensive-guide)

ssh into it and at the command prompt 

you'll want to [install the bonnet](https://learn.adafruit.com/adafruit-rgb-matrix-bonnet-for-raspberry-pi/driving-matrices) 

git clone https://github.com/c0wsaysmoo/plane-tracker-rgb-pi

sudo apt install python3-pip 

sudo pip3 install pytz requests (this only works with python version 3.9)

sudo pip3 install FlightRadarAPI (this only works with python version 3.9)

cd its-a-plane-python


You'll have to make it executable by running chmod +x /home/path/its-a-plane-python/its-a-plane.py
Although to get it to run on boot youll have to do a crontab -e and add @reboot sleep 60 && sudo ./its-a-plane.py

This also assumes the bridge is [soldered on the bonnet](https://learn.adafruit.com/assets/5772) if that's not the case youll have to be False under "HAT_PWM_ENABLED" in the config file

When you use git to pull these files you'll have to move everything into a folder up. logos and files must be in the main folder ie /home/xxx/ not /home/xxx/plane-tracker-rgb-pi

You'll need to fill out the config file.


FYI the logos are going to be a little blurry, again they are 16x16 pixels so you can only do so much and since most were batch shrunk they haven't been touched up. If youd like to touch them up and add them that would be great OR add ones that are missing. Just save the new logo as XXX.png and 16x16 size and put it into the logo folder. I have most of them.


If you want to add a power button, you'll need to solder onto the bonnet pins on the GND/SCL then

git clone https://github.com/Howchoo/pi-power-button.git

./pi-power-button/script/install

I'm on reddit under this name if you have any questions or let me know if you make this.
![PXL_20230819_190424332](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/aff9d3dc-eeb1-40b3-963a-d243fb5db403)
![PXL_20230813_181336664](https://github.com/c0wsaysmoo/plane-tracker-rgb-pi/assets/127139588/4578076f-61c9-45cd-b8f6-3fbda4461e0e)




