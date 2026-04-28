#!/usr/bin/env bash
# Whatcher — update bot on a running VPS.
# Usage (as root): bash deploy/update.sh
set -euo pipefail

INSTALL_DIR="/opt/whatcher"

die() { echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run this script as root (sudo bash deploy/update.sh)"
[ -d "$INSTALL_DIR/.git" ] || die "Repository not found at $INSTALL_DIR. Run setup.sh first."

echo "── Pulling latest changes ──"
git -C "$INSTALL_DIR" pull origin main

echo "── Installing Python dependencies ──"
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo "── Restarting service ──"
systemctl restart whatcher
sleep 2
systemctl status whatcher --no-pager

echo
echo "Whatcher updated and restarted successfully."
