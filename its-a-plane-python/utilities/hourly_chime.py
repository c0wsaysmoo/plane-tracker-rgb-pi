"""
Hourly cabin chime — plays a short "ding-dong" wav on the hour through the
Pi's local speaker (USB audio card if present, else the onboard output).

A dedicated daemon thread sleeps until the top of the next hour and fires
there (accurate to a fraction of a second — no frame-loop polling). It
re-reads config each hour, so the enable toggle, volume, and quiet-hours
window take effect on the next hour without a restart.

Self-contained: only needs `mpv` installed. Off by default; enable in the web
config (Display → Hourly Chime) or config.json (display.hourly_chime_enabled).

Playing OVER another stream (optional): a USB speaker's `plughw:` device is
exclusive, so if something else already holds it (e.g. a live audio stream) the
chime gets 'Device or resource busy' and logs "NO SOUND" for that hour — it
never crashes, it just can't share the card. To let them mix, define an ALSA
dmix PCM named `usbmix` in /etc/asound.conf (or ~/.asoundrc); this module then
targets that shared mixer automatically. Example:

    pcm.usbmix { type dmix; ipc_key 2048; ipc_perm 0666;
                 slave { pcm "hw:CARD=<your-usb-card>,DEV=0";
                         rate 48000; channels 2; } }
"""
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHIME_FILE = os.path.join(_BASE_DIR, "data", "ding_dong.wav")


def _detect_usb_alsa_device():
    """mpv --audio-device string for the first USB-audio card, or '' if none
    (mpv then falls back to its own default).

    Prefers a shared software-mix PCM ('usbmix', an ALSA dmix defined in
    /etc/asound.conf or ~/.asoundrc) when one is configured, so the chime can
    play OVER another stream on the same speaker (e.g. live ATC audio) instead
    of both fighting over the exclusive hw device — the loser gets 'Device or
    resource busy' and stays silent. Falls back to plughw: (which also lets
    ALSA convert the sample rate/format for cheap USB DACs) when no dmix is set
    up."""
    try:
        with open("/proc/asound/cards") as f:
            text = f.read()
    except OSError:
        return ""
    card = None
    # e.g. " 1 [UACDemoV10     ]: USB-Audio - USB Audio Device"
    for m in re.finditer(r"^\s*\d+\s*\[([^\]]+)\]:\s*(.+)$", text, re.M):
        name, desc = m.group(1).strip(), m.group(2)
        if "USB-Audio" in desc or "USB Audio" in desc:
            card = name
            break
    if not card:
        return ""
    for cfg in ("/etc/asound.conf", os.path.expanduser("~/.asoundrc")):
        try:
            with open(cfg) as f:
                if "pcm.usbmix" in f.read():
                    return "alsa/usbmix"
        except OSError:
            continue
    return f"alsa/plughw:CARD={card},DEV=0"


def play(volume: int = 50):
    """Play the chime and report whether it was actually audible. Never raises.

    :param volume: mpv volume 0-100 (the wav is normalised).
    """
    try:
        args = ["mpv", "--no-video", "--no-terminal", "--really-quiet",
                f"--volume={int(volume)}"]
        device = _detect_usb_alsa_device()
        if device:
            args.append(f"--audio-device={device}")
        args.append(_CHIME_FILE)
        proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            os.sched_setaffinity(proc.pid, {2})
        except Exception:
            pass
        # VERIFY it actually played — don't claim a ring just because Popen
        # succeeded. plughw: is an EXCLUSIVE ALSA device: if something else is
        # already using the card, mpv exits NON-ZERO ("Could not
        # open/initialize audio device -> no sound") and nothing is audible.
        # Wait for the (~2s) clip and check the exit code — deterministic,
        # unlike a fixed-delay poll — so the log reflects the room, not the
        # spawn.
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()  # reap the SIGKILLed mpv — else returncode stays None
                         # (kill() doesn't set it) and it lingers as a <defunct>
                         # zombie until the next hour's Popen happens to reap it.
        if proc.returncode == 0:
            logger.info(f"Hourly chime: rang (volume {int(volume)}, "
                        f"device {device or 'default'})")
        else:
            logger.warning(
                "Hourly chime: NO SOUND — mpv exited rc=%s; the audio device "
                "is busy or unavailable.", proc.returncode)
    except FileNotFoundError:
        logger.warning("Hourly chime: mpv not installed — skipping")
    except Exception as e:
        logger.warning(f"Hourly chime: failed to play ({e})")


# ── Scheduler ────────────────────────────────────────────────────────────

def _parse_hhmm(s):
    """'HH:MM' -> minutes since midnight, or None if unparseable/blank."""
    try:
        h, m = str(s).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _in_quiet_hours(start_s, end_s, now=None):
    """True if `now` falls in [start, end). Handles overnight windows
    (22:00-08:00). Blank or equal start/end => never quiet."""
    a, b = _parse_hhmm(start_s), _parse_hhmm(end_s)
    if a is None or b is None or a == b:
        return False
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    return (a <= cur < b) if a < b else (cur >= a or cur < b)


def _seconds_to_next_hour(now=None):
    now = now or datetime.now()
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return max(1.0, (nxt - now).total_seconds())


def _run_scheduler():
    while True:
        # +0.5s margin so we always wake just PAST the boundary.
        time.sleep(_seconds_to_next_hour() + 0.5)
        try:
            import config as cfg
            # Re-read config from disk so a web-UI save (which reloads in the
            # separate web process) is picked up here next hour.
            try:
                cfg.reload()
            except Exception:
                pass
            if not getattr(cfg, "HOURLY_CHIME_ENABLED", False):
                continue
            if _in_quiet_hours(getattr(cfg, "HOURLY_CHIME_QUIET_START", ""),
                               getattr(cfg, "HOURLY_CHIME_QUIET_END", "")):
                logger.info("Hourly chime: quiet hours — skipped")
                continue
            play(getattr(cfg, "HOURLY_CHIME_VOLUME", 50))
        except Exception as e:
            logger.warning(f"Hourly chime scheduler error: {e}")


_scheduler_started = False


def start_scheduler():
    """Start the hourly scheduler thread once. Safe to call repeatedly."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_run_scheduler, daemon=True,
                     name="hourly-chime").start()
    logger.info("Hourly chime scheduler started")
