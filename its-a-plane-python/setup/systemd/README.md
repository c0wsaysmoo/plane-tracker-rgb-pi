# Hourly chime via a systemd timer (optional)

The hourly chime can be driven two ways:

1. **Built-in scheduler (default).** A daemon thread inside the tracker sleeps to
   the top of each hour and plays the chime. Nothing to install — just set
   `HOURLY_CHIME_ENABLED=True` (and optionally the volume / quiet-hours).

2. **External systemd timer (this directory).** Recommended **if the chime logs
   `NO SOUND — ... cannot get card index`** (or otherwise never plays) even
   though playing the wav from a shell works.

## Why the timer is sometimes needed

An `mpv` process **`fork()`ed from the long-running tracker** can fail ALSA card
enumeration on every attempt — the tracker process's inherited state breaks the
child's audio device open, even though the identical command works from a shell,
from `systemd-run`, and inside the tracker's own cgroup. (Ruled out: USB
autosuspend, CPU pinning, environment, cgroup device policy, mlockall.) Firing
the chime as its **own systemd service — started by PID 1, not forked from the
tracker** — runs `mpv` in a clean context where the device opens normally.

## Install

1. Edit `flight-tracker-chime.service` for your system: the `User=`, the
   `WorkingDirectory`, and the `PYTHONPATH` (project dir + your site-packages,
   with the right `python3.X`). The user must be in the `audio` group.
2. Copy both units and enable the timer:
   ```sh
   sudo cp flight-tracker-chime.service flight-tracker-chime.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now flight-tracker-chime.timer
   ```
3. Tell the tracker to **not** also run its in-process scheduler (avoids a
   double fire) by exporting `CHIME_EXTERNAL_SCHEDULER=1` in the tracker's
   environment (e.g. add `CHIME_EXTERNAL_SCHEDULER=1` to the project `.env`, or
   `Environment=CHIME_EXTERNAL_SCHEDULER=1` in the tracker's service unit), then
   restart the tracker.
4. Test without waiting for the hour:
   ```sh
   sudo systemctl start flight-tracker-chime.service
   journalctl -u flight-tracker-chime.service -n 5   # -> "Hourly chime: rang ..."
   ```

The timer fires on the hour; `fire_once()` still checks `HOURLY_CHIME_ENABLED`
and the quiet-hours window, so enabling/disabling and quiet hours work exactly
as with the built-in scheduler.
