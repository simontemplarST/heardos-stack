#!/bin/bash
# Nextcloud post-install (run on the host by 50-app.sh nextcloud). args: DOMAIN LAN_DOMAIN CLIENT_ID TRAEFIK_PROXY_IP LH_OVERLAY
# Secrets come from the environment (NC_OIDC_CLIENT_SECRET, NC_JWT_SECRET), never this script's command line.
set -euo pipefail; DOMAIN="$1"; LAN="$2"; CID="$3"; TIP="$4"; LH="$5"; C=nextcloud
: "${NC_OIDC_CLIENT_SECRET:?}" "${NC_JWT_SECRET:?}"
occ() { docker exec -u www-data "$C" php occ "$@"; }
occ config:system:set overwrite.cli.url --value "https://docs.$DOMAIN" >/dev/null
occ config:system:set overwriteprotocol --value https >/dev/null
occ config:system:set maintenance_window_start --type integer --value 1 >/dev/null
occ config:system:set trusted_proxies 0 --value "$TIP/32" >/dev/null; occ config:system:set trusted_proxies 1 --value "$LH/32" >/dev/null
# Needed: Nextcloud reaches Document Server as http://onlyoffice/ (a private address on the proxy network) and Nextcloud's
# HTTP client refuses private/local targets unless this is on; there is no per-host allow list. It also lets users point
# federated shares / external storage at LAN hosts — acceptable for a single-family instance (only family accounts exist).
occ config:system:set allow_local_remote_servers --type boolean --value true >/dev/null
for a in user_oidc onlyoffice files_external; do occ app:install "$a" >/dev/null 2>&1 || occ app:enable "$a" >/dev/null; done
if ! occ user_oidc:provider 2>/dev/null | grep -q authentik; then
  # occ has no stdin/env option for the client secret: it travels in the container's environment and is expanded by the
  # shell inside the container (briefly in that php process's argv there, never on the host's docker command line)
  CSEC="$NC_OIDC_CLIENT_SECRET" docker exec -e CSEC -u www-data "$C" sh -c 'php occ user_oidc:provider authentik --clientid "$1" --clientsecret "$CSEC" \
    --discoveryuri "https://auth.$2/application/o/nextcloud/.well-known/openid-configuration" --scope "openid email profile" --unique-uid 0 \
    --mapping-uid preferred_username --mapping-display-name name --mapping-email email --group-provisioning 1 --mapping-groups groups' sh "$CID" "$DOMAIN" >/dev/null
fi
occ config:app:set user_oidc allow_multiple_user_backends --value 1 >/dev/null
occ config:app:set onlyoffice DocumentServerUrl --value "https://office.$DOMAIN/" >/dev/null
occ config:app:set onlyoffice DocumentServerInternalUrl --value "http://onlyoffice/" >/dev/null
occ config:app:set onlyoffice StorageUrl --value "http://nextcloud/" >/dev/null
# JWT secret via `occ config:import` on stdin (JSON), so it never appears in any argv
python3 -c 'import json,os; print(json.dumps({"apps": {"onlyoffice": {"jwt_secret": os.environ["NC_JWT_SECRET"], "jwt_header": "Authorization"}}}))' \
  | docker exec -i -u www-data "$C" php occ config:import >/dev/null
have="$(occ files_external:list --output=json 2>/dev/null || echo '[]')"
mk() { if ! echo "$have" | grep -q "\"mount_point\": \"/$1\""; then id="$(occ files_external:create "$1" local null::null -c datadir="$2" | grep -oE '[0-9]+' | tail -1)"; [ "${3:-}" = ro ] && occ files_external:option "$id" readonly true >/dev/null; occ files_external:option "$id" filesystem_check_changes 1 >/dev/null; echo "  storage '$1' -> $2${3:+ ($3)}"; fi; }
mk "Family documents" /mnt/documents; mk "Music" /mnt/music; mk "Paperless inbox" /mnt/paperless-inbox; mk "Paperless archive" /mnt/paperless-archive ro
occ background:cron >/dev/null
occ onlyoffice:documentserver --check 2>&1 | tail -1
