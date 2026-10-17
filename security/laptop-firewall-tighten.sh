#!/usr/bin/env bash
# Run on the laptop with sudo. Tightens firewalld zone FedoraWorkstation (wlp5s0 + anything unassigned, incl. nebula1):
# removes the Fedora default 1025-65535/tcp+udp ranges, ssh (sshd not running), samba/samba-client; keeps what is used:
# kdeconnect (1714-1764), mdns, dhcpv6-client. Nebula/Tailscale replies still work (firewalld is stateful).
#   sudo bash laptop-firewall-tighten.sh          apply (runtime + permanent)
#   sudo bash laptop-firewall-tighten.sh --undo   restore the Fedora Workstation defaults
set -euo pipefail
Z=FedoraWorkstation
if [ "${1:-}" = --undo ]; then
  firewall-cmd --permanent --load-zone-defaults=$Z && firewall-cmd --reload
  firewall-cmd --zone=$Z --list-all; exit 0
fi
cp -a /etc/firewalld/zones/$Z.xml /etc/firewalld/zones/$Z.xml.bak-20261017 2>/dev/null || true
for p in 1025-65535/tcp 1025-65535/udp; do firewall-cmd --permanent --zone=$Z --remove-port=$p >/dev/null 2>&1 || true; done
for s in ssh samba samba-client; do firewall-cmd --permanent --zone=$Z --remove-service=$s >/dev/null 2>&1 || true; done
for s in kdeconnect mdns dhcpv6-client; do firewall-cmd --permanent --zone=$Z --add-service=$s >/dev/null; done
firewall-cmd --reload >/dev/null
firewall-cmd --zone=$Z --list-all | grep -E "interfaces|services|ports"
