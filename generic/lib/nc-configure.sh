#!/bin/bash
# Nextcloud post-install (run on the host by 50-app.sh nextcloud). args: DOMAIN LAN_DOMAIN CLIENT_ID CLIENT_SECRET JWT_SECRET PROXY_SUBNET LH_OVERLAY
set -euo pipefail; DOMAIN="$1"; LAN="$2"; CID="$3"; CSEC="$4"; JWT="$5"; PSUB="$6"; LH="$7"; C=nextcloud
occ() { docker exec -u www-data "$C" php occ "$@"; }
occ config:system:set overwrite.cli.url --value "https://docs.$DOMAIN" >/dev/null
occ config:system:set overwriteprotocol --value https >/dev/null
occ config:system:set maintenance_window_start --type integer --value 1 >/dev/null
occ config:system:set trusted_proxies 0 --value "$PSUB" >/dev/null; occ config:system:set trusted_proxies 1 --value "$LH" >/dev/null
occ config:system:set allow_local_remote_servers --type boolean --value true >/dev/null
for a in user_oidc onlyoffice files_external; do occ app:install "$a" >/dev/null 2>&1 || occ app:enable "$a" >/dev/null; done
if ! occ user_oidc:provider 2>/dev/null | grep -q authentik; then
  occ user_oidc:provider authentik --clientid "$CID" --clientsecret "$CSEC" --discoveryuri "https://auth.$DOMAIN/application/o/nextcloud/.well-known/openid-configuration" \
    --scope "openid email profile" --unique-uid 0 --mapping-uid preferred_username --mapping-display-name name --mapping-email email --group-provisioning 1 --mapping-groups groups >/dev/null
fi
occ config:app:set user_oidc allow_multiple_user_backends --value 1 >/dev/null
occ config:app:set onlyoffice DocumentServerUrl --value "https://office.$DOMAIN/" >/dev/null
occ config:app:set onlyoffice DocumentServerInternalUrl --value "http://onlyoffice/" >/dev/null
occ config:app:set onlyoffice StorageUrl --value "http://nextcloud/" >/dev/null
occ config:app:set onlyoffice jwt_secret --value "$JWT" >/dev/null; occ config:app:set onlyoffice jwt_header --value Authorization >/dev/null
have="$(occ files_external:list --output=json 2>/dev/null || echo '[]')"
mk() { if ! echo "$have" | grep -q "\"mount_point\": \"/$1\""; then id="$(occ files_external:create "$1" local null::null -c datadir="$2" | grep -oE '[0-9]+' | tail -1)"; [ "${3:-}" = ro ] && occ files_external:option "$id" readonly true >/dev/null; occ files_external:option "$id" filesystem_check_changes 1 >/dev/null; echo "  storage '$1' -> $2${3:+ ($3)}"; fi; }
mk "Family documents" /mnt/documents; mk "Music" /mnt/music; mk "Paperless inbox" /mnt/paperless-inbox; mk "Paperless archive" /mnt/paperless-archive ro
occ background:cron >/dev/null
occ onlyoffice:documentserver --check 2>&1 | tail -1
