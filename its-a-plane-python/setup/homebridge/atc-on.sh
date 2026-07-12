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
  # Build the JSON body with python3 (guaranteed present) so an output id
  # containing a quote/backslash can't produce invalid JSON that silently
  # no-ops select-output and leaves /start on the wrong (e.g. bedroom) target.
  body=$(python3 -c 'import json,sys; print(json.dumps({"output": sys.argv[1]}))' "$1")
  if ! curl -sf -m 5 -X POST -H 'Content-Type: application/json' \
       -d "$body" http://localhost:8080/api/atc/select-output >/dev/null; then
    echo "atc-on: select-output failed for '$1' — not starting" >&2
    exit 1
  fi
fi
curl -s -m 5 -X POST http://localhost:8080/api/atc/start >/dev/null
