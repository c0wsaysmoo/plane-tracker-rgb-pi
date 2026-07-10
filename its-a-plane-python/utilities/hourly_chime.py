"""
Hourly cabin chime — plays a short "ding-dong" wav on the hour through the
Pi's local speaker (USB audio card if present, else the onboard output).

Self-contained: only needs `mpv` installed. Off by default; enable in the web
config (Display → Hourly Chime) or config.json (display.hourly_chime_enabled).
"""
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHIME_FILE = os.path.join(_BASE_DIR, "data", "ding_dong.wav")


def _detect_usb_alsa_device():
    """mpv --audio-device string for the first USB-audio card, or '' if none
    (mpv then falls back to its own default). plughw: lets ALSA convert the
    sample rate/format for cheap USB DACs."""
    try:
        with open("/proc/asound/cards") as f:
            text = f.read()
    except OSError:
        return ""
    # e.g. " 1 [UACDemoV10     ]: USB-Audio - USB Audio Device"
    for m in re.finditer(r"^\s*\d+\s*\[([^\]]+)\]:\s*(.+)$", text, re.M):
        name, desc = m.group(1).strip(), m.group(2)
        if "USB-Audio" in desc or "USB Audio" in desc:
            return f"alsa/plughw:CARD={name},DEV=0"
    return ""


def play(volume: int = 50):
    """Fire-and-forget local playback of the chime. Never raises.

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
        # Pin off the display core if possible (best effort); harmless if the
        # core count is small.
        try:
            os.sched_setaffinity(proc.pid, {2})
        except Exception:
            pass
        # Positive log so a ring is verifiable (playback is fire-and-forget).
        logger.info(f"Hourly chime: rang (volume {int(volume)}, "
                    f"device {device or 'default'})")
    except FileNotFoundError:
        logger.warning("Hourly chime: mpv not installed — skipping")
    except Exception as e:
        logger.warning(f"Hourly chime: failed to play ({e})")
