#!/usr/bin/python3
import subprocess
import os
import sys
import logging

# Configure logging for systemd (no timestamps — journald adds them)
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("plane-tracker")


def validate_config():
    """Check that required configuration is present and log status."""
    from config import (
        FR24_API_KEY, TOMORROW_API_KEY,
        ZONE_HOME, LOCATION_HOME, TEMPERATURE_LOCATION,
    )

    logger.info("=" * 50)
    logger.info("Plane Tracker — Starting up")
    logger.info("=" * 50)

    errors = []

    # --- API Keys ---
    if FR24_API_KEY:
        masked = FR24_API_KEY[:8] + "..." + FR24_API_KEY[-4:]
        logger.info(f"  ✓ FR24_API_KEY: {masked}")
    else:
        errors.append("FR24_API_KEY")
        logger.error("  ✗ FR24_API_KEY is NOT SET — flight tracking will not work")

    if TOMORROW_API_KEY:
        masked = TOMORROW_API_KEY[:4] + "..." + TOMORROW_API_KEY[-4:]
        logger.info(f"  ✓ TOMORROW_API_KEY: {masked}")
    else:
        errors.append("TOMORROW_API_KEY")
        logger.error("  ✗ TOMORROW_API_KEY is NOT SET — weather/forecast will not work")

    # --- Location ---
    logger.info(f"  ✓ Home: {LOCATION_HOME[0]:.4f}, {LOCATION_HOME[1]:.4f}")
    logger.info(f"  ✓ Zone: N={ZONE_HOME['tl_y']}, S={ZONE_HOME['br_y']}, "
                f"W={ZONE_HOME['tl_x']}, E={ZONE_HOME['br_x']}")
    logger.info(f"  ✓ Weather location: {TEMPERATURE_LOCATION}")

    # --- Summary ---
    if errors:
        logger.warning(f"  Missing keys: {', '.join(errors)}")
        logger.warning("  Set them in /etc/plane-tracker.env and restart")
    else:
        logger.info("  All prerequisites OK")

    logger.info("=" * 50)
    return len(errors) == 0


if __name__ == "__main__":
    # Get directory of this script (its-a-plane.py)
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Validate configuration before starting
    validate_config()

    # Build path to web/app.py
    app_path = os.path.join(base_dir, "web", "app.py")

    # Start Flask server in background (use same interpreter as this process)
    subprocess.Popen([sys.executable, app_path])

    # Start display loop
    from display import Display
    run_text = Display()
    run_text.run()
