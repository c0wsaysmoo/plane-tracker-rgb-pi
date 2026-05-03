#!/bin/bash
# install-service.sh — Install the plane-tracker systemd service and secrets
#
# Usage:  sudo bash install-service.sh
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_SRC="$SCRIPT_DIR/plane-tracker.env"
SERVICE_SRC="$SCRIPT_DIR/plane-tracker.service"

ENV_DEST="/etc/plane-tracker.env"
SERVICE_DEST="/etc/systemd/system/plane-tracker.service"
OLD_SERVICE="flighttracker.service"

# --- Remove old service if it exists ---
if systemctl list-unit-files "$OLD_SERVICE" &>/dev/null && systemctl cat "$OLD_SERVICE" &>/dev/null; then
    echo "==> Stopping and removing old service: $OLD_SERVICE"
    systemctl stop "$OLD_SERVICE" 2>/dev/null || true
    systemctl disable "$OLD_SERVICE" 2>/dev/null || true
    rm -f "/etc/systemd/system/$OLD_SERVICE"
    systemctl daemon-reload
    echo "    Old service removed."
    echo ""
fi

echo "==> Installing environment file to $ENV_DEST (root-only, mode 0600)"
cp "$ENV_SRC" "$ENV_DEST"
chown root:root "$ENV_DEST"
chmod 0600 "$ENV_DEST"

echo "==> Installing systemd service to $SERVICE_DEST"
cp "$SERVICE_SRC" "$SERVICE_DEST"
chmod 0644 "$SERVICE_DEST"

echo "==> Reloading systemd daemon"
systemctl daemon-reload

echo "==> Enabling plane-tracker service to start on boot"
systemctl enable plane-tracker.service

echo ""
echo "Done. To start now:  sudo systemctl start plane-tracker"
echo "To view logs:        sudo journalctl -u plane-tracker -f"
echo ""
echo "IMPORTANT: Edit /etc/plane-tracker.env if you need to change API keys."
echo "           After editing, restart:  sudo systemctl restart plane-tracker"
