#!/usr/bin/env bash
# 51-custom-stack.sh — run ON THE HOST as root:  51-custom-stack.sh NAME
# Brings up a stack that is not in catalog.json ("your own app"). The installer renders and uploads two files first:
#   $HEARDOS_ROOT/stacks/NAME/compose.yml    service(s) on the external `proxy` network with the kit's Traefik labels
#   $HEARDOS_ROOT/stacks/NAME/custom.json    {"title", "host", "access": admins|family|public, "secrets": [KEY...],
#                                             "dirs": [{"path": "config", "owner": "1000:1000"}...]}
# This script registers the sign-in gate in Authentik (forward auth; none for access=public), creates the data folders
# under $HEARDOS_ROOT/data/NAME, generates each listed secret once (secrets.env, CUSTOM_<NAME>_<KEY>), writes .env and
# deploys. Re-running is safe: secrets and the Authentik application are reused. Public DNS and the Caddy host list are
# laptop-side (PUBLIC_HOSTS). Undo: `docker compose -p NAME down` in the stack folder; data stays in data/NAME.
. "$(dirname "$0")/lib/common.sh"
[ "${1:-}" = "--dry-run" ] && { export HEARDOS_DRY_RUN=1; shift; }
require_root; load_env
name="${1:?stack name}"
[[ "$name" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || die "stack name '$name' must be lowercase letters, digits and dashes"
[ -d "$HERE/stacks/$name" ] && die "'$name' is a catalogue app; install it with 50-app.sh"
d="$HEARDOS_ROOT/stacks/$name"; meta="$d/custom.json"
[ -f "$d/compose.yml" ] && [ -f "$meta" ] || die "upload $d/compose.yml and $d/custom.json first (the installer does this)"
field() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2], ""); print("\n".join(v) if isinstance(v, list) else v)' "$meta" "$1"; }
title="$(field title)"; host="$(field host)"; access="$(field access)"
[[ "$host" =~ ^[a-z][a-z0-9-]{0,30}$ ]] || die "custom.json: host '$host' is not a plain subdomain"
case "$access" in
  admins) HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" proxy "$name" --name "${title:-$name}" --host "https://$host.$DOMAIN" ;;
  family) HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" proxy "$name" --name "${title:-$name}" --host "https://$host.$DOMAIN" --users all ;;
  public) msg "$name: no sign-in gate (access=public): anyone on the internet can open https://$host.$DOMAIN" ;;
  *) die "custom.json: access must be admins, family or public (got '$access')" ;;
esac
# data folders: only plain relative names below data/NAME (custom.json is generated, but still never trust a path)
python3 - "$meta" <<'PY' | while IFS=$'\t' read -r sub owner; do
import json, re, sys
for x in json.load(open(sys.argv[1])).get("dirs", []):
    p, o = str(x.get("path", "")), str(x.get("owner", "") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,40}", p): sys.exit(f"bad data folder {p!r}")
    if o and not re.fullmatch(r"\d{1,6}:\d{1,6}", o): sys.exit(f"bad owner {o!r}")
    print(f"{p}\t{o}")
PY
  install -d "$HEARDOS_ROOT/data/$name/$sub"
  [ -z "$owner" ] || chown "$owner" "$HEARDOS_ROOT/data/$name/$sub"
done
umask 077
{ printf 'HEARDOS_ROOT=%s\nDOMAIN=%s\nLAN_DOMAIN=%s\nTZ=%s\nADMIN_EMAIL=%s\n' "$HEARDOS_ROOT" "$DOMAIN" "$LAN_DOMAIN" "${TZ:-UTC}" "$ADMIN_EMAIL"
  up="CUSTOM_$(echo "$name" | tr 'a-z-' 'A-Z_')"
  field secrets | while read -r k; do
    [ -n "$k" ] || continue
    [[ "$k" =~ ^[A-Z][A-Z0-9_]{0,40}$ ]] || die "custom.json: secret name '$k' must be UPPER_CASE"
    printf '%s=%s\n' "$k" "$(secret "${up}_$k" 32)"
  done; } > "$d/.env"
chmod 600 "$d/.env"
deploy_stack "$name"
msg "$name deployed: https://$host.$DOMAIN (sign-in: $access)"
out "CUSTOM_STACK" "$name"
