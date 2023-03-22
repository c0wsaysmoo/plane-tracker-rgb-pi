# planefinal
Hello.
So the basis of this project came from https://github.com/ColinWaddell/its-a-plane-python and his instructions are way better than mine. Mine is running on a Pi3A+ with adafruit bonnet (not hat)
I just added and changed his layout to include scrolling of the full airline name instead of code ie United Airlines 1234 instead of UAL1234 and added the matching logo in the corner. If there is no logo than it defaults to a blank plane. Also now displays the distance and direction from your location to the airplane.
I also added a 3 day forecast, well today and the next two days with the high and low temp. 
Although to get it to run on boot youll have to do a crontab -e and do @reboot sleep 60 && sudo ./its-a-plane.py
I also made it excutable but you may have to run this line as well
chmod +x ./its-a-plane.py![PXL_20230320_223944235](https://user-images.githubusercontent.com/127139588/226590887-d3836394-bf8b-482d-9d8e-149906a21cc8.jpg)
![fnished - Imgur](https://user-images.githubuse![PXL_20230322_212601997](https://user-images.githubusercontent.com/127139588/227058059-d402e0ac-b3e6-4dac-876e-3bba8e668232.jpg)
rcontent.com/127139588/226590890-79cbdf78-6249-4eed-8ea2-2a2b9e946d05.jpg)

I'm on reddit under this name if you have any questions. youll have to fill out the config file though
