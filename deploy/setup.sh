#!/usr/bin/env bash
# Whatcher — first-time setup on a clean Ubuntu VPS.
# Usage (as root): bash deploy/setup.sh
set -euo pipefail

REPO_URL="https://github.com/YOUR_USERNAME/whatcher.git"
INSTALL_DIR="/opt/whatcher"
SERVICE_USER="whatcher"
PYTHON="python3"

# ── helpers ──────────────────────────────────────────────────────────
step() { echo; echo "── [step $1/12] $2 ──"; }
die()  { echo "ERROR: $*" >&2; exit 1; }

# ── checks ───────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "Run this script as root (sudo bash deploy/setup.sh)"

step 1 "Update package index"
apt-get update -qq

step 2 "Install system dependencies"
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git curl ca-certificates

step 3 "Create system user '$SERVICE_USER'"
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "User '$SERVICE_USER' created."
else
    echo "User '$SERVICE_USER' already exists — skipping."
fi

step 4 "Clone repository into $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Repository already cloned — skipping."
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

step 5 "Create Python virtual environment"
$PYTHON -m venv "$INSTALL_DIR/venv"

step 6 "Install Python dependencies"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

step 7 "Install Playwright browser (Chromium)"
"$INSTALL_DIR/venv/bin/playwright" install chromium --with-deps

step 8 "Create .env from .env.example"
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "Created $INSTALL_DIR/.env — EDIT THIS FILE before starting the bot!"
else
    echo ".env already exists — skipping."
fi

step 9 "Create data and logs directories"
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs"

step 10 "Set file ownership"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

step 11 "Install and enable systemd service"
cp "$INSTALL_DIR/deploy/whatcher.service" /etc/systemd/system/whatcher.service
systemctl daemon-reload
systemctl enable whatcher

step 12 "Start service"
systemctl start whatcher
sleep 2
systemctl status whatcher --no-pager

echo
echo "════════════════════════════════════════════════════"
echo " Whatcher installed successfully!"
echo
echo " Next steps:"
echo "   1. Fill in BOT_TOKEN and GROUP_CHAT_ID in $INSTALL_DIR/.env"
echo "   2. Restart the bot:  systemctl restart whatcher"
echo "   3. View logs:        journalctl -u whatcher -f"
echo "════════════════════════════════════════════════════"
