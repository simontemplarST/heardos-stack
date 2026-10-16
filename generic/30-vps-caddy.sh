#!/usr/bin/env bash
# 30-vps-caddy.sh — run ON THE LAPTOP. Installs Caddy on the VPS and (re)writes its Caddyfile from PUBLIC_HOSTS.
# Re-run whenever PUBLIC_HOSTS changes. Certificates issue by HTTP-01 once DNS points at the VPS (32-dns.py).
. "$(dirname "$0")/lib/common.sh"; load_env
: "${NEBULA_LH_IP:?}" "${HOST_OVERLAY:?}" "${PUBLIC_HOSTS:?}" "${ACME_EMAIL:?}"

hosts=""; for h in $PUBLIC_HOSTS; do hosts="${hosts}${hosts:+, }$h.$DOMAIN"; done
HEARDOS_ENV="$ENV_FILE" python3 "$HERE/lib/caddyfile.py" > /tmp/Caddyfile.heardos   # CADDY_FRONT_DOOR=1 adds cloud.<domain>
msg "installing/refreshing Caddy on $NEBULA_LH_IP for: $hosts"
lh_put /tmp/Caddyfile.heardos /tmp/Caddyfile.heardos; rm -f /tmp/Caddyfile.heardos
lh 'bash -s' < "$HERE/vps/caddy-install.sh"
msg "overlay reachability of Traefik from the VPS:"; lh "curl -s -o /dev/null -m 10 -w '%{http_code}\n' -H 'Host: auth.$DOMAIN' http://$HOST_OVERLAY/" || warn "Traefik not reachable over the overlay yet (is the host node up and does its firewall allow tcp/80 from group lighthouse?)"
msg "next: 32-dns.py to point the A records at $NEBULA_LH_IP; certificates follow within a minute (journalctl -u caddy on the VPS)"
