#!/usr/bin/env bash
# vps/caddy-install.sh — runs ON THE VPS as root. Installs Caddy if missing, opens 80/443, applies /tmp/Caddyfile.heardos
# (validated first; the previous file is kept as Caddyfile.prev). Idempotent.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null; then
  apt-get -qq install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg >/dev/null
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get -qq update >/dev/null && apt-get -qq install -y caddy >/dev/null
fi
ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
mkdir -p /srv/heardos-brand && chmod 755 /srv/heardos-brand
[ -f /tmp/Caddyfile.heardos ] || { echo "no /tmp/Caddyfile.heardos to apply" >&2; exit 1; }
caddy fmt --overwrite /tmp/Caddyfile.heardos
caddy validate --config /tmp/Caddyfile.heardos --adapter caddyfile >/dev/null
[ -f /etc/caddy/Caddyfile ] && cp -p /etc/caddy/Caddyfile /etc/caddy/Caddyfile.prev
install -m 644 /tmp/Caddyfile.heardos /etc/caddy/Caddyfile && rm -f /tmp/Caddyfile.heardos
systemctl enable --now caddy >/dev/null; systemctl reload caddy; systemctl is-active caddy
