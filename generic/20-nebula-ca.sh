#!/usr/bin/env bash
# 20-nebula-ca.sh — run ON THE LAPTOP. Creates the Nebula CA once, signs host certificates, and renders a
# ready-to-install bundle per node under ~/.config/nebula-ca/out/<name>/ (config.yml, ca.crt, host.crt, host.key).
#
#   20-nebula-ca.sh init                      CA + bundles for lighthouse, host, laptop (from heardos.env)
#   20-nebula-ca.sh sign NAME IP GROUPS       e.g. sign phone-bob 10.42.0.31 admins
#   20-nebula-ca.sh backup                    encrypted copy of the CA (gpg symmetric) -> ./nebula-ca-YYYYMMDD.tgz.gpg
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?set NEBULA_LH_IP in the plan file before rendering node configs}"
CA="$HOME/.config/nebula-ca"; BIN="$HOME/.local/bin"; mkdir -p "$BIN" "$CA/out"; chmod 700 "$CA"

ensure_tools() {
  if [ ! -x "$BIN/nebula-cert" ] || [ ! -x "$BIN/nebula" ]; then
    local v; v="$(curl -fsS https://api.github.com/repos/slackhq/nebula/releases/latest | python3 -c 'import sys,json; print(json.load(sys.stdin)["tag_name"])')"
    msg "downloading nebula $v"
    curl -fsSL "https://github.com/slackhq/nebula/releases/download/$v/nebula-linux-amd64.tar.gz" | tar -xz -C "$BIN" nebula nebula-cert
  fi
  echo "$BIN/nebula" > "$CA/.binary"; "$BIN/nebula" -version
}

ensure_ca() {
  cd "$CA"
  [ -f ca.crt ] || { msg "creating CA 'heardos' (10 years)"; "$BIN/nebula-cert" ca -name heardos -duration 87600h; chmod 600 ca.key; }
}

sign() { # NAME IP GROUPS
  local name="$1" ip="$2" groups="$3"; cd "$CA"
  [ -f "$name.crt" ] || { msg "signing $name ($ip, groups $groups, 5 years)"; "$BIN/nebula-cert" sign -ca-crt ca.crt -ca-key ca.key -name "$name" -ip "$ip/24" -groups "$groups" -duration 43800h; chmod 600 "$name.key"; }
  local out="$CA/out/$name"; mkdir -p "$out"; cp ca.crt "$out/ca.crt"; cp "$name.crt" "$out/host.crt"; cp "$name.key" "$out/host.key"; chmod 600 "$out/host.key"
  export NODE_NAME="$name" OVERLAY_CIDR NEBULA_LH_IP NEBULA_LH_OVERLAY
  if [ "$name" = lighthouse ]; then render "$HERE/lib/nebula-lighthouse.yml.tmpl" "$out/config.yml"
  else
    # the compute host additionally accepts tcp/80 from the lighthouse: that is the public ingress hop
    if [ "$ip" = "$HOST_OVERLAY" ]; then export INGRESS_RULE='    - {port: 80, proto: tcp, group: lighthouse}' NEBULA_ROUTINES=1; else export INGRESS_RULE='' NEBULA_ROUTINES=1; fi
    render "$HERE/lib/nebula-node.yml.tmpl" "$out/config.yml"
  fi
  msg "bundle ready: $out"
}

case "${1:-}" in
  init)
    ensure_tools; ensure_ca
    sign lighthouse "$NEBULA_LH_OVERLAY" lighthouse
    sign host "$HOST_OVERLAY" servers
    sign laptop "$LAPTOP_OVERLAY" admins
    msg "next: 22-vps-lighthouse.sh (VPS), then 21-nebula-node.sh host root@$HOST_LAN_IP, then 21-nebula-node.sh laptop --local" ;;
  sign) ensure_tools; ensure_ca; sign "$2" "$3" "$4" ;;
  backup)
    need gpg; f="nebula-ca-$(date +%Y%m%d).tgz.gpg"
    tar -C "$HOME/.config" -czf - nebula-ca | gpg --symmetric --cipher-algo AES256 -o "$f" && msg "wrote $f — store it and its passphrase somewhere other than this laptop" ;;
  *) die "usage: $0 init | sign NAME IP GROUPS | backup" ;;
esac
