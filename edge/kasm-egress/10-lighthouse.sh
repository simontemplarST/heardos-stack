#!/usr/bin/env bash
# Run ON the lighthouse (nebula-lh) as root, with /tmp/lighthouse.crt.new staged. Lets ONLY the Nebula host "kasm"
# (10.42.0.12) use the lighthouse as its internet exit: cert unsafe-networks 0.0.0.0/0, nebula inbound rule host kasm
# local_cidr 0.0.0.0/0, ip_forward, ufw route rule, MASQUERADE out eth0. Arms a 30-min rollback (disarm: systemctl stop egress-rollback.timer).
set -euo pipefail
T=20261017-egress
cp -a /etc/nebula/config.yml /etc/nebula/config.yml.bak-$T; cp -a /etc/nebula/host.crt /etc/nebula/host.crt.bak-$T
cat > /root/egress-rollback.sh <<X
#!/bin/sh
cp -a /etc/nebula/config.yml.bak-$T /etc/nebula/config.yml; cp -a /etc/nebula/host.crt.bak-$T /etc/nebula/host.crt
systemctl restart nebula
X
chmod 700 /root/egress-rollback.sh
[ "${NO_ROLLBACK:-}" = 1 ] || systemd-run --on-active=1800 --unit=egress-rollback /root/egress-rollback.sh >/dev/null
install -m 644 /tmp/lighthouse.crt.new /etc/nebula/host.crt
grep -q 'host: kasm, local_cidr: 0.0.0.0/0' /etc/nebula/config.yml || \
  sed -i 's|^    - {port: 22, proto: tcp, group: admins}.*|&\n    - {port: any, proto: any, host: kasm, local_cidr: 0.0.0.0/0}   # Kasm VM 110 internet egress via this host (2026-10-17)|' /etc/nebula/config.yml
/usr/local/bin/nebula -config /etc/nebula/config.yml -test >/dev/null && echo nebula-config-ok
printf '# Kasm VM 110 internet egress through the lighthouse (2026-10-17)\nnet.ipv4.ip_forward = 1\n' > /etc/sysctl.d/93-nebula-egress.conf
sysctl -q -p /etc/sysctl.d/93-nebula-egress.conf
ufw route allow in on nebula1 out on eth0 from 10.42.0.12 comment 'kasm egress' >/dev/null
cat > /usr/local/sbin/nebula-egress-nat.sh <<'X'
#!/bin/sh
# MASQUERADE Kasm's (10.42.0.12) internet traffic out the public interface
iptables -t nat -C POSTROUTING -s 10.42.0.12/32 -o eth0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -s 10.42.0.12/32 -o eth0 -j MASQUERADE
iptables -t mangle -C FORWARD -i nebula1 -o eth0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || iptables -t mangle -A FORWARD -i nebula1 -o eth0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
iptables -t mangle -C FORWARD -i eth0 -o nebula1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || iptables -t mangle -A FORWARD -i eth0 -o nebula1 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
X
chmod 755 /usr/local/sbin/nebula-egress-nat.sh
cat > /etc/systemd/system/nebula-egress-nat.service <<'X'
[Unit]
Description=NAT for Kasm internet egress over Nebula (10.42.0.12 -> eth0)
After=network-online.target ufw.service nebula.service
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/nebula-egress-nat.sh
[Install]
WantedBy=multi-user.target
X
systemctl daemon-reload; systemctl enable --now nebula-egress-nat.service >/dev/null
systemctl reload nebula; sleep 3; systemctl is-active nebula nebula-egress-nat | paste -sd' '
