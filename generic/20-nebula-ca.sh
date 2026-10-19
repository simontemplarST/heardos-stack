#!/usr/bin/env bash
# 20-nebula-ca.sh — run ON THE LAPTOP. Creates the Nebula CA once, signs host certificates, and renders a
# ready-to-install bundle per node under ~/.config/nebula-ca/out/<name>/ (config.yml, ca.crt, host.crt, host.key).
#
#   20-nebula-ca.sh init                      CA + bundles for lighthouse, host, laptop (from heardos.env)
#   20-nebula-ca.sh sign NAME IP GROUPS       e.g. sign phone-bob 10.42.0.31 admins
#   20-nebula-ca.sh renew NAME                re-sign NAME (same IP/groups) before its 1-year certificate runs out
#   20-nebula-ca.sh revoke NAME               block NAME's certificate (pki.blocklist) and re-render every bundle;
#                                             then reinstall the bundles with 21-nebula-node.sh (lighthouse + host first)
#   20-nebula-ca.sh backup                    encrypted copy of the CA (gpg symmetric) -> ./nebula-ca-YYYYMMDD.tgz.gpg
# Nebula itself is pinned (NEBULA_VERSION in the plan file, default below) and checked against the release's SHASUM256.txt.
# Node firewalls by role: the lighthouse takes ssh from admins; the compute host takes anything from admins plus tcp/80
# from the lighthouse (the public ingress hop); every other node (laptops, phones) takes only ICMP.
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?set NEBULA_LH_IP in the plan file before rendering node configs}"
CA="${NEBULA_CA_DIR:-$HOME/.config/nebula-ca}"; BIN="${NEBULA_BIN_DIR:-$HOME/.local/bin}"; mkdir -p "$BIN" "$CA/out"; chmod 700 "$CA"
PREFIX="${OVERLAY_CIDR##*/}"; PREFIX="${PREFIX:-24}"   # node addresses carry the overlay's prefix length
NEBULA_VERSION="${NEBULA_VERSION:-v1.11.2}"            # tested with the kit; bump deliberately
HOST_CERT_HOURS=8760                                   # node certificates: 1 year (renew with "renew NAME"); CA: 10 years
BLOCKLIST="$CA/blocklist.txt"                          # one revoked certificate fingerprint per line

sha256_check() { if command -v sha256sum >/dev/null; then sha256sum -c "$@"; else shasum -a 256 -c "$@"; fi; }

ensure_tools() {
  local have=""; [ -x "$BIN/nebula" ] && have="$("$BIN/nebula" -version 2>/dev/null | awk '/Version/{print "v"$2}')"
  if [ ! -x "$BIN/nebula-cert" ] || [ "$have" != "$NEBULA_VERSION" ]; then
    msg "downloading nebula $NEBULA_VERSION"
    local os arch f t u; os="$(uname -s | tr 'A-Z' 'a-z')"; arch="$(uname -m)"
    case "$arch" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; armv7*) arch=arm-7;; *) die "unsupported CPU $arch";; esac
    if [ "$os" = darwin ]; then f=nebula-darwin.zip; else f="nebula-$os-$arch.tar.gz"; fi
    u="https://github.com/slackhq/nebula/releases/download/$NEBULA_VERSION"
    t="$(mktemp -d)"
    curl -fsSL "$u/$f" -o "$t/$f" && curl -fsSL "$u/SHASUM256.txt" -o "$t/SHASUM256.txt" || { rm -rf "$t"; die "download of nebula $NEBULA_VERSION failed"; }
    # SHASUM256.txt also lists the files inside each archive ("<archive>/nebula"); check just the archive's own line
    grep "  $f\$" "$t/SHASUM256.txt" > "$t/sum" || { rm -rf "$t"; die "$f is not listed in SHASUM256.txt for $NEBULA_VERSION"; }
    (cd "$t" && sha256_check sum >/dev/null) || { rm -rf "$t"; die "checksum mismatch for $f — not installing it"; }
    if [ "$os" = darwin ]; then (cd "$t" && unzip -oq "$f" nebula nebula-cert); else tar -xzf "$t/$f" -C "$t" nebula nebula-cert; fi
    install -m 755 "$t/nebula" "$BIN/nebula"; install -m 755 "$t/nebula-cert" "$BIN/nebula-cert"; rm -rf "$t"
  fi
  echo "$BIN/nebula" > "$CA/.binary"; "$BIN/nebula" -version
}

ensure_ca() {
  cd "$CA"
  [ -f ca.crt ] || { msg "creating CA 'heardos' (10 years)"; "$BIN/nebula-cert" ca -name heardos -duration 87600h; chmod 600 ca.key; }
}

# cert_field NAME.crt ips|groups|fingerprint — read a field from a certificate (v1 and v2 JSON layouts)
cert_field() {
  "$BIN/nebula-cert" print -json -path "$1" | python3 -c '
import json, sys
d = json.load(sys.stdin); d = d[0] if isinstance(d, list) else d; det = d.get("details", d); f = sys.argv[1]
if f == "fingerprint": print(d.get("fingerprint", ""))
elif f == "groups": print(",".join(det.get("groups") or []))
else: print(((det.get("networks") or det.get("ips") or [""])[0]).split("/")[0])' "$2"
}

# render NAME IP — (re)write the bundle's config.yml for its role, with the current blocklist
render_bundle() {
  local name="$1" ip="$2" out="$CA/out/$1"; mkdir -p "$out"
  export NODE_NAME="$name" OVERLAY_CIDR NEBULA_LH_IP NEBULA_LH_OVERLAY NEBULA_ROUTINES=1
  if [ "$name" = lighthouse ]; then render "$HERE/lib/nebula-lighthouse.yml.tmpl" "$out/config.yml"
  else
    # the compute host: admins may reach anything, and the lighthouse tcp/80 (the public ingress hop). Others: ICMP only.
    if [ "$ip" = "$HOST_OVERLAY" ]; then
      export INGRESS_RULE='    - {port: any, proto: any, group: admins}
    - {port: 80, proto: tcp, group: lighthouse}'
    else export INGRESS_RULE=''; fi
    render "$HERE/lib/nebula-node.yml.tmpl" "$out/config.yml"
  fi
  # pki.blocklist: certificates revoked with "revoke NAME" are refused by every node that has this config
  if [ -s "$BLOCKLIST" ]; then
    python3 - "$out/config.yml" "$BLOCKLIST" <<'PY'
import sys
cfg, bl = sys.argv[1], sys.argv[2]
fps = [l.strip() for l in open(bl) if l.strip() and not l.startswith("#")]
lines = open(cfg).read().split("\n"); i = next(n for n, l in enumerate(lines) if l.startswith("  key:"))
lines[i + 1:i + 1] = ["  blocklist:"] + [f"    - {f}" for f in fps]
open(cfg, "w").write("\n".join(lines))
PY
  fi
}

sign() { # NAME IP GROUPS
  local name="$1" ip="$2" groups="$3"; cd "$CA"
  [ -f "$name.crt" ] || { msg "signing $name ($ip, groups $groups, 1 year)"; "$BIN/nebula-cert" sign -ca-crt ca.crt -ca-key ca.key -name "$name" -ip "$ip/$PREFIX" -groups "$groups" -duration "${HOST_CERT_HOURS}h"; chmod 600 "$name.key"; }
  local out="$CA/out/$name"; mkdir -p "$out"; cp ca.crt "$out/ca.crt"; cp "$name.crt" "$out/host.crt"; cp "$name.key" "$out/host.key"; chmod 600 "$out/host.key"
  render_bundle "$name" "$ip"
  msg "bundle ready: $out"
}

renew() { # NAME — new key + certificate with the same IP and groups; the old one keeps working until it expires
  local name="$1"; cd "$CA"; [ -f "$name.crt" ] || die "no certificate $CA/$name.crt"
  local ip groups; ip="$(cert_field "$name.crt" ip)"; groups="$(cert_field "$name.crt" groups)"
  mkdir -p "$CA/old"; mv "$name.crt" "$CA/old/$name.crt.$(date +%Y%m%d%H%M%S)"; mv "$name.key" "$CA/old/$name.key.$(date +%Y%m%d%H%M%S)"
  sign "$name" "$ip" "$groups"
  msg "now reinstall it: 21-nebula-node.sh $name <ssh target | --local>"
}

revoke() { # NAME — blocklist the certificate and re-render every bundle
  local name="$1"; cd "$CA"; [ -f "$name.crt" ] || die "no certificate $CA/$name.crt"
  local fp; fp="$(cert_field "$name.crt" fingerprint)"; [ -n "$fp" ] || die "could not read the fingerprint of $name.crt"
  touch "$BLOCKLIST"; chmod 600 "$BLOCKLIST"
  grep -qx "$fp" "$BLOCKLIST" || printf '%s\n' "$fp" >> "$BLOCKLIST"
  mkdir -p "$CA/revoked"; mv "$name.crt" "$name.key" "$CA/revoked/" 2>/dev/null || true; rm -rf "$CA/out/$name"
  msg "revoked $name ($fp)"
  local d n; for d in "$CA"/out/*/; do
    n="$(basename "$d")"; [ -f "$d/host.crt" ] || continue
    render_bundle "$n" "$(cert_field "$d/host.crt" ip)"; msg "re-rendered $n"
  done
  msg "reinstall the bundles so every node refuses it: 21-nebula-node.sh lighthouse root@<lighthouse>, then host, then the rest"
}

case "${1:-}" in
  init)
    ensure_tools; ensure_ca
    sign lighthouse "$NEBULA_LH_OVERLAY" lighthouse
    sign host "$HOST_OVERLAY" servers
    sign laptop "$LAPTOP_OVERLAY" admins
    msg "next: 22-vps-lighthouse.sh (VPS), then 21-nebula-node.sh host root@$HOST_LAN_IP, then 21-nebula-node.sh laptop --local" ;;
  sign) ensure_tools; ensure_ca; sign "${2:?NAME}" "${3:?IP}" "${4:?GROUPS}" ;;
  renew) ensure_tools; renew "${2:?NAME}" ;;
  revoke) ensure_tools; revoke "${2:?NAME}" ;;
  backup)
    need gpg; f="nebula-ca-$(date +%Y%m%d).tgz.gpg"
    tar -C "$(dirname "$CA")" -czf - "$(basename "$CA")" | gpg --symmetric --cipher-algo AES256 -o "$f" && msg "wrote $f — store it and its passphrase somewhere other than this laptop" ;;
  *) die "usage: $0 init | sign NAME IP GROUPS | renew NAME | revoke NAME | backup" ;;
esac
