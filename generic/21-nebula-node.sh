#!/usr/bin/env bash
# 21-nebula-node.sh — run ON THE LAPTOP. Installs a node bundle made by 20-nebula-ca.sh onto a machine.
#   21-nebula-node.sh host root@192.168.10.50     install bundle "host" over ssh
#   21-nebula-node.sh laptop --local          install bundle "laptop" on this laptop (uses sudo)
# Idempotent: re-running replaces config/certs and restarts the service.
. "$(dirname "$0")/lib/common.sh"; load_env
name="${1:?bundle name}"; target="${2:?ssh target or --local}"
CA="$HOME/.config/nebula-ca"; out="$CA/out/$name"; bin="$(cat "$CA/.binary")"
[ -f "$out/config.yml" ] || die "no bundle for '$name' — run 20-nebula-ca.sh init or sign first"

cat > "$out/nebula.service" <<'EOF'
[Unit]
Description=Nebula overlay network (heardos)
Wants=basic.target network-online.target
After=basic.target network-online.target
[Service]
Type=notify
NotifyAccess=main
SyslogIdentifier=nebula
ExecReload=/bin/kill -HUP $MAINPID
ExecStart=/usr/local/bin/nebula -config /etc/nebula/config.yml
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
EOF
cat > "$out/install.sh" <<'EOF'
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
EOF
cp "$bin" "$out/nebula"

if [ "$target" = "--local" ]; then
  msg "installing bundle '$name' locally (sudo)"; sudo bash "$out/install.sh"
else
  msg "installing bundle '$name' on $target"
  tar -C "$out" -cf - nebula ca.crt host.crt host.key config.yml nebula.service install.sh | ssh -o BatchMode=yes "$target" 'rm -rf /tmp/nebula-stage && mkdir -p /tmp/nebula-stage && tar -C /tmp/nebula-stage -xf - && bash /tmp/nebula-stage/install.sh && rm -rf /tmp/nebula-stage'
fi
rm -f "$out/nebula"
msg "verify: ping $NEBULA_LH_OVERLAY from the node; 'journalctl -u nebula' on the lighthouse shows its handshake"
