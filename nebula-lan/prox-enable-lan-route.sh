#!/usr/bin/env bash
# prox-enable-lan-route.sh — make prox (10.42.0.10) the Nebula gateway for the house LAN 192.168.10.0/24.
#
#   bash prox-enable-lan-route.sh root@192.168.10.2     run it over ssh from the LAN (copies the new cert+key, then applies)
#   bash prox-enable-lan-route.sh --print            print a self-contained script to PASTE into the Proxmox web shell
#                                                    (https://10.42.0.10:8006 → prox → Shell) when ssh to prox is not reachable
#
# What it does on prox (idempotent, re-runnable):
#   1. installs the re-signed host cert/key from ~/.config/nebula-ca/prox.{crt,key} — the cert now carries
#      "subnets: 192.168.10.0/24", which Nebula requires before any peer will accept an unsafe_route via this host
#   2. net.ipv4.ip_forward=1 (persisted in /etc/sysctl.d/92-nebula-lan-route.conf)
#   3. nebula-lan-nat.service: MASQUERADE 10.42.0.0/24 -> LAN interface + FORWARD accept rules, so LAN devices need no
#      return route (they just see prox's own LAN address)
#   4. restarts nebula so the new cert is used
# Afterwards, laptop-add-lan-route.sh adds the matching unsafe_route on the laptop.
set -euo pipefail
CA="$HOME/.config/nebula-ca"
[ -f "$CA/prox.crt" ] && [ -f "$CA/prox.key" ] || { echo "missing $CA/prox.crt|key"; exit 1; }
grep -q "NEBULA CERTIFICATE" "$CA/prox.crt" || { echo "prox.crt does not look like a nebula cert"; exit 1; }

REMOTE=$(cat <<'REMOTE_EOF'
set -euo pipefail
echo "== installing cert/key"
# D = a root-only staging dir (mktemp -d under /root), set by the caller; never fixed /tmp names
[ -n "${D:-}" ] && [ -s "$D/nebula-prox.crt" ] && [ -s "$D/nebula-prox.key" ] || { echo "cert/key not staged in \$D"; exit 1; }
cp -p /etc/nebula/host.crt /etc/nebula/host.crt.bak-$(date +%Y%m%d) 2>/dev/null || true
cp -p /etc/nebula/host.key /etc/nebula/host.key.bak-$(date +%Y%m%d) 2>/dev/null || true
install -m 644 "$D/nebula-prox.crt" /etc/nebula/host.crt
install -m 600 "$D/nebula-prox.key" /etc/nebula/host.key
rm -rf "$D"
/usr/local/bin/nebula -config /etc/nebula/config.yml -test && echo "nebula config ok"

echo "== firewall: let admins reach the LAN behind this node (nebula >=1.9 rules only match the node's own overlay IP unless local_cidr is set)"
cp -p /etc/nebula/config.yml /etc/nebula/config.yml.bak-$(date +%Y%m%d) 2>/dev/null || true
grep -q "local_cidr: 192.168.10.0/24" /etc/nebula/config.yml || sed -i "/groups: \[admins\]}/a\\    - {port: any, proto: any, group: admins, local_cidr: 192.168.10.0/24}" /etc/nebula/config.yml
/usr/local/bin/nebula -config /etc/nebula/config.yml -test >/dev/null && echo "nebula config ok (firewall rule present)"

echo "== ip forwarding"
printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/92-nebula-lan-route.conf
sysctl -q -p /etc/sysctl.d/92-nebula-lan-route.conf

echo "== NAT + forward rules (nebula-lan-nat.service)"
cat > /usr/local/sbin/nebula-lan-nat.sh <<'X'
#!/bin/bash
# Overlay 10.42.0.0/24 -> house LAN via this host. Idempotent; safe to re-run. Installed by prox-enable-lan-route.sh.
set -e
LAN_IF=$(ip -o route get 192.168.10.1 | sed -n 's/.* dev \([^ ]*\).*/\1/p')
[ -n "$LAN_IF" ] || { echo "no route to 192.168.10.0/24"; exit 1; }
add() { local t="$1"; shift; iptables -t "$t" -C "$@" 2>/dev/null || iptables -t "$t" -I "$@"; }
add nat    POSTROUTING -s 10.42.0.0/24 -o "$LAN_IF" -j MASQUERADE
add filter FORWARD -i nebula1 -o "$LAN_IF" -j ACCEPT
add filter FORWARD -i "$LAN_IF" -o nebula1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
echo "nebula-lan-nat: overlay -> $LAN_IF ok"
X
chmod 755 /usr/local/sbin/nebula-lan-nat.sh
cat > /etc/systemd/system/nebula-lan-nat.service <<'X'
[Unit]
Description=NAT the Nebula overlay onto the house LAN (unsafe_routes gateway)
After=network-online.target nebula.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/nebula-lan-nat.sh
[Install]
WantedBy=multi-user.target
X
systemctl daemon-reload
systemctl enable --now nebula-lan-nat.service
systemctl is-active nebula-lan-nat
iptables -t nat -S POSTROUTING | grep 10.42.0.0 || true
# restart nebula DETACHED: when this runs over the overlay the restart drops the ssh session, so do it 2 s from now
systemd-run --quiet --collect --on-active=2 --unit nebula-lan-restart systemctl restart nebula
echo "== done: nebula restarts in 2 s with the new cert; prox then routes 192.168.10.0/24 for the overlay"
echo "   verify: ssh root@10.42.0.10 'systemctl is-active nebula; nebula-cert print -path /etc/nebula/host.crt | grep -A1 subnets'"
REMOTE_EOF
)

if [ "${1:-}" = "--print" ]; then
  cat <<EOF
# ---- paste everything below into the Proxmox shell on prox (as root) ----
D=\$(umask 077; mktemp -d /root/nebula-prox.XXXXXX)
cat > "\$D/nebula-prox.crt" <<'CRT'
$(cat "$CA/prox.crt")
CRT
cat > "\$D/nebula-prox.key" <<'KEY'
$(cat "$CA/prox.key")
KEY
$REMOTE
# ---- end ----
EOF
  exit 0
fi

TARGET="${1:?usage: $0 root@192.168.10.2 | --print}"
R="$(ssh "$TARGET" 'umask 077; mktemp -d /root/nebula-prox.XXXXXX')"; [ -n "$R" ] || { echo "no staging dir on $TARGET"; exit 1; }
scp -q "$CA/prox.crt" "$TARGET:$R/nebula-prox.crt"
scp -q "$CA/prox.key" "$TARGET:$R/nebula-prox.key"
{ printf 'D=%q\n' "$R"; printf '%s\n' "$REMOTE"; } | ssh "$TARGET" "bash -s"
