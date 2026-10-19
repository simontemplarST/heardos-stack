#!/usr/bin/env bash
# vps/caddy-install.sh — runs ON THE VPS as root. Installs Caddy if missing, opens 80/443, applies the Caddyfile given as
# $1 (default /tmp/Caddyfile.heardos, where the installer puts it): copied into a private work dir first, validated there,
# the previous file kept as Caddyfile.prev. Idempotent.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
SRC="${1:-/tmp/Caddyfile.heardos}"
# apt_key URL FINGERPRINT KEYRING — install a repo signing key only if it carries the expected primary fingerprint
apt_key() {
  local t; t="$(mktemp)"; curl -fsSL "$1" -o "$t"
  gpg --show-keys --with-colons "$t" 2>/dev/null | awk -F: '$1=="fpr"{print $10}' | grep -qx "$2" \
    || { echo "signing key from $1 is not $2; refusing it" >&2; rm -f "$t"; exit 1; }
  gpg --dearmor --yes -o "$3" < "$t"; chmod 644 "$3"; rm -f "$t"
}
if ! command -v caddy >/dev/null; then
  apt-get -qq install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
  apt_key https://dl.cloudsmith.io/public/caddy/stable/gpg.key 65760C51EDEA2017CEA2CA15155B6D79CA56EA34 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" > /etc/apt/sources.list.d/caddy-stable.list
  apt-get -qq update >/dev/null && apt-get -qq install -y caddy >/dev/null
fi
ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
mkdir -p /srv/heardos-brand && chmod 755 /srv/heardos-brand
[ -f "$SRC" ] && [ ! -L "$SRC" ] || { echo "no Caddyfile to apply at $SRC" >&2; exit 1; }
W="$(umask 077; mktemp -d /root/heardos-caddy.XXXXXX)"; trap 'rm -rf "$W"' EXIT
cp "$SRC" "$W/Caddyfile"; rm -f "$SRC"
caddy fmt --overwrite "$W/Caddyfile"
caddy validate --config "$W/Caddyfile" --adapter caddyfile >/dev/null
[ -f /etc/caddy/Caddyfile ] && cp -p /etc/caddy/Caddyfile /etc/caddy/Caddyfile.prev
install -m 644 "$W/Caddyfile" /etc/caddy/Caddyfile
systemctl enable --now caddy >/dev/null; systemctl reload caddy; systemctl is-active caddy
