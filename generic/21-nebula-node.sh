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
cp "$HERE/lib/nebula-install.sh" "$out/install.sh"
cp "$bin" "$out/nebula"

if [ "$target" = "--local" ]; then
  msg "installing bundle '$name' locally (sudo)"; sudo bash "$out/install.sh"
else
  msg "installing bundle '$name' on $target"
  tar -C "$out" -cf - nebula ca.crt host.crt host.key config.yml nebula.service install.sh | ssh -o BatchMode=yes "$target" \
    'd=$(umask 077; mktemp -d /root/nebula-stage.XXXXXX) && tar -C "$d" -xf - && bash "$d/install.sh"; rc=$?; rm -rf "$d"; exit $rc'
fi
rm -f "$out/nebula"
msg "verify: ping $NEBULA_LH_OVERLAY from the node; 'journalctl -u nebula' on the lighthouse shows its handshake"
