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
`http://<tracker-pi>:8080`).

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

## Matter exposure (Google Home + Apple Home)

The same on/off switch can also be exposed as a **Matter device**: define a
RESTful switch in Home Assistant pointing at `/api/atc/start|stop|status`,
then bridge it with [Matterbridge](https://github.com/Luligu/matterbridge)'s
`matterbridge-hass` plugin (entity whitelist) — Google Home and Apple Home
both pair to it natively. The scripts below are the plain-Homebridge
alternative for HomeKit-only setups.

## Recommended switch semantics

**The switch plays out of the tracker's OWN speaker** (the Pi's USB speaker):

```json
"on": "/var/lib/homebridge/scripts/atc-on.sh usb"
```

That makes the tile deterministic — ON always means "the tracker starts
talking", regardless of what output someone last picked in the web UI.
Browser/Chromecast/AirPlay listening stays a web-UI choice (mirror bar icons
or config page).

`atc-on.sh` accepts any output id (see `GET /api/atc/outputs`), so per-room
switches ("ATC Pool Room" → `atc-on.sh 'chromecast:<uuid>'`) are POSSIBLE —
but with one shared radio they get clunky (last one wins, all tiles show ON
together), so the deliberate setup is: one switch = own speaker, everything
else via the web UI.

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
