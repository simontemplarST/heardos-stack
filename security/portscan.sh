#!/usr/bin/env bash
# portscan.sh — external exposure check of the lighthouse (the only internet-facing host).
#   bash portscan.sh            quick: top TCP ports + service versions (laptop, no root needed; nmap via Homebrew)
#   bash portscan.sh --full     all 65535 TCP (connect scan) — ~5 min
#   bash portscan.sh --home     run the root-level SYN + UDP scans from CT 108 (home connection) instead
# Expected result (baseline 2026-10-15): TCP 80, 443 open; everything else filtered; UDP 4242 (Nebula) and 443 (HTTP/3)
# open|filtered (they never answer random probes). tcp/udp 53 showing "open" is DNS INTERCEPTION by the network you scan
# from — verify by probing an unrelated IP on 53: if that "succeeds" too, it is the network, not the VPS.
set -uo pipefail
T=203.0.113.10
case "${1:-}" in
  --full) python3 "$(dirname "$0")/tcpscan.py" "$T" 500 2.5 ;;
  --home) ssh -o BatchMode=yes root@192.168.10.20 "command -v nmap >/dev/null || apt-get install -y -qq nmap >/dev/null; nmap -Pn -sS -p- --min-rate 2500 --max-retries 2 $T | grep -E '^[0-9]+/|Not shown'; nmap -Pn -sU --top-ports 300 --max-retries 1 --host-timeout 8m $T | grep -E '^[0-9]+/|Not shown'; printf 'interception control 203.0.113.20:53 -> '; timeout 5 nc -zv -w 3 203.0.113.20 53 2>&1 | tail -1" ;;
  *) nmap -Pn -sT -sV --top-ports 1000 "$T" | grep -E "^[0-9]+/|Not shown"; printf 'interception control 203.0.113.20:53 -> '; timeout 5 nc -zv -w 3 203.0.113.20 53 2>&1 | tail -1 ;;
esac
