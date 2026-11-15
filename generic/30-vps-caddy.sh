#!/usr/bin/env bash
# 30-vps-caddy.sh — run ON THE LAPTOP. Installs Caddy on the VPS and (re)writes its Caddyfile from PUBLIC_HOSTS.
# Re-run whenever PUBLIC_HOSTS changes. Certificates issue by HTTP-01 once DNS points at the VPS (32-dns.py).
. "$(dirname "$0")/lib/common.sh"; load_env
# An edge published by an ngfw firewall (ngfw ADR 0104) pulls its Caddyfile from the firewall every minute; a file
# written here would be replaced at once. Publish hostnames with lib/ngfw-publish.py (on the host) instead.
[ -z "${NGFW_URL:-}" ] || die "the edge is published by the ngfw firewall (NGFW_URL is set); use lib/ngfw-publish.py publish HOST on the host instead"
: "${NEBULA_LH_IP:?}" "${HOST_OVERLAY:?}" "${PUBLIC_HOSTS:?}" "${ACME_EMAIL:?}"

hosts=""; for h in $PUBLIC_HOSTS; do hosts="${hosts}${hosts:+, }$h.$DOMAIN"; done
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
HEARDOS_ENV="$ENV_FILE" python3 "$HERE/lib/caddyfile.py" > "$T/Caddyfile"   # CADDY_FRONT_DOOR=1 adds cloud.<domain>
msg "installing/refreshing Caddy on $NEBULA_LH_IP for: $hosts"
R="$(lh_stage)"; [ -n "$R" ] || die "could not create a staging directory on the VPS"
lh_put "$T/Caddyfile" "$R/Caddyfile"
lh "bash -s '$R/Caddyfile'; rc=\$?; rm -rf '$R'; exit \$rc" < "$HERE/vps/caddy-install.sh"
msg "overlay reachability of Traefik from the VPS:"; lh "curl -s -o /dev/null -m 10 -w '%{http_code}\n' -H 'Host: auth.$DOMAIN' http://$HOST_OVERLAY/" || warn "Traefik not reachable over the overlay yet (is the host node up and does its firewall allow tcp/80 from group lighthouse?)"
msg "next: 32-dns.py (plan), then 32-dns.py --apply to point the A records at $NEBULA_LH_IP; certificates follow within a minute (journalctl -u caddy on the VPS)"
