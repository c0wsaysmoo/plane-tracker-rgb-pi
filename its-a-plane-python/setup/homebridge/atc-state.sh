#!/bin/bash
# HomeKit state poll -> "true"/"false" so the Home app tile reflects reality
# (e.g. quiet hours stopped playback, or it was started from the web UI).
curl -s -m 5 http://localhost:8080/api/atc/status \
  | python3 -c "import sys,json;print('true' if json.load(sys.stdin).get('playing') else 'false')" \
  2>/dev/null || echo false
