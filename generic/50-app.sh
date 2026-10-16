#!/usr/bin/env bash
# 50-app.sh — run ON THE HOST as root:  50-app.sh <app>   (paperless mealie homeassistant homebox baikal navidrome onlyoffice nextcloud immich)
# One command per app: registers it in Authentik (via 42-ak-app.py, using AUTHENTIK_TOKEN from the plan file),
# generates its secrets, renders its stack .env, creates data dirs, deploys, and runs the app's post-install.
# Public DNS and the Caddy host list are laptop-side: run 30-vps-caddy.sh and 32-dns.py once PUBLIC_HOSTS is final.
. "$(dirname "$0")/lib/common.sh"; require_root; load_env
app="${1:?app name}"; : "${PROXY_SUBNET:?}" "${AUTHENTIK_TOKEN:?run 40-authentik-stack.sh first}" "${TZ:=UTC}"
d="$HEARDOS_ROOT/stacks/$app"; [ -d "$HERE/stacks/$app" ] || die "no stack definition for '$app'"
install -d "$d"; cp "$HERE/stacks/$app/compose.yml" "$d/compose.yml"
base_env() { printf 'HEARDOS_ROOT=%s\nDOMAIN=%s\nLAN_DOMAIN=%s\nHOST_LAN_IP=%s\nPROXY_SUBNET=%s\nTZ=%s\nADMIN_USER=%s\nADMIN_EMAIL=%s\n' "$HEARDOS_ROOT" "$DOMAIN" "$LAN_DOMAIN" "$HOST_LAN_IP" "$PROXY_SUBNET" "$TZ" "$ADMIN_USER" "$ADMIN_EMAIL"; }
oidc() { # SLUG NAME LAUNCH REDIRECT... -> sets OIDC_CLIENT_ID/SECRET from secrets.env (registering on first run)
  local slug="$1" name="$2" launch="$3"; shift 3; local args=() u; for u in "$@"; do case "$u" in --public) args+=(--public);; *) args+=(--redirect "$u");; esac; done
  local key="AUTHENTIK_$(echo "$slug" | tr 'a-z-' 'A-Z_')"
  if ! grep -q "^${key}_CLIENT_ID=" "$HEARDOS_ROOT/secrets/secrets.env" 2>/dev/null; then
    HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" oidc "$slug" --name "$name" --launch "$launch" "${args[@]}" --out "$HEARDOS_ROOT/secrets/secrets.env"
  else HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" oidc "$slug" --name "$name" --launch "$launch" "${args[@]}" >/dev/null; fi
  OIDC_CLIENT_ID="$(grep "^${key}_CLIENT_ID=" "$HEARDOS_ROOT/secrets/secrets.env" | cut -d= -f2-)"; OIDC_CLIENT_SECRET="$(grep "^${key}_CLIENT_SECRET=" "$HEARDOS_ROOT/secrets/secrets.env" | cut -d= -f2-)"
}
proxy() { HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" proxy "$@"; }
pub="https://$app.$DOMAIN"
case "$app" in
  paperless)
    # x-paperless://oidc-callback + --public: the Swift Paperless iOS app runs the OIDC code exchange itself (PKCE, no secret) — Paperless >= 3.0
    oidc paperless Paperless "$pub/" "$pub/accounts/oidc/authentik/login/callback/" "x-paperless://oidc-callback" --public
    install -d "$HEARDOS_ROOT/data/paperless"/{data,media,export,consume,postgres,redis}
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nPAPERLESS_DB_PASSWORD=%s\nPAPERLESS_SECRET_KEY=%s\nPAPERLESS_ADMIN_PASSWORD=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret PAPERLESS_DB_PASSWORD)" "$(secret PAPERLESS_SECRET_KEY 50)" "$(secret PAPERLESS_ADMIN_PASSWORD 24)"; printf 'OCR_API_KEY=%s\nOCR_LANGS=%s\n' "$(secret OCR_API_KEY 48)" "${OCR_LANGS:-eng}"; } > "$d/.env" ;;
  ocr)
    # OPTIONAL heardos-ocr endpoint (not used by default; see stacks/ocr/compose.yml and stacks/paperless PAPERLESS_REMOTE_OCR_ENGINE)
    install -d "$HEARDOS_ROOT/data/ocr/jobs"; chown 1000:1000 "$HEARDOS_ROOT/data/ocr/jobs"
    install -m 644 "$HERE/stacks/ocr/server.py" "$d/server.py"
    { base_env; printf 'OCR_API_KEY=%s\nOCR_LANGS=%s\nOCR_CPUS=%s\n' "$(secret OCR_API_KEY 48)" "${OCR_LANGS:-eng}" "${OCR_CPUS:-2.0}"; } > "$d/.env" ;;
  mealie)
    oidc mealie Mealie "$pub/" "$pub/login" "http://mealie.$LAN_DOMAIN/login"
    install -d "$HEARDOS_ROOT/data/mealie"
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nMEALIE_ADMIN_PASSWORD=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret MEALIE_ADMIN_PASSWORD 24)"; } > "$d/.env" ;;
  homeassistant)
    pub="https://home.$DOMAIN"; oidc home-assistant "Home Assistant" "$pub/" "$pub/auth/oidc/callback" "http://home.$LAN_DOMAIN/auth/oidc/callback"
    c="$HEARDOS_ROOT/data/homeassistant"; install -d "$c/custom_components"
    if [ ! -d "$c/custom_components/auth_oidc" ]; then
      msg "installing hass-oidc-auth custom component"; tmp="$(mktemp -d)"
      curl -fsSL "$(curl -fsS https://api.github.com/repos/christiaangoossens/hass-oidc-auth/releases/latest | python3 -c 'import sys,json; print(json.load(sys.stdin)["zipball_url"])')" -o "$tmp/r.zip"
      python3 - "$tmp/r.zip" "$c/custom_components" <<'PY'
import sys,zipfile,os,shutil
z=zipfile.ZipFile(sys.argv[1]); root=next(n for n in z.namelist() if n.endswith("custom_components/auth_oidc/")); z.extractall(sys.argv[1]+".d")
shutil.copytree(os.path.join(sys.argv[1]+".d",root), os.path.join(sys.argv[2],"auth_oidc"), dirs_exist_ok=True)
PY
      rm -rf "$tmp"
    fi
    if ! grep -q '^auth_oidc:' "$c/configuration.yaml" 2>/dev/null; then
      [ -f "$c/configuration.yaml" ] || printf 'default_config:\n' > "$c/configuration.yaml"
      cat >> "$c/configuration.yaml" <<EOF

http:
  use_x_forwarded_for: true
  trusted_proxies: [${PROXY_SUBNET}, 127.0.0.1]
  ip_ban_enabled: true
  login_attempts_threshold: 5

auth_oidc:
  client_id: "${OIDC_CLIENT_ID}"
  client_secret: "${OIDC_CLIENT_SECRET}"
  discovery_url: "https://auth.${DOMAIN}/application/o/home-assistant/.well-known/openid-configuration"
  display_name: "HeardOS"
  roles:
    admin: "authentik Admins"
EOF
    fi
    base_env > "$d/.env" ;;
  homebox)
    oidc homebox Homebox "$pub/" "$pub/api/v1/users/login/oidc/callback" "http://homebox.$LAN_DOMAIN/api/v1/users/login/oidc/callback"
    install -d "$HEARDOS_ROOT/data/homebox"; { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET"; } > "$d/.env" ;;
  baikal)
    proxy baikal --name "Baikal (calendar & contacts)" --host "https://dav.$DOMAIN" --launch "https://dav.$DOMAIN/admin/" --skip '^/dav\.php.*' --skip '^/card\.php.*' --skip '^/cal\.php.*' --skip '^/\.well-known/.*'
    install -d "$HEARDOS_ROOT/data/baikal"/{config,specific}; chown -R 33:33 "$HEARDOS_ROOT/data/baikal"; base_env > "$d/.env" ;;
  navidrome)
    proxy navidrome --name "Navidrome (music)" --host "https://music.$DOMAIN" --skip '^/rest(/.*)?$' --skip '^/share(/.*)?$' --skip '^/backgrounds(/.*)?$'
    install -d "$HEARDOS_ROOT/data/navidrome"; chown -R 1000:1000 "$HEARDOS_ROOT/data/navidrome" "$HEARDOS_ROOT/media/music"; base_env > "$d/.env" ;;
  onlyoffice)
    install -d "$HEARDOS_ROOT/data/onlyoffice"/{data,logs,lib,db}
    { base_env; printf 'ONLYOFFICE_JWT_SECRET=%s\nONLYOFFICE_VERSION=9.3\n' "$(secret ONLYOFFICE_JWT_SECRET 40)"; } > "$d/.env" ;;
  nextcloud)
    pub="https://docs.$DOMAIN"; oidc nextcloud "Nextcloud (documents)" "$pub/" "$pub/apps/user_oidc/code" "http://docs.$LAN_DOMAIN/apps/user_oidc/code"
    install -d "$HEARDOS_ROOT/data/nextcloud"/{html,postgres} "$HEARDOS_ROOT/media/documents" "$HEARDOS_ROOT/media/music" "$HEARDOS_ROOT/data/paperless"/{consume,media/documents/originals}
    chmod 2777 "$HEARDOS_ROOT/media/documents" "$HEARDOS_ROOT/media/music" "$HEARDOS_ROOT/data/paperless/consume"   # www-data (33) writes, paperless/navidrome (1000) read
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nNEXTCLOUD_DB_PASSWORD=%s\nNEXTCLOUD_ADMIN_USER=ncadmin\nNEXTCLOUD_ADMIN_PASSWORD=%s\nNEBULA_LH_OVERLAY=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret NEXTCLOUD_DB_PASSWORD)" "$(secret NEXTCLOUD_ADMIN_PASSWORD 24)" "$NEBULA_LH_OVERLAY"; } > "$d/.env" ;;
  immich)
    pub="https://photos.$DOMAIN"; oidc immich "Immich (photos)" "$pub/" "$pub/auth/login" "$pub/user-settings" "$pub/api/oauth/mobile-redirect" "app.immich:///oauth-callback" "http://photos.$LAN_DOMAIN/auth/login" "http://$HOST_LAN_IP:2283/auth/login"
    install -d "$HEARDOS_ROOT/data/immich"/{postgres,model-cache} "$HEARDOS_ROOT/media/photos"
    { base_env; printf 'IMMICH_DB_PASSWORD=%s\nIMMICH_VERSION=release\n' "$(secret IMMICH_DB_PASSWORD)"; } > "$d/.env" ;;
  *) die "unknown app $app" ;;
esac
chmod 600 "$d/.env"
deploy_stack "$app"
case "$app" in
  immich)
    msg "waiting for Immich"; wait_http "http://$HOST_LAN_IP:2283/api/server/ping" 600
    python3 "$HERE/lib/immich-setup.py" "http://$HOST_LAN_IP:2283" "$ADMIN_EMAIL" "$(secret IMMICH_ADMIN_PASSWORD 24)" "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "https://auth.$DOMAIN/application/o/immich/" "$pub" ;;
  paperless)
    msg "waiting for Paperless, then creating category tags/types + workflows (lib/paperless-workflows.py)"
    for i in $(seq 1 60); do curl -fs -o /dev/null -u "$ADMIN_USER:$(grep '^PAPERLESS_ADMIN_PASSWORD=' "$d/.env" | cut -d= -f2-)" "http://paperless.$LAN_DOMAIN/api/ui_settings/" && break; sleep 5; done
    PAPERLESS_URL="http://paperless.$LAN_DOMAIN" PAPERLESS_ADMIN_USER="$ADMIN_USER" PAPERLESS_ADMIN_PASSWORD="$(grep '^PAPERLESS_ADMIN_PASSWORD=' "$d/.env" | cut -d= -f2-)" python3 "$HERE/lib/paperless-workflows.py" || warn "workflow bootstrap failed — re-run lib/paperless-workflows.py later" ;;
  ocr) msg "check: docker exec paperless-webserver python3 -c \"import urllib.request;print(urllib.request.urlopen('http://heardos-ocr:8000/health').read())\"; then consume a scanned PDF and confirm its text in Paperless" ;;
  onlyoffice) msg "Document Server builds fonts on first start (several minutes); check: curl -s http://office.$LAN_DOMAIN/healthcheck -> true" ;;
  nextcloud)
    msg "waiting for the Nextcloud installer"; n=0; until curl -s -m 5 "http://docs.$LAN_DOMAIN/status.php" 2>/dev/null | grep -q '"installed":true'; do sleep 10; n=$((n+1)); [ $n -gt 90 ] && die "Nextcloud did not finish installing"; done
    bash "$HERE/lib/nc-configure.sh" "$DOMAIN" "$LAN_DOMAIN" "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret ONLYOFFICE_JWT_SECRET 40)" "$PROXY_SUBNET" "$NEBULA_LH_OVERLAY"
    msg "log in once via Authentik, then make yourself admin: docker exec -u www-data nextcloud php occ group:adduser admin $ADMIN_USER" ;;
  baikal) msg "finish Baïkal in the browser: http://dav.$LAN_DOMAIN/admin/install/  (choose DAV auth type BASIC; set an admin password; create users)" ;;
  homeassistant) msg "finish HA onboarding at http://home.$LAN_DOMAIN (create the owner account); OIDC login appears on the login page afterwards" ;;
esac
msg "$app deployed. Public URL works once 30-vps-caddy.sh + 32-dns.py include it. Secrets: $HEARDOS_ROOT/secrets/secrets.env"
