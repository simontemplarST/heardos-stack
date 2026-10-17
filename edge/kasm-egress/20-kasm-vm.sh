#!/usr/bin/env bash
# Run ON Kasm VM 110 as root, with /tmp/kasm-nebula/{nebula,ca.crt,host.crt,host.key,nebula.service} staged.
# All internet-bound traffic leaves via the Nebula lighthouse (10.42.0.1 / 203.0.113.10):
#  - nebula node "kasm" 10.42.0.12, unsafe_routes 0.0.0.0/1 + 128.0.0.0/1 via 10.42.0.1 (beats the DHCP default route)
#  - nebula's own UDP is marked (listen.so_mark 0x4242) and routed via table 200 (LAN gateway) so the tunnel can't loop
#  - kill switch: anything else leaving eth0 for a non-LAN address is rejected (host + docker/Kasm sessions, IPv4 + IPv6)
#  - DNS 1.1.1.1 / 9.9.9.9 (through the tunnel) instead of the home router
#   bash 20-kasm-vm.sh          install / re-apply
#   bash 20-kasm-vm.sh --undo   remove routing, kill switch, DNS override and nebula
set -euo pipefail
S=/tmp/kasm-nebula
if [ "${1:-}" = --undo ]; then
  systemctl disable --now kasm-egress.service nebula 2>/dev/null || true
  /usr/local/sbin/kasm-egress.sh down 2>/dev/null || true
  rm -f /etc/systemd/system/kasm-egress.service /usr/local/sbin/kasm-egress.sh /etc/netplan/90-kasm-egress-dns.yaml
  systemctl daemon-reload; netplan apply; exit 0
fi
install -m 755 $S/nebula /usr/local/bin/nebula
install -d -m 700 /etc/nebula
install -m 644 $S/ca.crt /etc/nebula/ca.crt; install -m 644 $S/host.crt /etc/nebula/host.crt; install -m 600 $S/host.key /etc/nebula/host.key
cat > /etc/nebula/config.yml <<'X'
# Nebula node "kasm" (Kasm VM 110) — full-tunnel internet egress via the lighthouse (2026-10-17)
pki:
  ca: /etc/nebula/ca.crt
  cert: /etc/nebula/host.crt
  key: /etc/nebula/host.key
static_host_map:
  "10.42.0.1": ["203.0.113.10:4242"]
lighthouse:
  am_lighthouse: false
  interval: 60
  hosts: ["10.42.0.1"]
listen:
  host: "0.0.0.0"
  port: 0
  read_buffer: 10485760
  write_buffer: 10485760
  batch: 64
  so_mark: 16962          # 0x4242: kasm-egress routes marked packets via the LAN (table 200), not into the tunnel
punchy:
  punch: true
  respond: true
relay:
  relays: ["10.42.0.1"]
  am_relay: false
  use_relays: true
tun:
  disabled: false
  dev: nebula1
  mtu: 1300
  tx_queue: 1000
  unsafe_routes:
    - {route: 0.0.0.0/1, via: 10.42.0.1, mtu: 1300}
    - {route: 128.0.0.0/1, via: 10.42.0.1, mtu: 1300}
logging:
  level: info
  format: text
firewall:
  outbound_action: drop
  inbound_action: drop
  conntrack: {tcp_timeout: 12m, udp_timeout: 3m, default_timeout: 10m}
  outbound:
    - {port: any, proto: any, host: any}
  inbound:
    - {port: any, proto: icmp, host: any}
    - {port: any, proto: any, group: admins}
routines: 1
X
install -m 644 $S/nebula.service /etc/systemd/system/nebula.service
printf 'net.core.rmem_max=10485760\nnet.core.wmem_max=10485760\n' > /etc/sysctl.d/90-nebula.conf; sysctl -q -p /etc/sysctl.d/90-nebula.conf
/usr/local/bin/nebula -config /etc/nebula/config.yml -test >/dev/null && echo nebula-config-ok

cat > /usr/local/sbin/kasm-egress.sh <<'X'
#!/bin/sh
# up: policy routing for nebula's marked UDP + kill switch + MSS clamp. down: remove them. Idempotent.
LAN=192.168.10.0/24; GW=192.168.10.1; V6LAN=fd00:0:0:1::/64
ipt() { iptables "$@"; }; ip6t() { ip6tables "$@"; }
if [ "$1" = down ]; then
  ip rule del fwmark 0x4242 lookup 200 2>/dev/null; ip route flush table 200 2>/dev/null
  for c in OUTPUT DOCKER-USER; do ipt -D $c -j KASM-EGRESS 2>/dev/null; ip6t -D $c -j KASM-EGRESS 2>/dev/null; done
  ipt -F KASM-EGRESS 2>/dev/null; ipt -X KASM-EGRESS 2>/dev/null; ip6t -F KASM-EGRESS 2>/dev/null; ip6t -X KASM-EGRESS 2>/dev/null
  ipt -t mangle -D FORWARD -o nebula1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null
  exit 0
fi
ip route replace $LAN dev eth0 table 200
ip route replace default via $GW dev eth0 table 200
ip rule show | grep -q "fwmark 0x4242 lookup 200" || ip rule add fwmark 0x4242 lookup 200 priority 100
# kill switch (IPv4): on eth0 only LAN destinations, DHCP, and nebula's own marked packets may leave
ipt -N KASM-EGRESS 2>/dev/null; ipt -F KASM-EGRESS
ipt -A KASM-EGRESS ! -o eth0 -j RETURN
ipt -A KASM-EGRESS -d $LAN -j RETURN
ipt -A KASM-EGRESS -d 255.255.255.255 -p udp --dport 67 -j RETURN
ipt -A KASM-EGRESS -m mark --mark 0x4242 -j RETURN
ipt -A KASM-EGRESS -j REJECT --reject-with icmp-net-unreachable
# kill switch (IPv6): only link-local, the LAN ULA and multicast on eth0
ip6t -N KASM-EGRESS 2>/dev/null; ip6t -F KASM-EGRESS
ip6t -A KASM-EGRESS ! -o eth0 -j RETURN
ip6t -A KASM-EGRESS -d fe80::/10 -j RETURN
ip6t -A KASM-EGRESS -d $V6LAN -j RETURN
ip6t -A KASM-EGRESS -d ff00::/8 -j RETURN
ip6t -A KASM-EGRESS -j REJECT
for c in OUTPUT DOCKER-USER; do
  ipt -N $c 2>/dev/null; ipt -C $c -j KASM-EGRESS 2>/dev/null || ipt -I $c 1 -j KASM-EGRESS
  ip6t -N $c 2>/dev/null; ip6t -C $c -j KASM-EGRESS 2>/dev/null || ip6t -I $c 1 -j KASM-EGRESS
done
ipt -t mangle -C FORWARD -o nebula1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
  ipt -t mangle -A FORWARD -o nebula1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
exit 0
X
chmod 755 /usr/local/sbin/kasm-egress.sh
cat > /etc/systemd/system/kasm-egress.service <<'X'
[Unit]
Description=Kasm internet egress via Nebula lighthouse: policy routing + kill switch
After=network-online.target docker.service
Wants=network-online.target
Before=nebula.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/kasm-egress.sh up
ExecStop=/usr/local/sbin/kasm-egress.sh down
[Install]
WantedBy=multi-user.target
X
# docker re-creates DOCKER-USER on restart but keeps its rules only if the chain exists; re-apply after docker restarts
mkdir -p /etc/systemd/system/docker.service.d
printf '[Service]\nExecStartPost=/usr/local/sbin/kasm-egress.sh up\n' > /etc/systemd/system/docker.service.d/50-kasm-egress.conf

# routes/rule also added by kasm-egress.sh at boot (before networkd settles); netplan owns them afterwards
cat > /etc/netplan/90-kasm-egress-dns.yaml <<'X'
# Kasm egress (2026-10-17): public resolvers, reached through the Nebula tunnel, instead of the home router
network:
  version: 2
  ethernets:
    eth0:
      match:
        macaddress: 52:54:00:00:00:01
      dhcp4-overrides:
        use-dns: false
      nameservers:
        addresses: [1.1.1.1, 9.9.9.9]
      # nebula's own marked UDP (so_mark 0x4242) leaves via the LAN gateway; declared here so networkd keeps it
      routes:
        - {to: default, via: 192.168.10.1, table: 200}
        - {to: 192.168.10.0/24, scope: link, table: 200}
      routing-policy:
        - {to: 0.0.0.0/0, mark: 16962, table: 200, priority: 100}
X
chmod 600 /etc/netplan/90-kasm-egress-dns.yaml

systemctl daemon-reload
systemctl enable --now kasm-egress.service
systemctl enable --now nebula
sleep 5
netplan apply
sleep 3
systemctl is-active kasm-egress nebula | paste -sd' '
ip -br addr show nebula1; ip route | grep -E '^(0|128)\.0\.0\.0/1'; ip rule | grep 0x4242
resolvectl dns eth0
