#!/usr/bin/env bash
# Shared helpers for the HeardOS generic build scripts. Source it; do not run it.
#   . "$(dirname "$0")/lib/common.sh"
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${HEARDOS_ENV:-$HERE/heardos.env}"

msg()  { printf '\033[1;36m::\033[0m %s\n' "$*"; }
# out KEY VALUE — a machine-readable result for the HeardOS installer (it parses "HEARDOS_OUTPUT KEY=VALUE" lines)
out()  { printf 'HEARDOS_OUTPUT %s=%s\n' "$1" "$2"; }
dry()  { [ -n "${HEARDOS_DRY_RUN:-}" ]; }   # true when the installer asked for a dry run
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing '$1' — install it and re-run"; }
require_root() { [ "$(id -u)" = 0 ] || die "run as root"; }

load_env() {
  [ -f "$ENV_FILE" ] || die "plan file not found: $ENV_FILE (copy heardos.env.example to heardos.env and fill it in)"
  # the plan file holds API tokens: it must not be readable by anyone else (and is sourced as shell, so not writable)
  local m; m="$(stat -c %a "$ENV_FILE" 2>/dev/null || stat -f %Lp "$ENV_FILE")"
  case "$m" in
    600|400) ;;
    *) chmod 600 "$ENV_FILE" 2>/dev/null || die "plan file $ENV_FILE is mode $m; chmod 600 it (it holds tokens)"
       warn "plan file $ENV_FILE was mode $m; set it to 600 (it holds tokens)" ;;
  esac
  [ -O "$ENV_FILE" ] || [ "$(id -u)" = 0 ] || die "plan file $ENV_FILE is owned by someone else; refusing to source it"
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

# ssh helpers for the VPS (SSH_KEY lets the installer's dedicated key be used from the laptop scripts too).
# Once 22-vps-lighthouse.sh has closed public ssh it records NEBULA_LH_SSH=<overlay IP> in the plan file.
_sshkey() { [ -n "${SSH_KEY:-}" ] && printf -- '-i %s -o IdentitiesOnly=yes' "$SSH_KEY"; }
_lhaddr() { printf '%s' "${NEBULA_LH_SSH:-${NEBULA_LH_IP:?set NEBULA_LH_IP}}"; }
lh() { ssh -o BatchMode=yes -o ConnectTimeout=10 $(_sshkey) "root@$(_lhaddr)" "$@"; }
# wait_ssh SECONDS — until the VPS accepts our key (fresh VPSes take a minute or two); fails instead of looping forever
wait_ssh() { local t="${1:-600}" i=0; until lh true 2>/dev/null; do sleep 5; i=$((i+5)); if [ "$i" -ge "$t" ]; then return 1; fi; done; return 0; }
lh_put() { scp -q -o BatchMode=yes $(_sshkey) "$1" "root@$(_lhaddr):$2"; }
# lh_stage — a fresh private (0700) staging directory under /root on the VPS; prints its path
lh_stage() { lh 'umask 077; mktemp -d /root/heardos-stage.XXXXXX'; }
# set_plan KEY VALUE — add or replace KEY=VALUE in the plan file
set_plan() {
  if grep -q "^$1=" "$ENV_FILE"; then sed -i.bak "s|^$1=.*|$1=$2|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"; else printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; fi
}

# deploy_stack NAME — bring up $HEARDOS_ROOT/stacks/NAME/compose.yml, via Portainer when a token is set, else docker compose.
# compose.override.yml next to it (the installer's per-app settings; 50-app.sh never overwrites it) is merged in: docker
# compose reads it by itself, Portainer gets one merged file (${VAR} references kept, so no secret is inlined).
deploy_stack() {
  local name="$1" dir="$HEARDOS_ROOT/stacks/$1" file
  [ -f "$dir/compose.yml" ] || die "no compose.yml in $dir"
  if dry; then msg "[dry-run] would deploy stack $name from $dir"; return 0; fi
  if [ -n "${PORTAINER_TOKEN:-}" ]; then
    file="$dir/compose.yml"
    if [ -f "$dir/compose.override.yml" ]; then
      # one merged file for Portainer, strings kept as written (${VAR} stays a reference; `docker compose config
      # --no-interpolate` would turn "${HEARDOS_ROOT}/x:/y" into an undefined named volume)
      file="$dir/.compose.merged.yml"
      python3 "$HERE/lib/compose-merge.py" "$dir/compose.yml" "$dir/compose.override.yml" > "$file"
    fi
    "$HERE/lib/portainer-stack.py" "$name" "$file" "$dir/.env"
  else
    ( cd "$dir" && docker compose --project-name "$name" up -d --remove-orphans )
  fi
}

wait_http() { # wait_http URL [seconds] — until URL answers any HTTP status; 1 when it never did within the time
  local url="$1" t="${2:-120}" i=0
  # an explicit return: a function's status is that of its last command, and the loop body's "[ ] && return 1" leaves
  # status 1 behind whenever the service needed a moment (which made every wait after a redeploy look like a failure)
  until curl -s -o /dev/null -m 5 "$url"; do sleep 3; i=$((i+3)); if [ "$i" -ge "$t" ]; then return 1; fi; done
  return 0
}
