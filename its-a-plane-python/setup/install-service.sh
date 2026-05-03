#!/bin/bash
# install-service.sh — Install the plane-tracker systemd service and config
#
# Usage:  sudo bash install-service.sh
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVICE_SRC="$SCRIPT_DIR/plane-tracker.service"

ENV_DEST="/etc/plane-tracker.env"
SERVICE_DEST="/etc/systemd/system/plane-tracker.service"
OLD_SERVICE="flighttracker.service"

echo "============================================"
echo "  Plane Tracker — Service Installer"
echo "============================================"
echo ""

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

# --- Install environment file ---
if [ ! -f "$ENV_DEST" ]; then
    # Check for .env in project root first (user may have pre-filled it)
    if [ -f "$PROJECT_ROOT/.env" ]; then
        echo "==> Found .env in project root — installing to $ENV_DEST"
        cp "$PROJECT_ROOT/.env" "$ENV_DEST"
    else
        echo "==> No .env found — installing .env.example as starting point"
        cp "$PROJECT_ROOT/.env.example" "$ENV_DEST"
        echo ""
        echo "  !! You MUST edit $ENV_DEST to add your API keys !!"
        echo "     sudo nano $ENV_DEST"
        echo ""
    fi
    chown root:root "$ENV_DEST"
    chmod 0600 "$ENV_DEST"
    echo "  → Installed to $ENV_DEST (mode 0600, root-only)"
else
    echo "==> $ENV_DEST already exists — keeping existing configuration"
fi

echo ""

# --- Install systemd service ---
echo "==> Installing systemd service to $SERVICE_DEST"
cp "$SERVICE_SRC" "$SERVICE_DEST"
chmod 0644 "$SERVICE_DEST"

echo "==> Reloading systemd daemon"
systemctl daemon-reload

echo "==> Enabling plane-tracker service to start on boot"
systemctl enable plane-tracker.service

echo ""
echo "============================================"
echo "  Done!"
echo "============================================"
echo ""
echo "  Start:     sudo systemctl start plane-tracker"
echo "  Status:    sudo systemctl status plane-tracker"
echo "  Logs:      sudo journalctl -u plane-tracker -f"
echo ""
echo "  Edit config: sudo nano /etc/plane-tracker.env"
echo "  Then:        sudo systemctl restart plane-tracker"
echo ""
