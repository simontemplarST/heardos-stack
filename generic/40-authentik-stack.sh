#!/usr/bin/env bash
# 40-authentik-stack.sh — run ON THE HOST as root. Generates secrets, deploys Authentik, waits until it is ready,
# and records the bootstrap API token in the plan file as AUTHENTIK_TOKEN (copy it to the laptop's plan file too).
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
: "${ADMIN_EMAIL:?}" "${TZ:=UTC}"
d="$HEARDOS_ROOT/stacks/authentik"; install -d "$d" "$HEARDOS_ROOT/data/authentik"/{postgres,redis,media,templates,certs}
cp "$HERE/stacks/authentik/compose.yml" "$d/compose.yml"
PG_PASS="$(secret AUTHENTIK_PG_PASS 40)"; SK="$(secret AUTHENTIK_SECRET_KEY 60)"
BP="$(secret AUTHENTIK_AKADMIN_PASSWORD 28)"; BT="$(secret AUTHENTIK_BOOTSTRAP_TOKEN 60)"
cat > "$d/.env" <<EOF
HEARDOS_ROOT=$HEARDOS_ROOT
DOMAIN=$DOMAIN
LAN_DOMAIN=$LAN_DOMAIN
HOST_LAN_IP=$HOST_LAN_IP
ADMIN_EMAIL=$ADMIN_EMAIL
TZ=$TZ
PG_PASS=$PG_PASS
AUTHENTIK_SECRET_KEY=$SK
AUTHENTIK_BOOTSTRAP_PASSWORD=$BP
AUTHENTIK_BOOTSTRAP_TOKEN=$BT
EOF
chmod 600 "$d/.env"
deploy_stack authentik
msg "waiting for Authentik (first start migrates the database; 1-3 minutes)"
wait_http "http://$HOST_LAN_IP:9000/-/health/ready/" 600 || die "Authentik did not become ready; docker logs authentik-server"
until curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $BT" "http://$HOST_LAN_IP:9000/api/v3/core/users/me/" | grep -q 200; do sleep 5; done
grep -q '^AUTHENTIK_TOKEN=' "$ENV_FILE" && sed -i "s|^AUTHENTIK_TOKEN=.*|AUTHENTIK_TOKEN=$BT|" "$ENV_FILE" || echo "AUTHENTIK_TOKEN=$BT" >> "$ENV_FILE"
grep -q '^AUTHENTIK_URL=' "$ENV_FILE" || echo "AUTHENTIK_URL=http://$HOST_LAN_IP:9000" >> "$ENV_FILE"
msg "Authentik is up: http://auth.$LAN_DOMAIN  (akadmin password and API token are in $HEARDOS_ROOT/secrets/secrets.env)"
msg "copy AUTHENTIK_TOKEN=$BT into the laptop's heardos.env, then run 41-authentik-bootstrap.py"
