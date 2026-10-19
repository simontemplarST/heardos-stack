#!/usr/bin/env bash
# 40-authentik-stack.sh — run ON THE HOST as root. Generates secrets, deploys Authentik, waits until it is ready, then
# swaps the one-time bootstrap credentials for a dedicated automation account (first run only):
#   1. the stack .env gets AUTHENTIK_BOOTSTRAP_PASSWORD/TOKEN (Authentik creates akadmin + its token on first start)
#   2. `42-ak-app.py token rotate` creates the `heardos-automation` service account (superuser group) with a 90-day API
#      token and writes it to the plan file as AUTHENTIK_TOKEN (and a HEARDOS_OUTPUT line for the installer)
#   3. the stack is redeployed WITHOUT the bootstrap variables, then akadmin's bootstrap token is deleted
# Re-runs keep the bootstrap variables out. The token expires: rotate it with `42-ak-app.py token rotate --write-env`
# (see docs/authentik-flows.md). akadmin's password stays in $HEARDOS_ROOT/secrets/secrets.env as break-glass.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
: "${ADMIN_EMAIL:?}" "${TZ:=UTC}"
d="$HEARDOS_ROOT/stacks/authentik"; install -d "$d" "$HEARDOS_ROOT/data/authentik"/{postgres,redis,media,templates,certs}
chown -R 1000:1000 "$HEARDOS_ROOT/data/authentik"/{media,templates,certs}   # the worker runs as the image's uid 1000, not root
cp "$HERE/stacks/authentik/compose.yml" "$d/compose.yml"
PG_PASS="$(secret AUTHENTIK_PG_PASS 40)"; SK="$(secret AUTHENTIK_SECRET_KEY 60)"
BP="$(secret AUTHENTIK_AKADMIN_PASSWORD 28)"
done_mark="$HEARDOS_ROOT/secrets/authentik-bootstrap.done"
keep="$(grep "^EMAIL_" "$d/.env" 2>/dev/null || true)"   # mail settings written by 43 survive a re-run
api="http://$HOST_LAN_IP:9000/api/v3"

write_env() { # write_env [BOOTSTRAP_TOKEN] — the stack .env; the bootstrap variables only while the hand-over is pending
  ( umask 077
    { cat <<EOF
HEARDOS_ROOT=$HEARDOS_ROOT
DOMAIN=$DOMAIN
LAN_DOMAIN=$LAN_DOMAIN
HOST_LAN_IP=$HOST_LAN_IP
ADMIN_EMAIL=$ADMIN_EMAIL
TZ=$TZ
PG_PASS=$PG_PASS
AUTHENTIK_SECRET_KEY=$SK
EOF
      if [ -n "${1:-}" ]; then printf 'AUTHENTIK_BOOTSTRAP_PASSWORD=%s\nAUTHENTIK_BOOTSTRAP_TOKEN=%s\n' "$BP" "$1"; fi
      if [ -n "$keep" ]; then printf '%s\n' "$keep"; fi
    } > "$d/.env.tmp" )
  mv "$d/.env.tmp" "$d/.env"; chmod 600 "$d/.env"
}
# tok_ok TOKEN — the API accepts TOKEN (sent through curl's config on stdin, never on a command line)
tok_ok() { printf 'header = "Authorization: Bearer %s"\n' "$1" | curl -s -m 10 -K - -o /dev/null -w '%{http_code}' "$api/core/users/me/" | grep -q 200; }
wait_tok() { local n=0; until tok_ok "$1"; do sleep 5; n=$((n+5)); [ $n -ge 300 ] && return 1; done; }
# set_plan KEY VALUE — replace or append KEY= in the plan file without putting VALUE on a command line
set_plan() { local t; t="$(mktemp "$ENV_FILE.XXXXXX")"; { grep -v "^$1=" "$ENV_FILE" || true; printf '%s=%s\n' "$1" "$2"; } > "$t"; mv "$t" "$ENV_FILE"; chmod 600 "$ENV_FILE"; }
ready() { wait_http "http://$HOST_LAN_IP:9000/-/health/ready/" 600 || die "Authentik did not become ready; docker logs authentik-server"; }

grep -q '^AUTHENTIK_URL=' "$ENV_FILE" || echo "AUTHENTIK_URL=http://$HOST_LAN_IP:9000" >> "$ENV_FILE"
out AUTHENTIK_URL "http://$HOST_LAN_IP:9000"

# hand-over already done but the marker is missing (interrupted run, restored backup): the plan's token is not the
# bootstrap token and the running Authentik accepts it
old_bt="$(grep -E '^AUTHENTIK_BOOTSTRAP_TOKEN=' "$HEARDOS_ROOT/secrets/secrets.env" 2>/dev/null | cut -d= -f2- || true)"
if [ ! -f "$done_mark" ] && [ -n "${AUTHENTIK_TOKEN:-}" ] && [ "$AUTHENTIK_TOKEN" != "$old_bt" ] && ! dry && tok_ok "$AUTHENTIK_TOKEN"; then
  touch "$done_mark"
fi
if [ -f "$done_mark" ]; then
  write_env; deploy_stack authentik
  dry && exit 0
  msg "waiting for Authentik"; ready
  if [ -n "${AUTHENTIK_TOKEN:-}" ] && ! wait_tok "$AUTHENTIK_TOKEN"; then
    warn "AUTHENTIK_TOKEN in $ENV_FILE is not accepted (expired?). Mint a new one: Admin UI as akadmin -> Directory -> Tokens,"
    warn "or temporarily put that token in the plan file and run: 42-ak-app.py token rotate --write-env"
  fi
  msg "Authentik is up: http://auth.$LAN_DOMAIN (bootstrap hand-over already done)"
  exit 0
fi

BT="$(secret AUTHENTIK_BOOTSTRAP_TOKEN 60)"
write_env "$BT"; deploy_stack authentik
dry && { msg "[dry-run] would wait for Authentik, create heardos-automation and remove the bootstrap credentials"; exit 0; }
msg "waiting for Authentik (first start migrates the database; 1-3 minutes)"; ready
wait_tok "$BT" || die "bootstrap token not accepted after 5 minutes"
set_plan AUTHENTIK_TOKEN "$BT"
msg "creating the heardos-automation service account and its API token"
HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" token rotate --write-env   # emits HEARDOS_OUTPUT AUTHENTIK_TOKEN=...
load_env
[ "${AUTHENTIK_TOKEN:-}" != "$BT" ] || die "token rotation did not update AUTHENTIK_TOKEN in $ENV_FILE"
msg "redeploying Authentik without the bootstrap credentials"
write_env; deploy_stack authentik; ready
wait_tok "$AUTHENTIK_TOKEN" || die "automation token not accepted after redeploy"
HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" token revoke-bootstrap
touch "$done_mark"
sed -i '/^AUTHENTIK_BOOTSTRAP_TOKEN=/d' "$HEARDOS_ROOT/secrets/secrets.env"
msg "Authentik is up: http://auth.$LAN_DOMAIN  (akadmin password: AUTHENTIK_AKADMIN_PASSWORD in $HEARDOS_ROOT/secrets/secrets.env)"
msg "AUTHENTIK_TOKEN (heardos-automation, 90 days) is in $ENV_FILE; copy it into the laptop's heardos.env, then run 41-authentik-bootstrap.py"
