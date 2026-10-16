#!/usr/bin/env bash
# nebula-install.sh — runs ON THE TARGET as root from a staging dir holding nebula, ca.crt, host.crt, host.key, config.yml,
# nebula.service (copied there by 21-nebula-node.sh or the HeardOS installer). Idempotent.
set -euo pipefail; D="$(cd "$(dirname "$0")" && pwd)"
install -m 755 "$D/nebula" /usr/local/bin/nebula
install -d -m 700 /etc/nebula
install -m 644 "$D/ca.crt" /etc/nebula/ca.crt; install -m 644 "$D/host.crt" /etc/nebula/host.crt; install -m 600 "$D/host.key" /etc/nebula/host.key
install -m 644 "$D/config.yml" /etc/nebula/config.yml; install -m 644 "$D/nebula.service" /etc/systemd/system/nebula.service
mkdir -p /etc/systemd/system/nebula.service.d; printf '[Service]\nNice=-10\nCPUWeight=1000\n' > /etc/systemd/system/nebula.service.d/10-priority.conf   # tunnel stays responsive under host load
printf 'net.core.rmem_max=10485760\nnet.core.wmem_max=10485760\n' > /etc/sysctl.d/90-nebula.conf; sysctl -q -p /etc/sysctl.d/90-nebula.conf || true
/usr/local/bin/nebula -config /etc/nebula/config.yml -test >/dev/null && echo "config ok"
systemctl daemon-reload; systemctl enable --now nebula >/dev/null; systemctl restart nebula; sleep 2
systemctl is-active nebula; ip -4 -br addr show nebula1
