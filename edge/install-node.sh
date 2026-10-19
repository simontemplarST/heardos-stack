#!/bin/bash
# usage: install-node.sh DIR  (DIR holds nebula binary, ca.crt, host.crt, host.key, config.yml, nebula.service; make it
# root-only, e.g. D=$(umask 077; mktemp -d /root/nebula-stage.XXXXXX) — never a fixed /tmp name)
set -euo pipefail; D="${1:?usage: install-node.sh STAGING_DIR}"
[ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c %u "$D")" = 0 ] || { echo "$D must be a root-owned directory" >&2; exit 1; }
install -m 755 "$D"/nebula /usr/local/bin/nebula
install -d -m 700 /etc/nebula
install -m 644 "$D"/ca.crt /etc/nebula/ca.crt; install -m 644 "$D"/host.crt /etc/nebula/host.crt; install -m 600 "$D"/host.key /etc/nebula/host.key
install -m 644 "$D"/config.yml /etc/nebula/config.yml; install -m 644 "$D"/nebula.service /etc/systemd/system/nebula.service
printf 'net.core.rmem_max=10485760\nnet.core.wmem_max=10485760\n' > /etc/sysctl.d/90-nebula.conf; sysctl -q -p /etc/sysctl.d/90-nebula.conf
/usr/local/bin/nebula -config /etc/nebula/config.yml -test && echo "config ok"
systemctl daemon-reload; systemctl enable --now nebula; sleep 2; systemctl is-active nebula; ip -4 -br addr show nebula1
rm -rf "$D"
