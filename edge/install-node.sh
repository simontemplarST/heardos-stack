#!/bin/bash
# usage: install-node.sh  (expects nebula binary, ca.crt, host.crt, host.key, config.yml, nebula.service in /tmp/nebula-stage)
set -euo pipefail; D=/tmp/nebula-stage
install -m 755 $D/nebula /usr/local/bin/nebula
install -d -m 700 /etc/nebula
install -m 644 $D/ca.crt /etc/nebula/ca.crt; install -m 644 $D/host.crt /etc/nebula/host.crt; install -m 600 $D/host.key /etc/nebula/host.key
install -m 644 $D/config.yml /etc/nebula/config.yml; install -m 644 $D/nebula.service /etc/systemd/system/nebula.service
printf 'net.core.rmem_max=10485760\nnet.core.wmem_max=10485760\n' > /etc/sysctl.d/90-nebula.conf; sysctl -q -p /etc/sysctl.d/90-nebula.conf
/usr/local/bin/nebula -config /etc/nebula/config.yml -test && echo "config ok"
systemctl daemon-reload; systemctl enable --now nebula; sleep 2; systemctl is-active nebula; ip -4 -br addr show nebula1
rm -rf $D
