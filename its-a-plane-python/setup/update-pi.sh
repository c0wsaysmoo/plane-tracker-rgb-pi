#!/bin/bash
# update-pi.sh — Run this ON THE RASPBERRY PI to switch from the old repo
# to your fork and install everything.
#
# Usage (on Pi):
#   curl -sSL https://raw.githubusercontent.com/a10kiloham/plane-tracker-rgb-pi/main/its-a-plane-python/setup/update-pi.sh | sudo bash
#
# Or if you've already cloned manually:
#   cd ~/plane-tracker-rgb-pi && sudo bash its-a-plane-python/setup/update-pi.sh
#
set -e

REPO_DIR="$HOME/plane-tracker-rgb-pi"
FORK_URL="https://github.com/a10kiloham/plane-tracker-rgb-pi.git"
ENV_DEST="/etc/plane-tracker.env"

echo "============================================"
echo "  Plane Tracker — Switch to forked repo"
echo "============================================"
echo ""

# --- Step 1: Switch git remote or fresh clone ---
if [ -d "$REPO_DIR/.git" ]; then
    echo "==> Existing repo found at $REPO_DIR"
    cd "$REPO_DIR"

    # Stash any local changes
    git stash 2>/dev/null || true

    # Update remote to your fork
    echo "==> Updating origin remote to $FORK_URL"
    git remote set-url origin "$FORK_URL"

    # Fetch and reset to latest
    echo "==> Pulling latest from your fork..."
    git fetch origin
    git checkout main 2>/dev/null || git checkout master
    git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)
else
    echo "==> Cloning your fork to $REPO_DIR"
    git clone "$FORK_URL" "$REPO_DIR"
    cd "$REPO_DIR"
fi

echo ""

# --- Step 2: Install Python dependencies (using a virtual environment) ---
echo "==> Ensuring python3-venv is available..."
apt-get update -qq && apt-get install -y -qq python3-venv python3-dev 2>/dev/null || true

VENV_DIR="$REPO_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
else
    echo "==> Virtual environment already exists at $VENV_DIR"
fi

echo "==> Installing Python dependencies into venv..."
"$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel 2>&1 | tail -3
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt" 2>&1 | tail -5
echo "   ✓ Dependencies installed"

echo ""

# --- Step 3: Create environment file with secrets ---
if [ ! -f "$ENV_DEST" ]; then
    echo "==> Creating $ENV_DEST (you'll be prompted for your keys)"
    echo ""

    read -p "  FR24 API Key (subscription_key|token): " FR24_KEY
    read -p "  Tomorrow.io API Key: " TOMORROW_KEY

    cat > "$ENV_DEST" <<EOF
FR24_API_KEY=${FR24_KEY}
TOMORROW_API_KEY=${TOMORROW_KEY}
EOF
    chown root:root "$ENV_DEST"
    chmod 0600 "$ENV_DEST"
    echo "  → Saved to $ENV_DEST (mode 0600)"
else
    echo "==> $ENV_DEST already exists, keeping existing keys"
fi

echo ""

# --- Step 4: Install and enable systemd service ---
echo "==> Installing systemd service..."
sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/its-a-plane-python/setup/plane-tracker.service" > /etc/systemd/system/plane-tracker.service
chmod 0644 /etc/systemd/system/plane-tracker.service
systemctl daemon-reload
systemctl enable plane-tracker.service

echo ""
echo "============================================"
echo "  Done! Your Pi is now using your fork."
echo "============================================"
echo ""
echo "  Start:    sudo systemctl start plane-tracker"
echo "  Status:   sudo systemctl status plane-tracker"
echo "  Logs:     sudo journalctl -u plane-tracker -f"
echo "  Edit keys: sudo nano /etc/plane-tracker.env"
echo ""
echo "  To update in future: cd $REPO_DIR && git pull"
echo ""
