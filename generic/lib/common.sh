#!/usr/bin/env bash
# Shared helpers for the HeardOS generic build scripts. Source it; do not run it.
#   . "$(dirname "$0")/lib/common.sh"
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${HEARDOS_ENV:-$HERE/heardos.env}"

msg()  { printf '\033[1;36m::\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing '$1' — install it and re-run"; }
require_root() { [ "$(id -u)" = 0 ] || die "run as root"; }

load_env() {
  [ -f "$ENV_FILE" ] || die "plan file not found: $ENV_FILE (copy heardos.env.example to heardos.env and fill it in)"
  set -a; . "$ENV_FILE"; set +a
  : "${DOMAIN:?set DOMAIN in $ENV_FILE}" "${HOST_LAN_IP:?set HOST_LAN_IP}" "${HEARDOS_ROOT:=/opt/heardos}"
}

gen_secret() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-32}"; }

# secret NAME [LEN] — print the value of NAME from $HEARDOS_ROOT/secrets/secrets.env, creating it if absent.
secret() {
  local f="$HEARDOS_ROOT/secrets/secrets.env" name="$1" len="${2:-32}" val
  mkdir -p "$(dirname "$f")"; touch "$f"; chmod 700 "$(dirname "$f")"; chmod 600 "$f"
  val="$(grep -E "^${name}=" "$f" | head -1 | cut -d= -f2- || true)"
  if [ -z "$val" ]; then val="$(gen_secret "$len")"; printf '%s=%s\n' "$name" "$val" >> "$f"; fi
  printf '%s' "$val"
}

# render SRC DST — substitute ${VAR} placeholders from the environment (only the variables we export).
render() { envsubst < "$1" > "$2"; }

# ssh helpers for the VPS
lh() { ssh -o BatchMode=yes -o ConnectTimeout=10 "root@${NEBULA_LH_IP:?set NEBULA_LH_IP}" "$@"; }
lh_put() { scp -q -o BatchMode=yes "$1" "root@${NEBULA_LH_IP:?}:$2"; }

# deploy_stack NAME — bring up $HEARDOS_ROOT/stacks/NAME/compose.yml, via Portainer when a token is set, else docker compose.
deploy_stack() {
  local name="$1" dir="$HEARDOS_ROOT/stacks/$1"
  [ -f "$dir/compose.yml" ] || die "no compose.yml in $dir"
  if [ -n "${PORTAINER_TOKEN:-}" ]; then
    "$HERE/lib/portainer-stack.py" "$name" "$dir/compose.yml" "$dir/.env"
  else
    ( cd "$dir" && docker compose --project-name "$name" up -d --remove-orphans )
  fi
}

wait_http() { # wait_http URL [seconds] — until URL answers any HTTP status
  local url="$1" t="${2:-120}" i=0
  until curl -s -o /dev/null -m 5 "$url"; do sleep 3; i=$((i+3)); [ "$i" -ge "$t" ] && return 1; done
}
