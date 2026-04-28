#!/usr/bin/env bash
# Whatcher — AmneziaVPN compatibility diagnostic.
# Run once on the VPS after first deploy to confirm the bot can reach Telegram.
# Usage: bash deploy/check-vpn.sh
set -uo pipefail

OK="✅"
FAIL="❌"
WARN="⚠️ "
issues=0

echo "═══════════════════════════════════════════════════"
echo "  Whatcher — VPN Compatibility Check"
echo "═══════════════════════════════════════════════════"
echo

# ── 1. Detect VPN interfaces ─────────────────────────────────────────
echo "── [1/5] VPN interfaces ──"
vpn_ifaces=$(ip link show 2>/dev/null | grep -oE '(awg|wg|tun|tap)[0-9]+' | head -5 || true)
if [ -n "$vpn_ifaces" ]; then
    echo "$OK VPN interface(s) found: $vpn_ifaces"
    echo "   (AmneziaVPN server is active — expected)"
else
    echo "$WARN No VPN interface detected (awg0/wg0/tun0). Is AmneziaVPN running?"
fi
echo

# ── 2. Default route (bot traffic path) ──────────────────────────────
echo "── [2/5] Default route (bot traffic) ──"
default_route=$(ip route show default 2>/dev/null | head -1)
if [ -z "$default_route" ]; then
    echo "$FAIL No default route found!"
    issues=$((issues + 1))
else
    echo "$OK Default route: $default_route"
    # Warn if default route goes through a VPN interface
    if echo "$default_route" | grep -qE 'dev (awg|wg|tun|tap)'; then
        echo "$WARN Default route goes through VPN interface!"
        echo "   Bot outbound traffic will pass through VPN tunnel."
        echo "   This may cause issues if VPN exit IP is blocked by WB/Ozon."
        issues=$((issues + 1))
    else
        echo "$OK Bot traffic uses main internet interface (not VPN) — correct."
    fi
fi
echo

# ── 3. Telegram API reachability (as root) ───────────────────────────
echo "── [3/5] Telegram API reachability ──"
if curl -s --max-time 8 --fail -o /dev/null https://api.telegram.org; then
    echo "$OK api.telegram.org reachable"
else
    echo "$FAIL api.telegram.org NOT reachable — bot cannot send messages!"
    issues=$((issues + 1))
fi
echo

# ── 4. Telegram API reachability (as whatcher user) ──────────────────
echo "── [4/5] Telegram API reachability as 'whatcher' user ──"
if id whatcher &>/dev/null; then
    if sudo -u whatcher curl -s --max-time 8 --fail -o /dev/null https://api.telegram.org; then
        echo "$OK api.telegram.org reachable as whatcher"
    else
        echo "$FAIL api.telegram.org NOT reachable as whatcher — check network permissions!"
        issues=$((issues + 1))
    fi
else
    echo "$WARN User 'whatcher' not found — run setup.sh first."
fi
echo

# ── 5. Port conflicts ─────────────────────────────────────────────────
echo "── [5/5] Port conflicts ──"
# Bot uses outbound HTTPS only (no listening ports)
# VPN typically listens on UDP 51820 (WireGuard) or custom port
vpn_ports=$(ss -ulpn 2>/dev/null | grep -E ':(51820|4433|8443|1194)\s' || true)
if [ -n "$vpn_ports" ]; then
    echo "$OK VPN server ports detected (expected):"
    echo "$vpn_ports" | sed 's/^/   /'
else
    echo "$OK No well-known VPN port conflicts (bot uses only outbound HTTPS)"
fi
echo

# ── Summary ───────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════"
if [ "$issues" -eq 0 ]; then
    echo "$OK All checks passed. Bot is compatible with AmneziaVPN."
else
    echo "$FAIL $issues issue(s) found. Review warnings above before starting the bot."
fi
echo "═══════════════════════════════════════════════════"
