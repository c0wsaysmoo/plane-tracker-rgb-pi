#!/bin/bash
# HomeKit switch ON -> start ATC audio. Playing during quiet hours from here
# counts as the explicit override (sticks until the window ends or off.sh).
#
# Optional $1 = output id (from /api/atc/outputs) to select first — lets you
# expose one HomeKit switch per destination:
#   atc-on.sh                          # start on the currently selected output
#   atc-on.sh usb                      # Pi USB speaker
#   atc-on.sh "chromecast:<uuid>"      # a specific cast target / speaker group
#   atc-on.sh "airplay:<id>"           # an AirPlay receiver
if [ -n "$1" ]; then
  curl -s -m 5 -X POST -H 'Content-Type: application/json' \
       -d "{\"output\":\"$1\"}" http://localhost:8080/api/atc/select-output >/dev/null
fi
curl -s -m 5 -X POST http://localhost:8080/api/atc/start >/dev/null
