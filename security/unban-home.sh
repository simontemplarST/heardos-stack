#!/usr/bin/env bash
# unban-home.sh — run ON THE LAPTOP while it is on a DIFFERENT connection (phone hotspot), 2026-10-18.
# The lighthouse stopped answering the home IP (198.51.100.23): every public site 502s because the home server's Nebula
# link comes from that IP too. This script shows why (CrowdSec decisions, nftables sets), removes the ban, and allowlists
# the home IP in CrowdSec so it can't happen again. Usage: unban-home.sh [HOME_IP]   (default 198.51.100.23)
set -euo pipefail
IP="${1:-198.51.100.23}"
echo "== waiting for the overlay (laptop must NOT be on the home network)"
for i in $(seq 1 20); do ping -c1 -W2 10.42.0.1 >/dev/null 2>&1 && break; sleep 3; done
ping -c1 -W2 10.42.0.1 >/dev/null || { echo "lighthouse 10.42.0.1 not reachable over Nebula — is the laptop on the hotspot, and is nebula running?"; exit 1; }
ssh -o ConnectTimeout=10 nebula-lh bash -s -- "$IP" <<'REMOTE'
set -u
IP="$1"
echo "== CrowdSec decisions for $IP"
cscli decisions list -i "$IP" 2>&1 | head -20
echo "== alerts for $IP (why)"
cscli alerts list -i "$IP" 2>&1 | head -12
echo "== nftables sets containing $IP"
nft list ruleset 2>/dev/null | grep -n "$IP" | head -5 || true
echo "== removing decisions"
cscli decisions delete --ip "$IP" 2>&1 | tail -2
echo "== allowlisting $IP"
cat > /etc/crowdsec/parsers/s02-enrich/heardos-home-whitelist.yaml <<EOF
# HeardOS (2026-10-18): the home connection must never be banned — the home server's Nebula link and every admin
# check comes from it. If the home IP changes, update this file and run: systemctl reload crowdsec
name: heardos/home-whitelist
description: "Never ban the HeardOS home connection"
whitelist:
  reason: "HeardOS home connection"
  ip:
    - "$IP"
EOF
systemctl reload crowdsec 2>/dev/null || systemctl restart crowdsec
sleep 3
echo "== after"
cscli decisions list -i "$IP" 2>&1 | head -5
nft list ruleset 2>/dev/null | grep -c "$IP" | sed 's/^/nft entries still mentioning the IP: /'
cscli parsers list 2>/dev/null | grep -i heardos || true
REMOTE
echo
echo "Done. Switch the laptop back to home Wi-Fi; the home server's Nebula link reconnects on its own within a minute."
echo "Then check: curl -s -o /dev/null -w '%{http_code}\n' https://auth.example.com/"
