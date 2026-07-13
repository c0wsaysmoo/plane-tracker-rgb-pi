# Hourly chime — systemd timer

The hourly chime is played by a **systemd timer**, not by the tracker process.

## Why not from the tracker itself

An `mpv` **`fork()`ed from the long-running tracker** fails ALSA card
enumeration ("cannot get card index for &lt;card&gt;") on every attempt — the
tracker's inherited process state breaks the child's audio device open, even
though the identical command plays fine from a shell, from `systemd-run`, and
inside the tracker's own cgroup. (Ruled out: USB autosuspend, CPU pinning,
environment, cgroup device policy, mlockall.) A timer fires the chime as its
**own service, started by PID 1**, where the device opens normally.

## Install

1. Edit `flight-tracker-chime.service` for your system: `User=`,
   `WorkingDirectory`, and `PYTHONPATH` (project dir + your site-packages, with
   the right `python3.X`). The user must be in the `audio` group.
2. Install and enable:
   ```sh
   sudo cp flight-tracker-chime.service flight-tracker-chime.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now flight-tracker-chime.timer
   ```
3. Test without waiting for the hour:
   ```sh
   sudo systemctl start flight-tracker-chime.service
   journalctl -u flight-tracker-chime.service -n 5    # -> "Hourly chime: rang ..."
   ```

The timer fires on the hour; `fire_once()` checks `HOURLY_CHIME_ENABLED` and the
quiet-hours window, so enabling/disabling and quiet hours work from config with
no restart. Enable the chime and set the volume / quiet hours in the web config
(Display → Hourly Chime) or `config.json`.
