#!/usr/bin/env bash
# Whatcher — update bot on a running VPS.
# Usage (as root): bash deploy/update.sh
set -euo pipefail

INSTALL_DIR="/opt/whatcher"

die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this script as root (sudo bash deploy/update.sh)"
[ -d "$INSTALL_DIR/.git" ] || die "Repository not found at $INSTALL_DIR. Run setup.sh first."

echo "── Pulling latest changes ──"
git -C "$INSTALL_DIR" pull origin master

echo "── Installing Python dependencies ──"
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo "── Restarting service ──"
systemctl restart whatcher

echo "── Verifying service is running ──"
sleep 3
if ! systemctl is-active --quiet whatcher; then
    echo "ERROR: whatcher failed to start after update!"
    journalctl -u whatcher --no-pager -n 30
    exit 1
fi
systemctl status whatcher --no-pager

echo
echo "Whatcher updated and running successfully."
