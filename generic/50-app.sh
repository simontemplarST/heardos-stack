#!/usr/bin/env bash
# 50-app.sh — run ON THE HOST as root:  50-app.sh <app>   (see --list: paperless mealie homeassistant homebox baikal navidrome onlyoffice nextcloud immich vaultwarden wallos actual invidious homepage calibre-web librespeed dash hortusfox archivebox listmonk paperless-gpt)
# One command per app: registers it in Authentik (via 42-ak-app.py, using AUTHENTIK_TOKEN from the plan file),
# generates its secrets, renders its stack .env, creates data dirs, deploys, and runs the app's post-install.
# Public DNS and the Caddy host list are laptop-side: run 30-vps-caddy.sh and 32-dns.py once PUBLIC_HOSTS is final.
. "$(dirname "$0")/lib/common.sh"
if [ "${1:-}" = "--list" ]; then   # apps this kit can install (from catalog.json), one per line: stack<TAB>name<TAB>depends_on
  python3 -c 'import json,sys,os; c=json.load(open(sys.argv[1])); [print(a["stack"]+"\t"+a["name"]+"\t"+",".join(a.get("depends_on",[]))) for a in c["apps"] if not a.get("core") and os.path.isdir(os.path.join(sys.argv[2],a["stack"]))]' "$HERE/catalog.json" "$HERE/stacks"; exit 0
fi
[ "${1:-}" = "--dry-run" ] && { export HEARDOS_DRY_RUN=1; shift; }
require_root; load_env
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
have() { grep -E "^$1=" "$HEARDOS_ROOT/secrets/secrets.env" 2>/dev/null | head -1 | cut -d= -f2- || true; }   # read a secret, never create it
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
  vaultwarden)
    pub="https://vault.$DOMAIN"; oidc vaultwarden "Vaultwarden (passwords)" "$pub/" "$pub/identity/connect/oidc-signin" "http://vault.$LAN_DOMAIN/identity/connect/oidc-signin"
    install -d "$HEARDOS_ROOT/data/vaultwarden"
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nVAULTWARDEN_ADMIN_TOKEN=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret VAULTWARDEN_ADMIN_TOKEN 48)"; } > "$d/.env" ;;
  wallos)
    oidc wallos "Wallos (subscriptions)" "$pub/" "$pub/" "http://wallos.$LAN_DOMAIN/"
    install -d "$HEARDOS_ROOT/data/wallos"/{db,logos}
    { base_env; printf 'BRAND_TITLE=%s\nOIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\n' "${BRAND_TITLE:-HeardOS}" "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET"; } > "$d/.env" ;;
  actual)
    pub="https://budget.$DOMAIN"; oidc actual-budget "Actual (budget)" "$pub/" "$pub/openid/callback" "http://budget.$LAN_DOMAIN/openid/callback"
    install -d "$HEARDOS_ROOT/data/actual"
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nNEBULA_LH_OVERLAY=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$NEBULA_LH_OVERLAY"; } > "$d/.env" ;;
  invidious)
    proxy invidious --name "Invidious (videos)" --host "https://tube.$DOMAIN" --skip '^/api/v1/.*' --skip '^/feed/private.*'
    install -d "$HEARDOS_ROOT/data/invidious"/{postgres,companion-cache}; chown 10001:10001 "$HEARDOS_ROOT/data/invidious/companion-cache"
    { base_env; printf 'INVIDIOUS_DB_PASSWORD=%s\nINVIDIOUS_HMAC_KEY=%s\nINVIDIOUS_COMPANION_KEY=%s\n' "$(secret INVIDIOUS_DB_PASSWORD)" "$(secret INVIDIOUS_HMAC_KEY 32)" "$(secret INVIDIOUS_COMPANION_KEY 16)"; } > "$d/.env" ;;
  homepage)
    proxy hub --name "Family hub" --host "https://hub.$DOMAIN"
    install -d "$HEARDOS_ROOT/data/homepage"; base_env > "$d/.env" ;;
  calibre-web)
    proxy calibre-web --name "Books (Calibre-Web)" --host "https://books.$DOMAIN" --skip '^/opds(/.*)?$' --skip '^/kobo(/.*)?$'
    install -d "$HEARDOS_ROOT/data/calibre-web" "$HEARDOS_ROOT/media/books"; chown 1000:1000 "$HEARDOS_ROOT/data/calibre-web" "$HEARDOS_ROOT/media/books"
    base_env > "$d/.env" ;;
  librespeed)
    install -d "$HEARDOS_ROOT/data/librespeed/www"
    [ -f "$HERE/../apps/librespeed/index.html" ] && install -m 644 "$HERE/../apps/librespeed/index.html" "$HEARDOS_ROOT/data/librespeed/www/index.html"
    { base_env; printf 'LIBRESPEED_STATS_PASSWORD=%s\n' "$(secret LIBRESPEED_STATS_PASSWORD 24)"; } > "$d/.env" ;;
  dash)
    proxy dash --name "Family dashboard" --host "https://dash.$DOMAIN"
    install -d "$HEARDOS_ROOT/data/dash"; chown 1000:1000 "$HEARDOS_ROOT/data/dash"
    if dry; then msg "[dry-run] would build heardos-dash:local from apps/dash"
    else msg "building the dashboard image"; docker build -q -t heardos-dash:local "$HERE/../apps/dash" >/dev/null; fi
    # tokens come from the post-install automation (Mealie token, Homebox API key, Actual session); empty = "not configured" card
    { base_env; c="https://cloud.$DOMAIN"
      printf 'MEALIE_PUBLIC_URL=%s\nHOMEBOX_PUBLIC_URL=%s\nACTUAL_PUBLIC_URL=%s\n' "$c/mealie" "$c/homebox" "$c/actual-budget"
      printf 'MEALIE_TOKEN=%s\nHOMEBOX_API_KEY=%s\nACTUAL_SYNC_ID=%s\nACTUAL_PASSWORD=%s\n' "$(have MEALIE_TOKEN)" "$(have HOMEBOX_API_KEY)" "$(have ACTUAL_SYNC_ID)" "$(have ACTUAL_SESSION_TOKEN)"; } > "$d/.env" ;;
  hortusfox)
    proxy hortusfox --name "Plants (HortusFox)" --host "https://plants.$DOMAIN" --launch "https://cloud.$DOMAIN/plants"
    install -d "$HEARDOS_ROOT/data/hortusfox"/{img,attachments,logs,backup,themes,migrate,db}
    { base_env; printf 'HORTUS_ADMIN_PASSWORD=%s\nHORTUS_DB_PASSWORD=%s\nHORTUS_DB_ROOT_PASSWORD=%s\n' "$(secret HORTUS_ADMIN_PASSWORD 24)" "$(secret HORTUS_DB_PASSWORD)" "$(secret HORTUS_DB_ROOT_PASSWORD)"; } > "$d/.env" ;;
  archivebox)
    proxy archivebox --name "Saved pages (ArchiveBox)" --host "https://archive.$DOMAIN" --launch "https://cloud.$DOMAIN/archive"
    install -d "$HEARDOS_ROOT/data/archivebox"
    { base_env; printf 'ARCHIVEBOX_ADMIN_PASSWORD=%s\n' "$(secret ARCHIVEBOX_ADMIN_PASSWORD 24)"; } > "$d/.env" ;;
  listmonk)
    # gate for /admin + admin API (owner only), then Listmonk's own OIDC sign-in (hidden app); public pages stay open
    proxy listmonk --name "Newsletters (Listmonk)" --host "https://news.$DOMAIN" --launch "https://cloud.$DOMAIN/news" --users "$ADMIN_USER"
    oidc listmonk-sso "Listmonk sign-in" "https://news.$DOMAIN/" "https://news.$DOMAIN/auth/oidc"
    install -d "$HEARDOS_ROOT/data/listmonk"/{uploads,db}
    { base_env; printf 'LISTMONK_ADMIN_PASSWORD=%s\nLISTMONK_DB_PASSWORD=%s\n' "$(secret LISTMONK_ADMIN_PASSWORD 24)" "$(secret LISTMONK_DB_PASSWORD)"; } > "$d/.env" ;;
  paperless-gpt)
    # needs Paperless running: creates the paperless-gpt service user + API token (PAPERLESS_GPT_TOKEN) first
    install -d "$HEARDOS_ROOT/data/ollama" "$HEARDOS_ROOT/data/paperless-gpt/prompts"
    dry || python3 "$HERE/63-post-install.py" paperless-gpt-token >/dev/null || die "paperless-gpt needs Paperless running (50-app.sh paperless)"
    { base_env; printf 'PAPERLESS_GPT_TOKEN=%s\nPAPERLESS_GPT_MODEL=%s\nOLLAMA_CPUS=%s\n' "$(have PAPERLESS_GPT_TOKEN)" "${PAPERLESS_GPT_MODEL:-llama3.2:3b}" "${OLLAMA_CPUS:-6}"; } > "$d/.env" ;;
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
  calibre-web)
    msg "waiting for Calibre-Web (the Calibre tools install on first start, ~1-2 min), then enabling header login"
    dry || PROXY_SUBNET="$PROXY_SUBNET" ADMIN_USER="$ADMIN_USER" ADMIN_EMAIL="$ADMIN_EMAIL" CALIBREWEB_ADMIN_PASSWORD="$(secret CALIBREWEB_ADMIN_PASSWORD 24)" HEARDOS_ROOT="$HEARDOS_ROOT" python3 "$HERE/lib/calibre-web-setup.py" ;;
  vaultwarden) msg "admin page: https://vault.$DOMAIN/admin (token VAULTWARDEN_ADMIN_TOKEN in secrets.env); people sign in with their family account" ;;
  actual) msg "the first person to sign in to https://budget.$DOMAIN becomes the budget owner — sign in as $ADMIN_USER first" ;;
  ocr) msg "check: docker exec paperless-webserver python3 -c \"import urllib.request;print(urllib.request.urlopen('http://heardos-ocr:8000/health').read())\"; then consume a scanned PDF and confirm its text in Paperless" ;;
  onlyoffice) msg "Document Server builds fonts on first start (several minutes); check: curl -s http://office.$LAN_DOMAIN/healthcheck -> true" ;;
  nextcloud)
    msg "waiting for the Nextcloud installer"; n=0; until curl -s -m 5 "http://docs.$LAN_DOMAIN/status.php" 2>/dev/null | grep -q '"installed":true'; do sleep 10; n=$((n+1)); [ $n -gt 90 ] && die "Nextcloud did not finish installing"; done
    bash "$HERE/lib/nc-configure.sh" "$DOMAIN" "$LAN_DOMAIN" "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret ONLYOFFICE_JWT_SECRET 40)" "$PROXY_SUBNET" "$NEBULA_LH_OVERLAY"
    msg "log in once via Authentik, then make yourself admin: docker exec -u www-data nextcloud php occ group:adduser admin $ADMIN_USER" ;;
  baikal) msg "finish Baïkal in the browser: http://dav.$LAN_DOMAIN/admin/install/  (choose DAV auth type BASIC; set an admin password; create users)" ;;
  homeassistant) msg "finish HA onboarding at http://home.$LAN_DOMAIN (create the owner account); OIDC login appears on the login page afterwards" ;;
  hortusfox) dry || python3 "$HERE/63-post-install.py" hortusfox-cron ;;
  listmonk)
    msg "waiting for Listmonk, then setting root URL, sender, SMTP (MAIL_MODE as for Authentik) and OIDC"
    dry || { wait_http "http://news.$LAN_DOMAIN/" 300 2>/dev/null || sleep 20; python3 "$HERE/63-post-install.py" listmonk-config || warn "listmonk-config failed — re-run 63-post-install.py listmonk-config"; } ;;
  paperless-gpt) dry || python3 "$HERE/63-post-install.py" paperless-gpt ;;
esac
if [ -f "$HEARDOS_ROOT/stacks/homepage/compose.yml" ]; then DOMAIN="$DOMAIN" HEARDOS_ROOT="$HEARDOS_ROOT" BRAND_TITLE="${BRAND_TITLE:-}" BRAND_COLOR="${BRAND_COLOR:-}" python3 "$HERE/lib/homepage-config.py" "$HEARDOS_ROOT/data/homepage"; fi
msg "$app deployed. Public URL works once 30-vps-caddy.sh + 32-dns.py include it. Secrets: $HEARDOS_ROOT/secrets/secrets.env"
