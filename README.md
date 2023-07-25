# planefinal

So changed the source of the weather since it was constantly off to Tomorrow.IO in order to do that had to make other adjustments. Now it'll only pull the current temp every 10 minutes, and update the forecast at every hour. also if you are already using this you'll need to get pyzt. as always if you have any issues I'll do my best to help but I don't think I can do much. Please read the entire README first. Also again if you find a ICAO code that I don't have let me know so I can add it for others. The connection timeout errors should be fixed now, also updated some of the weathe icons.

Hello.
So the basis of this project came from https://github.com/ColinWaddell/its-a-plane-python and his instructions are way better than mine. Mine is running on a Pi3A+ with adafruit bonnet (not hat) https://www.adafruit.com/product/3211 and a 64x32 rgb panel https://www.adafruit.com/product/2278 although any should work
I just added and changed his layout to include scrolling of the full airline name instead of callsign ie Airline Name 1234 instead of aln1234 and added the matching logo in the corner. If there is no logo than it defaults to a blank plane. The logo is tied to the ICAO code where the airline name is the IATA code, meaning the logo is who is operating the flight and the name is who they are operating under. IE some regionals are partnered with multiple airlines. Also now displays the distance and direction from your location to the airplane and wil update as the plane flies through the box.
I also added a 3 day forecast, well today and the next two days with the high and low temp.

once you get the pi going

https://linuxconfig.org/enabling-ssh-on-raspberry-pi-a-comprehensive-guide

ssh into it and at the command prompt 

git clone https://github.com/c0wsaysmoo/plane-tracker-rgb-pi

sudo apt install python3-pip

sudo pip3 install pytz requests

sudo pip3 install FlightRadarAPI

cd its-a-plane-python


You'll have to make it executable by running chmod +x /home/path/its-a-plane-python/its-a-plane.py
Although to get it to run on boot youll have to do a crontab -e and add @reboot sleep 60 && sudo ./its-a-plane.py

This also assumes the bridge is soldered on the bonnet https://learn.adafruit.com/assets/5772 if that's not the case youll have to be False under "HAT_PWM_ENABLED" in the config file

When you use git to pull these files you'll have to move everything into a folder up. logos and files must be in the main folder ie /home/xxx/ not /home/xxx/plane-tracker-rgb-pi

You'll need to fill out the config file. I use https://www.latlong.net/ to find lat and long

If you want to change the clock to 24hr you'll need to edit the clock.py in scene and change line 29 from ("%l:%M") to ("%H:%M")

FYI the logos are going to be a little blurry, again they are 16x16 pixels so you can only do so much and since most were batch shrunk they haven't been touched up. If youd like to touch them up and add them that would be great OR add ones that are missing. Just save the new logo as XXX.png and 16x16 size and put it into the logo folder. I have most of the ones around me but your milage may vary. 


If you want to add a power button, you'll need to solder onto the bonnet pins on the GND/SCL then

git clone https://github.com/Howchoo/pi-power-button.git

./pi-power-button/script/install

I'm on reddit under this name if you have any questions or let me know if you make this. youll have to fill out the config file though
![PXL_20230323_000203495](https://user-images.githubusercontent.com/127139588/227799263-05bafba3-a847-4832-8635-f495ca50358b.jpg)
![PXL_20230323_000207446](https://user-images.githubusercontent.com/127139588/227799264-3d6b1132-a177-4c56-ae65-a33387daacf2.jpg)
![PXL_20230323_003309032](https://user-images.githubusercontent.com/127139588/227799265-e80bd531-704b-440d-8b6f-0e2a6b373294.jpg)


