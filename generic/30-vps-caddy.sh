#!/usr/bin/env bash
# 30-vps-caddy.sh — run ON THE LAPTOP. Installs Caddy on the VPS and (re)writes its Caddyfile from PUBLIC_HOSTS.
# Re-run whenever PUBLIC_HOSTS changes. Certificates issue by HTTP-01 once DNS points at the VPS (32-dns.py).
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?}" "${HOST_OVERLAY:?}" "${PUBLIC_HOSTS:?}" "${ACME_EMAIL:?}"

hosts=""; for h in $PUBLIC_HOSTS; do hosts="${hosts}${hosts:+, }$h.$DOMAIN"; done
cat > /tmp/Caddyfile.heardos <<EOF
# ${DOMAIN} public edge: TLS here, then over the Nebula overlay to Traefik on the compute host (${HOST_OVERLAY}:80).
{
	email ${ACME_EMAIL}
}

${hosts} {
	header Strict-Transport-Security "max-age=31536000"
	encode zstd gzip
	reverse_proxy ${HOST_OVERLAY}:80 {
		header_up X-Forwarded-Proto https
		header_up X-Forwarded-Port 443
		lb_try_duration 15s
		lb_try_interval 500ms
		transport http {
			dial_timeout 10s
			response_header_timeout 120s
			keepalive 2m
			keepalive_idle_conns 32
		}
	}
	request_body {
		max_size 0
	}
}
EOF
msg "installing/refreshing Caddy on $NEBULA_LH_IP for: $hosts"
lh 'export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null; then
  apt-get -qq install -y debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get -qq update >/dev/null && apt-get -qq install -y caddy >/dev/null
fi
ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null'
lh_put /tmp/Caddyfile.heardos /etc/caddy/Caddyfile; rm -f /tmp/Caddyfile.heardos
lh 'caddy fmt --overwrite /etc/caddy/Caddyfile; caddy validate --config /etc/caddy/Caddyfile >/dev/null && systemctl reload caddy && systemctl is-active caddy'
msg "overlay reachability of Traefik from the VPS:"; lh "curl -s -o /dev/null -m 10 -w '%{http_code}\n' -H 'Host: auth.$DOMAIN' http://$HOST_OVERLAY/" || warn "Traefik not reachable over the overlay yet (is the host node up and does its firewall allow tcp/80 from group lighthouse?)"
msg "next: 32-dns.py to point the A records at $NEBULA_LH_IP; certificates follow within a minute (journalctl -u caddy on the VPS)"
