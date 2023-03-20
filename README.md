# planefinal
Hello.
So the basis of this project came from https://github.com/ColinWaddell/its-a-plane-python and his instructions are way better than mine. Mine is running on a Pi3A+ with adafruit bonnet (not hat)
I just added and changed his layout to include scrolling of the full airline name instead of code ie United Airlines 1234 instead of UAL1234 and added the matching logo in the corner. If there is no logo than it defaults to a blank plane.
I also added a 3 day forecast, well today and the next two days with the high and low temp. 
Although to get it to run on boot youll have to do a crontab -e and do @reboot sleep 60 && sudo ./its-a-plane.py
I also made it excutable but you may have to run this line as well
chmod +x ./its-a-plane.py
I'm on reddit under this name if you have any questions.
