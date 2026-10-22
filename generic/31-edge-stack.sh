#!/usr/bin/env bash
# 31-edge-stack.sh — run ON THE HOST as root. Deploys Traefik and the shared dynamic config.
# Apps declare their own routers with labels (see stacks/*/compose.yml); nothing per-app lives here.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
: "${PROXY_SUBNET:?run 10-host-bootstrap.sh first (it records PROXY_SUBNET)}" "${NEBULA_LH_OVERLAY:?}" "${LAN_DOMAIN:?}"
export HEARDOS_ROOT LAN_DOMAIN NEBULA_LH_OVERLAY PROXY_SUBNET
install -d "$HEARDOS_ROOT/stacks/traefik" "$HEARDOS_ROOT/dynamic"
cp "$HERE/stacks/traefik/compose.yml" "$HEARDOS_ROOT/stacks/traefik/compose.yml"
printf 'HEARDOS_ROOT=%s\nLAN_DOMAIN=%s\nNEBULA_LH_OVERLAY=%s\nPROXY_SUBNET=%s\nHOST_LAN_IP=%s\nHOST_OVERLAY=%s\nTRAEFIK_PROXY_IP=%s\n' \
  "$HEARDOS_ROOT" "$LAN_DOMAIN" "$NEBULA_LH_OVERLAY" "$PROXY_SUBNET" "$HOST_LAN_IP" "$HOST_OVERLAY" "${TRAEFIK_PROXY_IP:?run 10-host-bootstrap.sh first}" > "$HEARDOS_ROOT/stacks/traefik/.env"
render "$HERE/lib/dynamic-local-sso.yml.tmpl" "$HEARDOS_ROOT/dynamic/local-sso.yml"
deploy_stack traefik
# Portainer deploys asynchronously: give the container a minute to appear
for i in $(seq 30); do docker ps --format '{{.Names}} {{.Status}}' | grep -E '^traefik ' && break; sleep 2; [ "$i" = 30 ] && die "traefik container did not come up (docker ps -a; docker logs traefik)"; done
msg "Traefik up on :80. LAN names resolve via $LAN_DOMAIN; the dashboard is http://traefik.$LAN_DOMAIN once Authentik exists."
msg "next: 40-authentik-stack.sh (host), then 30-vps-caddy.sh + 32-dns.py (laptop)"
