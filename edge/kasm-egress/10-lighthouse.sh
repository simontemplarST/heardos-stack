#!/usr/bin/env bash
# Run ON the lighthouse (nebula-lh) as root:  bash 10-lighthouse.sh /root/<private dir>/lighthouse.crt.new
# (stage the re-signed cert in a root-only dir, e.g. d=$(mktemp -d /root/egress.XXXXXX); never a fixed /tmp name).
# Lets ONLY the Nebula host "kasm" (10.42.0.12) use the lighthouse as its internet exit: cert unsafe-networks 0.0.0.0/0,
# nebula inbound rules host kasm for every destination EXCEPT the overlay 10.42.0.0/24 (the lighthouse's own overlay
# services: sshd, nebula peers) and 169.254.0.0/16 (cloud metadata), ip_forward, ufw route rule, MASQUERADE out eth0.
# Nebula's local_cidr takes one CIDR and has no "all but", so the exclusion is spelled as the covering set of CIDRs.
# Arms a 30-min rollback (disarm: systemctl stop egress-rollback.timer).
set -euo pipefail
CRT="${1:?usage: 10-lighthouse.sh /root/<private dir>/lighthouse.crt.new}"
[ -f "$CRT" ] && [ ! -L "$CRT" ] && [ "$(stat -c %u "$CRT")" = 0 ] || { echo "$CRT must be a regular root-owned file" >&2; exit 1; }
T=20261017-egress
cp -a /etc/nebula/config.yml /etc/nebula/config.yml.bak-$T; cp -a /etc/nebula/host.crt /etc/nebula/host.crt.bak-$T
cat > /root/egress-rollback.sh <<X
#!/bin/sh
cp -a /etc/nebula/config.yml.bak-$T /etc/nebula/config.yml; cp -a /etc/nebula/host.crt.bak-$T /etc/nebula/host.crt
systemctl restart nebula
X
chmod 700 /root/egress-rollback.sh
[ "${NO_ROLLBACK:-}" = 1 ] || systemd-run --on-active=1800 --unit=egress-rollback /root/egress-rollback.sh >/dev/null
install -m 644 "$CRT" /etc/nebula/host.crt
# replace any earlier "host: kasm" rules (incl. the old local_cidr 0.0.0.0/0 one) with the overlay-excluding set
python3 - /etc/nebula/config.yml <<'PY'
import ipaddress, sys
p = sys.argv[1]; lines = [l for l in open(p).read().split("\n") if "host: kasm" not in l]
nets = [ipaddress.ip_network("0.0.0.0/0")]
for ex in map(ipaddress.ip_network, ("10.42.0.0/24", "169.254.0.0/16")):
    nets = [y for x in nets for y in (x.address_exclude(ex) if ex.subnet_of(x) else [x])]
i = next(n for n, l in enumerate(lines) if l.startswith("    - {port: 22, proto: tcp, group: admins}"))
lines[i + 1:i + 1] = [f"    - {{port: any, proto: any, host: kasm, local_cidr: {n}}}   # Kasm internet egress (not the overlay)" for n in sorted(nets)]
open(p, "w").write("\n".join(lines))
PY
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
