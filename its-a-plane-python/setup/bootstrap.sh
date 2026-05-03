#!/bin/bash
# bootstrap.sh — Install, upgrade, or migrate the plane-tracker service
#
# This script is idempotent: run it any time to bring the system up to date.
#
# What it does:
#   1. Detects and removes the old flighttracker.service (if present)
#   2. Clones or updates the repo from the fork
#   3. Installs/upgrades Python dependencies
#   4. Sets up /etc/plane-tracker.env (prompts on first install, preserves existing)
#   5. Installs/updates the systemd service unit
#   6. Restarts the service
#
# Usage:
#   sudo bash bootstrap.sh
#
# Or remotely (first install):
#   curl -sSL https://raw.githubusercontent.com/a10kiloham/plane-tracker-rgb-pi/main/its-a-plane-python/setup/bootstrap.sh | sudo bash
#
set -e

# --- Configuration ---
REPO_URL="https://github.com/a10kiloham/plane-tracker-rgb-pi.git"
REPO_DIR="/home/robk/plane-tracker-rgb-pi"
ENV_DEST="/etc/plane-tracker.env"
NEW_SERVICE="plane-tracker.service"
OLD_SERVICE="flighttracker.service"
SERVICE_DEST="/etc/systemd/system/$NEW_SERVICE"

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║   Plane Tracker — Bootstrap / Upgrade      ║"
echo "╚════════════════════════════════════════════╝"
echo ""

# --- Step 1: Detect and remove old service ---
if systemctl cat "$OLD_SERVICE" &>/dev/null; then
    echo "⚠  Old service detected: $OLD_SERVICE"
    echo "   Stopping and removing..."
    systemctl stop "$OLD_SERVICE" 2>/dev/null || true
    systemctl disable "$OLD_SERVICE" 2>/dev/null || true
    rm -f "/etc/systemd/system/$OLD_SERVICE"
    systemctl daemon-reload
    echo "   ✓ Old service removed"
    echo ""
fi

# --- Step 2: Stop current service if running (for clean upgrade) ---
if systemctl is-active "$NEW_SERVICE" &>/dev/null; then
    echo "→ Stopping $NEW_SERVICE for upgrade..."
    systemctl stop "$NEW_SERVICE"
    echo ""
fi

# --- Step 3: Clone or update the repository ---
if [ -d "$REPO_DIR/.git" ]; then
    echo "→ Updating existing repo at $REPO_DIR"
    cd "$REPO_DIR"

    # Ensure remote points to the correct fork
    CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
    if [ "$CURRENT_REMOTE" != "$REPO_URL" ]; then
        echo "   Switching remote from: $CURRENT_REMOTE"
        echo "   To: $REPO_URL"
        git remote set-url origin "$REPO_URL"
    fi

    # Stash local changes, pull latest
    git stash 2>/dev/null || true
    git fetch origin
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    git reset --hard "origin/$BRANCH"
    echo "   ✓ Updated to latest ($(git log --oneline -1))"
else
    echo "→ Cloning repo to $REPO_DIR"
    rm -rf "$REPO_DIR"
    git clone "$REPO_URL" "$REPO_DIR"
    cd "$REPO_DIR"
    echo "   ✓ Cloned ($(git log --oneline -1))"
fi
echo ""

# --- Step 4: Install/upgrade Python dependencies ---
echo "→ Installing Python dependencies..."
pip install --break-system-packages --ignore-installed typing-extensions \
    -r "$REPO_DIR/requirements.txt" 2>&1 | tail -5
echo "   ✓ Dependencies installed"
echo ""

# --- Step 5: Set up environment file ---
if [ ! -f "$ENV_DEST" ]; then
    echo "→ First install: setting up $ENV_DEST"
    echo ""

    if [ -f "$REPO_DIR/.env" ]; then
        # User pre-created a .env — use it
        echo "   Found .env in repo — using it"
        cp "$REPO_DIR/.env" "$ENV_DEST"
    else
        # Copy example and prompt for secrets
        cp "$REPO_DIR/.env.example" "$ENV_DEST"
        echo "   Copied .env.example → $ENV_DEST"
        echo ""
        echo "   You need to add your API keys."
        read -p "   FR24 API Key (subscription_key|token): " FR24_KEY
        read -p "   Tomorrow.io API Key: " TOMORROW_KEY

        if [ -n "$FR24_KEY" ]; then
            sed -i "s|^FR24_API_KEY=.*|FR24_API_KEY=$FR24_KEY|" "$ENV_DEST"
        fi
        if [ -n "$TOMORROW_KEY" ]; then
            sed -i "s|^TOMORROW_API_KEY=.*|TOMORROW_API_KEY=$TOMORROW_KEY|" "$ENV_DEST"
        fi
    fi

    chown root:root "$ENV_DEST"
    chmod 0600 "$ENV_DEST"
    echo "   ✓ Saved to $ENV_DEST (mode 0600)"
else
    echo "→ $ENV_DEST exists — preserving current configuration"
fi
echo ""

# --- Step 6: Install/update systemd service ---
echo "→ Installing systemd service..."
cp "$REPO_DIR/its-a-plane-python/setup/plane-tracker.service" "$SERVICE_DEST"
chmod 0644 "$SERVICE_DEST"
systemctl daemon-reload
systemctl enable "$NEW_SERVICE"
echo "   ✓ Service installed and enabled"
echo ""

# --- Step 7: Start the service ---
echo "→ Starting $NEW_SERVICE..."
systemctl start "$NEW_SERVICE"
sleep 2

if systemctl is-active "$NEW_SERVICE" &>/dev/null; then
    echo "   ✓ Service is running"
else
    echo "   ✗ Service failed to start — check logs:"
    echo "     sudo journalctl -u $NEW_SERVICE -n 20"
fi

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║   Done!                                    ║"
echo "╠════════════════════════════════════════════╣"
echo "║   Status:  sudo systemctl status $NEW_SERVICE  ║"
echo "║   Logs:    sudo journalctl -u $NEW_SERVICE -f  ║"
echo "║   Config:  sudo nano $ENV_DEST    ║"
echo "║   Upgrade: re-run this script              ║"
echo "╚════════════════════════════════════════════╝"
echo ""
