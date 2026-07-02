#!/bin/bash
# HomeKit switch OFF -> stop ATC audio (sticks: mode drops to off; the next
# ON restores the previous auto/manual mode).
curl -s -m 5 -X POST http://localhost:8080/api/atc/stop >/dev/null
