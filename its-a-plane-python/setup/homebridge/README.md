# ATC Radio as a HomeKit switch

Exposes the tracker's ATC audio as a plain on/off **Switch** in Apple Home
(same pattern as the nightlight toggle). ON = `/api/atc/start` (during quiet
hours this IS the override — audio sticks until the window ends or OFF).
OFF = `/api/atc/stop` (sticks; the next ON restores the previous auto/manual
mode). The state script keeps the tile honest when playback was changed from
the web UI or stopped by quiet hours.

## Install (on the Pi running Homebridge)

```bash
sudo cp atc-on.sh atc-off.sh atc-state.sh /var/lib/homebridge/scripts/
sudo chmod +x /var/lib/homebridge/scripts/atc-*.sh
```

If Homebridge runs on a DIFFERENT host than the tracker, edit the scripts and
replace `localhost:8080` with the tracker Pi's address (e.g.
`http://192.168.1.50:8080`).

## homebridge-script2 accessory (matches the nightlight setup)

```json
{
    "accessory": "Script2",
    "name": "ATC Radio",
    "on": "/var/lib/homebridge/scripts/atc-on.sh",
    "off": "/var/lib/homebridge/scripts/atc-off.sh",
    "state": "/var/lib/homebridge/scripts/atc-state.sh",
    "on_value": "true"
}
```

## Alternative: homebridge-http-switch (no scripts needed)

```json
{
    "accessory": "HTTP-SWITCH",
    "name": "ATC Radio",
    "switchType": "stateful",
    "onUrl":  { "url": "http://localhost:8080/api/atc/start", "method": "POST" },
    "offUrl": { "url": "http://localhost:8080/api/atc/stop",  "method": "POST" },
    "statusUrl": "http://localhost:8080/api/atc/status",
    "statusPattern": "\"playing\": true"
}
```

Notes:
- The switch controls the SERVER-side player (USB speaker / Chromecast /
  AirPlay outputs). With the `browser` output selected, ON arms the stream
  and any open mirror tab with audio enabled plays it; a tab that never had
  a click can't autoplay (browser policy) and shows its resume overlay.
- Add it to HomeKit automations/scenes like any switch (e.g. "Good Morning"
  scene turns the radio on; it goes quiet again at ATC_QUIET_HOURS unless
  you flip it on during the window, which overrides for that night).
