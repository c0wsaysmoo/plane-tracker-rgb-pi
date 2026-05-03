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

# --- Install environment file with secrets ---
if [ ! -f "$ENV_DEST" ]; then
    if [ -f "$ENV_SRC" ]; then
        echo "==> Copying environment file to $ENV_DEST"
        cp "$ENV_SRC" "$ENV_DEST"
    else
        echo "==> Creating $ENV_DEST (enter your API keys)"
        echo ""
        read -p "  FR24 API Key (subscription_key|token): " FR24_KEY
        read -p "  Tomorrow.io API Key: " TOMORROW_KEY
        cat > "$ENV_DEST" <<EOF
FR24_API_KEY=${FR24_KEY}
TOMORROW_API_KEY=${TOMORROW_KEY}
EOF
    fi
    chown root:root "$ENV_DEST"
    chmod 0600 "$ENV_DEST"
    echo "  → Saved to $ENV_DEST (mode 0600, root-only)"
else
    echo "==> $ENV_DEST already exists — keeping existing keys"
fi

echo ""
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
echo "  Edit keys: sudo nano /etc/plane-tracker.env"
echo "  Then:      sudo systemctl restart plane-tracker"
echo ""
