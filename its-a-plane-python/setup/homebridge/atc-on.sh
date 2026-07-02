#!/bin/bash
# HomeKit switch ON -> start ATC audio. Playing during quiet hours from here
# counts as the explicit override (sticks until the window ends or off.sh).
curl -s -m 5 -X POST http://localhost:8080/api/atc/start >/dev/null
