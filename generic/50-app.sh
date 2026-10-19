#!/usr/bin/env bash
# 50-app.sh — run ON THE HOST as root:  50-app.sh <app>   (see --list: paperless mealie homeassistant homebox baikal navidrome onlyoffice nextcloud immich vaultwarden wallos actual invidious homepage calibre-web librespeed dash hortusfox archivebox listmonk paperless-gpt kiwix searxng)
# One command per app: registers it in Authentik (via 42-ak-app.py, using AUTHENTIK_TOKEN from the plan file),
# generates its secrets, renders its stack .env, creates data dirs, deploys, and runs the app's post-install.
# Public DNS and the Caddy host list are laptop-side: run 30-vps-caddy.sh and 32-dns.py once PUBLIC_HOSTS is final.
. "$(dirname "$0")/lib/common.sh"
if [ "${1:-}" = "--list" ]; then   # apps this kit can install (from catalog.json), one per line: stack<TAB>name<TAB>depends_on
  python3 -c 'import json,sys,os; c=json.load(open(sys.argv[1])); [print(a["stack"]+"\t"+a["name"]+"\t"+",".join(a.get("depends_on",[]))) for a in c["apps"] if not a.get("core") and os.path.isdir(os.path.join(sys.argv[2],a["stack"]))]' "$HERE/catalog.json" "$HERE/stacks"; exit 0
fi
[ "${1:-}" = "--dry-run" ] && { export HEARDOS_DRY_RUN=1; shift; }
require_root; load_env
app="${1:?app name}"; : "${PROXY_SUBNET:?}" "${AUTHENTIK_TOKEN:?run 40-authentik-stack.sh first}" "${TZ:=UTC}" "${MEDIA_GID:=1500}" "${LAN_HTTP:=0}"
: "${TRAEFIK_PROXY_IP:?set TRAEFIK_PROXY_IP in the plan file (the fixed Traefik address inside PROXY_SUBNET; header-trusting apps trust only it)}"
d="$HEARDOS_ROOT/stacks/$app"; [ -d "$HERE/stacks/$app" ] || die "no stack definition for '$app'"
install -d "$d"; cp "$HERE/stacks/$app/compose.yml" "$d/compose.yml"
base_env() { printf 'HEARDOS_ROOT=%s\nDOMAIN=%s\nLAN_DOMAIN=%s\nHOST_LAN_IP=%s\nPROXY_SUBNET=%s\nTRAEFIK_PROXY_IP=%s\nMEDIA_GID=%s\nTZ=%s\nADMIN_USER=%s\nADMIN_EMAIL=%s\n' "$HEARDOS_ROOT" "$DOMAIN" "$LAN_DOMAIN" "$HOST_LAN_IP" "$PROXY_SUBNET" "$TRAEFIK_PROXY_IP" "$MEDIA_GID" "$TZ" "$ADMIN_USER" "$ADMIN_EMAIL"
  local v; for v in ONLYOFFICE_MEM NEXTCLOUD_MEM IMMICH_ML_MEM OLLAMA_MEM ARCHIVEBOX_MEM; do [ -z "${!v:-}" ] || printf '%s=%s\n' "$v" "${!v}"; done; }   # optional mem_limit overrides
oidc() { # SLUG NAME LAUNCH REDIRECT... -> sets OIDC_CLIENT_ID/SECRET from secrets.env (registering on first run)
  # http:// redirects (the plain-HTTP LAN names) are registered only with LAN_HTTP=1: the kit has no LAN TLS yet, and an
  # http redirect URI hands the authorization code over the LAN in clear. 42-ak-app.py only adds URIs; drop old ones in the UI.
  local slug="$1" name="$2" launch="$3"; shift 3; local args=() u
  for u in "$@"; do case "$u" in --public) args+=(--public);; http://*) [ "$LAN_HTTP" = 1 ] && args+=(--redirect "$u");; *) args+=(--redirect "$u");; esac; done
  local key="AUTHENTIK_$(echo "$slug" | tr 'a-z-' 'A-Z_')"
  if ! grep -q "^${key}_CLIENT_ID=" "$HEARDOS_ROOT/secrets/secrets.env" 2>/dev/null; then
    HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" oidc "$slug" --name "$name" --launch "$launch" "${args[@]}" --out "$HEARDOS_ROOT/secrets/secrets.env"
  else HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" oidc "$slug" --name "$name" --launch "$launch" "${args[@]}" >/dev/null; fi
  OIDC_CLIENT_ID="$(grep "^${key}_CLIENT_ID=" "$HEARDOS_ROOT/secrets/secrets.env" | cut -d= -f2-)"; OIDC_CLIENT_SECRET="$(grep "^${key}_CLIENT_SECRET=" "$HEARDOS_ROOT/secrets/secrets.env" | cut -d= -f2-)"
}
have() { grep -E "^$1=" "$HEARDOS_ROOT/secrets/secrets.env" 2>/dev/null | head -1 | cut -d= -f2- || true; }   # read a secret, never create it
proxy() { HEARDOS_ENV="$ENV_FILE" python3 "$HERE/42-ak-app.py" proxy "$@"; }   # default: admin only; family apps pass --users all
# media_dirs DIR... — shared media folders: owner www-data (Nextcloud writes), group MEDIA_GID (heardos-media; Paperless
# runs with USERMAP_GID=MEDIA_GID so it may delete what it consumed), 2775. Navidrome only reads (other r-x).
# The first time a folder is converted (e.g. from the old 2777 layout) its contents are re-owned too.
media_dirs() {
  getent group "$MEDIA_GID" >/dev/null || groupadd -g "$MEDIA_GID" heardos-media
  local x; for x in "$@"; do
    install -d "$x"
    if [ "$(stat -c %u:%g "$x")" != "33:$MEDIA_GID" ]; then
      chown -R 33:"$MEDIA_GID" "$x"; chmod -R g+rwX,o-w "$x"; find "$x" -type d -exec chmod g+s {} +
    fi
    chmod 2775 "$x"
  done
}
pub="https://$app.$DOMAIN"
case "$app" in
  paperless)
    # x-paperless://oidc-callback + --public: the Swift Paperless iOS app runs the OIDC code exchange itself (PKCE, no secret) — Paperless >= 3.0
    oidc paperless Paperless "$pub/" "$pub/accounts/oidc/authentik/login/callback/" "x-paperless://oidc-callback" --public
    install -d "$HEARDOS_ROOT/data/paperless"/{data,media,export,postgres,redis}; media_dirs "$HEARDOS_ROOT/data/paperless/consume"
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nPAPERLESS_DB_PASSWORD=%s\nPAPERLESS_SECRET_KEY=%s\nPAPERLESS_ADMIN_PASSWORD=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret PAPERLESS_DB_PASSWORD)" "$(secret PAPERLESS_SECRET_KEY 50)" "$(secret PAPERLESS_ADMIN_PASSWORD 24)"; printf 'OCR_API_KEY=%s\nOCR_LANGS=%s\n' "$(secret OCR_API_KEY 48)" "${OCR_LANGS:-eng}"
      [ "$LAN_HTTP" = 1 ] && printf 'PAPERLESS_CSRF_LAN=,http://paperless.%s\n' "$LAN_DOMAIN"; true; } > "$d/.env" ;;
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
  trusted_proxies: [${TRAEFIK_PROXY_IP}/32]
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
    # older installs trusted the whole proxy subnet: narrow it to Traefik's /32 (restart below)
    ha_fix=""; if grep -qF "trusted_proxies: [${PROXY_SUBNET}, 127.0.0.1]" "$c/configuration.yaml"; then
      sed -i "s|^  trusted_proxies: \[${PROXY_SUBNET}, 127.0.0.1\]\$|  trusted_proxies: [${TRAEFIK_PROXY_IP}/32]|" "$c/configuration.yaml"; ha_fix=1; fi
    base_env > "$d/.env" ;;
  homebox)
    oidc homebox Homebox "$pub/" "$pub/api/v1/users/login/oidc/callback" "http://homebox.$LAN_DOMAIN/api/v1/users/login/oidc/callback"
    install -d "$HEARDOS_ROOT/data/homebox"; { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET"; } > "$d/.env" ;;
  baikal)
    proxy baikal --name "Baikal (calendar & contacts)" --host "https://dav.$DOMAIN" --launch "https://dav.$DOMAIN/admin/" --skip '^/dav\.php.*' --skip '^/card\.php.*' --skip '^/cal\.php.*' --skip '^/\.well-known/.*'
    install -d "$HEARDOS_ROOT/data/baikal"/{config,specific}; chown -R 33:33 "$HEARDOS_ROOT/data/baikal"; base_env > "$d/.env" ;;
  navidrome)
    proxy navidrome --name "Navidrome (music)" --host "https://music.$DOMAIN" --users all --skip '^/rest(/.*)?$' --skip '^/share(/.*)?$' --skip '^/backgrounds(/.*)?$'
    install -d "$HEARDOS_ROOT/data/navidrome"; chown -R 1000:1000 "$HEARDOS_ROOT/data/navidrome"; media_dirs "$HEARDOS_ROOT/media/music"; base_env > "$d/.env" ;;   # music: read-only mount
  onlyoffice)
    # gate office.$DOMAIN; skip only what the in-browser editor loads (assets, co-editing socket, signed download links).
    # Nextcloud <-> Document Server server-side calls go container-to-container (http://onlyoffice/, http://nextcloud/).
    proxy onlyoffice --name "Document editor (ONLYOFFICE)" --host "https://office.$DOMAIN" --users all --launch "https://docs.$DOMAIN/" \
      --skip '^/web-apps/.*' --skip '^/sdkjs/.*' --skip '^/sdkjs-plugins/.*' --skip '^/fonts/.*' --skip '^/dictionaries/.*' \
      --skip '^/\d+\.\d+\.\d+-\d+/.*' --skip '^/doc/.*' --skip '^/cache/files/.*' --skip '^/plugins\.json$' --skip '^/healthcheck$'
    install -d "$HEARDOS_ROOT/data/onlyoffice"/{data,logs,lib,db}
    { base_env; printf 'ONLYOFFICE_JWT_SECRET=%s\nONLYOFFICE_VERSION=9.3\n' "$(secret ONLYOFFICE_JWT_SECRET 40)"; } > "$d/.env" ;;
  nextcloud)
    pub="https://docs.$DOMAIN"; oidc nextcloud "Nextcloud (documents)" "$pub/" "$pub/apps/user_oidc/code" "http://docs.$LAN_DOMAIN/apps/user_oidc/code"
    install -d "$HEARDOS_ROOT/data/nextcloud"/{html,postgres} "$HEARDOS_ROOT/data/paperless/media/documents/originals"
    media_dirs "$HEARDOS_ROOT/media/documents" "$HEARDOS_ROOT/media/music" "$HEARDOS_ROOT/data/paperless/consume"   # www-data (33) writes, group heardos-media, 2775
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nNEXTCLOUD_DB_PASSWORD=%s\nNEXTCLOUD_ADMIN_USER=ncadmin\nNEXTCLOUD_ADMIN_PASSWORD=%s\nNEBULA_LH_OVERLAY=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$(secret NEXTCLOUD_DB_PASSWORD)" "$(secret NEXTCLOUD_ADMIN_PASSWORD 24)" "$NEBULA_LH_OVERLAY"; } > "$d/.env" ;;
  immich)
    pub="https://photos.$DOMAIN"; oidc immich "Immich (photos)" "$pub/" "$pub/auth/login" "$pub/user-settings" "$pub/api/oauth/mobile-redirect" "app.immich:///oauth-callback" "http://photos.$LAN_DOMAIN/auth/login"
    install -d "$HEARDOS_ROOT/data/immich"/{postgres,model-cache} "$HEARDOS_ROOT/media/photos"
    { base_env; printf 'IMMICH_DB_PASSWORD=%s\nIMMICH_VERSION=v3.3.3\n' "$(secret IMMICH_DB_PASSWORD)"; } > "$d/.env" ;;
  vaultwarden)
    pub="https://vault.$DOMAIN"; oidc vaultwarden "Vaultwarden (passwords)" "$pub/" "$pub/identity/connect/oidc-signin" "http://vault.$LAN_DOMAIN/identity/connect/oidc-signin"
    install -d "$HEARDOS_ROOT/data/vaultwarden"
    # admin token: plaintext stays in secrets.env (VAULTWARDEN_ADMIN_TOKEN, what you type at /admin); Vaultwarden gets only
    # its argon2id PHC hash, in data/vaultwarden/config.json (JSON, so the "$"s need no .env escaping). Rotate: change the
    # value in secrets.env, delete "admin_token" from config.json, re-run 50-app.sh vaultwarden.
    vw_cfg="$HEARDOS_ROOT/data/vaultwarden/config.json"; tok="$(secret VAULTWARDEN_ADMIN_TOKEN 48)"; vw_fix=""
    if ! python3 -c 'import json,sys; sys.exit(0 if str(json.load(open(sys.argv[1])).get("admin_token","")).startswith("$argon2") else 1)' "$vw_cfg" 2>/dev/null; then
      if dry; then msg "[dry-run] would write the argon2id admin token hash to $vw_cfg"
      else
        command -v argon2 >/dev/null || apt-get -qq install -y argon2 >/dev/null
        VW_HASH="$(printf '%s' "$tok" | argon2 "$(gen_secret 32)" -e -id -k 65540 -t 3 -p 4)" python3 - "$vw_cfg" <<'PY'
import json, os, sys
p = sys.argv[1]
try: c = json.load(open(p))
except FileNotFoundError: c = {}
c["admin_token"] = os.environ["VW_HASH"]
fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f: json.dump(c, f, indent=2)
os.replace(p + ".tmp", p)
PY
        vw_fix=1
      fi
    fi
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET"; } > "$d/.env" ;;
  wallos)
    oidc wallos "Wallos (subscriptions)" "$pub/" "$pub/" "http://wallos.$LAN_DOMAIN/"
    install -d "$HEARDOS_ROOT/data/wallos"/{db,logos}
    { base_env; printf 'BRAND_TITLE=%s\nOIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\n' "${BRAND_TITLE:-HeardOS}" "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET"; } > "$d/.env" ;;
  actual)
    pub="https://budget.$DOMAIN"; oidc actual-budget "Actual (budget)" "$pub/" "$pub/openid/callback" "http://budget.$LAN_DOMAIN/openid/callback"
    install -d "$HEARDOS_ROOT/data/actual"
    { base_env; printf 'OIDC_CLIENT_ID=%s\nOIDC_CLIENT_SECRET=%s\nNEBULA_LH_OVERLAY=%s\n' "$OIDC_CLIENT_ID" "$OIDC_CLIENT_SECRET" "$NEBULA_LH_OVERLAY"; } > "$d/.env" ;;
  invidious)
    proxy invidious --name "Invidious (videos)" --host "https://tube.$DOMAIN" --users all \
      --skip '^/api/v1/(videos|channels|search|playlists|comments|captions|trending|popular|stats|storyboards)(/.*)?$' --skip '^/feed/private.*'
    install -d "$HEARDOS_ROOT/data/invidious"/{postgres,companion-cache}; chown 10001:10001 "$HEARDOS_ROOT/data/invidious/companion-cache"
    { base_env; printf 'INVIDIOUS_DB_PASSWORD=%s\nINVIDIOUS_HMAC_KEY=%s\nINVIDIOUS_COMPANION_KEY=%s\n' "$(secret INVIDIOUS_DB_PASSWORD)" "$(secret INVIDIOUS_HMAC_KEY 32)" "$(secret INVIDIOUS_COMPANION_KEY 16)"; } > "$d/.env" ;;
  homepage)
    proxy hub --name "Family hub" --host "https://hub.$DOMAIN" --users all
    install -d "$HEARDOS_ROOT/data/homepage"; base_env > "$d/.env" ;;
  calibre-web)
    proxy calibre-web --name "Books (Calibre-Web)" --host "https://books.$DOMAIN" --users all --skip '^/opds(/.*)?$' --skip '^/kobo(/.*)?$'
    install -d "$HEARDOS_ROOT/data/calibre-web" "$HEARDOS_ROOT/media/books"; chown 1000:1000 "$HEARDOS_ROOT/data/calibre-web" "$HEARDOS_ROOT/media/books"
    base_env > "$d/.env" ;;
  librespeed)
    install -d "$HEARDOS_ROOT/data/librespeed/www"
    [ -f "$HERE/../apps/librespeed/index.html" ] && install -m 644 "$HERE/../apps/librespeed/index.html" "$HEARDOS_ROOT/data/librespeed/www/index.html"
    { base_env; printf 'LIBRESPEED_STATS_PASSWORD=%s\n' "$(secret LIBRESPEED_STATS_PASSWORD 24)"; } > "$d/.env" ;;
  dash)
    proxy dash --name "HeardOS desktop" --host "https://dash.$DOMAIN" --users all
    install -d "$HEARDOS_ROOT/data/dash"; chown 1000:1000 "$HEARDOS_ROOT/data/dash"
    if dry; then msg "[dry-run] would build heardos-dash:local from apps/dash"
    else msg "building the dashboard image"; docker build -q -t heardos-dash:local "$HERE/../apps/dash" >/dev/null; fi
    # tokens come from the post-install automation (Mealie token, Homebox API key, Actual session); empty = "not configured" card
    { base_env; c="https://cloud.$DOMAIN"
      printf 'MEALIE_PUBLIC_URL=%s\nHOMEBOX_PUBLIC_URL=%s\nACTUAL_PUBLIC_URL=%s\n' "$c/mealie" "$c/homebox" "$c/actual-budget"
      printf 'MEALIE_TOKEN=%s\nHOMEBOX_API_KEY=%s\nACTUAL_SYNC_ID=%s\nACTUAL_PASSWORD=%s\n' "$(have MEALIE_TOKEN)" "$(have HOMEBOX_API_KEY)" "$(have ACTUAL_SYNC_ID)" "$(have ACTUAL_SESSION_TOKEN)"
      # Traefik adds X-Dash-Gate on the gated route; the desktop refuses requests without it (other containers can't know it)
      printf 'DASH_GATE_SECRET=%s\n' "$(secret DASH_GATE_SECRET 48)"
      printf 'SUPPORT_EMAIL=%s\nSUPPORT_NAME=%s\n' "$ADMIN_EMAIL" "${ADMIN_NAME:-$ADMIN_USER}"; } > "$d/.env" ;;
  hortusfox)
    proxy hortusfox --name "Plants (HortusFox)" --host "https://plants.$DOMAIN" --users all --launch "https://cloud.$DOMAIN/plants"
    install -d "$HEARDOS_ROOT/data/hortusfox"/{img,attachments,logs,backup,themes,migrate,db}
    { base_env; printf 'HORTUS_ADMIN_PASSWORD=%s\nHORTUS_DB_PASSWORD=%s\nHORTUS_DB_ROOT_PASSWORD=%s\n' "$(secret HORTUS_ADMIN_PASSWORD 24)" "$(secret HORTUS_DB_PASSWORD)" "$(secret HORTUS_DB_ROOT_PASSWORD)"; } > "$d/.env" ;;
  archivebox)
    proxy archivebox --name "Saved pages (ArchiveBox)" --host "https://archive.$DOMAIN" --users all --launch "https://cloud.$DOMAIN/archive"
    install -d "$HEARDOS_ROOT/data/archivebox"
    { base_env; printf 'ARCHIVEBOX_ADMIN_PASSWORD=%s\n' "$(secret ARCHIVEBOX_ADMIN_PASSWORD 24)"; } > "$d/.env" ;;
  listmonk)
    # gate for /admin + admin API (owner only), then Listmonk's own OIDC sign-in (hidden app); public pages stay open
    proxy listmonk --name "Newsletters (Listmonk)" --host "https://news.$DOMAIN" --launch "https://cloud.$DOMAIN/news" --users "$ADMIN_USER"
    oidc listmonk-sso "Listmonk sign-in" "https://news.$DOMAIN/" "https://news.$DOMAIN/auth/oidc"
    install -d "$HEARDOS_ROOT/data/listmonk"/{uploads,db}
    { base_env; printf 'LISTMONK_ADMIN_PASSWORD=%s\nLISTMONK_DB_PASSWORD=%s\n' "$(secret LISTMONK_ADMIN_PASSWORD 24)" "$(secret LISTMONK_DB_PASSWORD)"; } > "$d/.env" ;;
  kiwix)
    # offline Wikipedia behind the gate; the ZIM (default: full English with pictures, ~119 GB) downloads in the background
    proxy wiki --name "Wikipedia (Kiwix)" --host "https://wiki.$DOMAIN" --users all --launch "https://cloud.$DOMAIN/wiki"
    install -d "$HEARDOS_ROOT/data/kiwix"
    command -v aria2c >/dev/null || { dry && msg "[dry-run] would apt-get install aria2" || apt-get -qq install -y aria2 >/dev/null; }
    free_gb=$(df -BG --output=avail "$HEARDOS_ROOT/data/kiwix" | tail -1 | tr -dc 0-9)
    [ "${free_gb:-0}" -ge "${KIWIX_MIN_FREE_GB:-130}" ] || warn "only ${free_gb} GB free under $HEARDOS_ROOT/data/kiwix — the default ZIM needs ~120 GB (set KIWIX_ZIM to a smaller one, e.g. wikipedia_en_all_nopic_*)"
    { base_env; printf 'KIWIX_ZIM=%s\n' "${KIWIX_ZIM:-wikipedia_en_all_maxi_2026-08.zim}"; } > "$d/.env" ;;
  searxng)
    # PUBLIC on purpose (browser search engine for everyone); limiter on, overlay + home LAN pass it
    install -d "$HEARDOS_ROOT/data/searxng/config"
    export BRAND_TITLE="${BRAND_TITLE:-HeardOS}" OVERLAY_CIDR="${OVERLAY_CIDR:-10.42.0.0/24}" NEBULA_LH_OVERLAY="${NEBULA_LH_OVERLAY:-10.42.0.1}" LAN_SUBNET="${LAN_SUBNET:?set LAN_SUBNET}"
    render "$HERE/../apps/searxng/settings.yml" "$HEARDOS_ROOT/data/searxng/config/settings.yml"
    render "$HERE/../apps/searxng/limiter.toml" "$HEARDOS_ROOT/data/searxng/config/limiter.toml"
    chmod 644 "$HEARDOS_ROOT/data/searxng/config"/*   # cap_drop ALL: root in the container can't read 0600 files
    { base_env; printf 'SEARXNG_SECRET=%s\n' "$(secret SEARXNG_SECRET 48)"; } > "$d/.env" ;;
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
    msg "waiting for Immich"; wait_http "http://127.0.0.1:2283/api/server/ping" 600
    IMMICH_ADMIN_PASSWORD="$(secret IMMICH_ADMIN_PASSWORD 24)" OIDC_CLIENT_SECRET="$OIDC_CLIENT_SECRET" \
      python3 "$HERE/lib/immich-setup.py" "http://127.0.0.1:2283" "$ADMIN_EMAIL" "$OIDC_CLIENT_ID" "https://auth.$DOMAIN/application/o/immich/" "$pub" ;;
  paperless)
    msg "waiting for Paperless, then creating category tags/types + workflows (lib/paperless-workflows.py)"
    # credentials go to curl as a config on stdin (-K -), not on its command line
    for i in $(seq 1 60); do printf 'user = "%s:%s"\n' "$ADMIN_USER" "$(grep '^PAPERLESS_ADMIN_PASSWORD=' "$d/.env" | cut -d= -f2-)" | curl -fs -o /dev/null -K - "http://paperless.$LAN_DOMAIN/api/ui_settings/" && break; sleep 5; done
    PAPERLESS_URL="http://paperless.$LAN_DOMAIN" PAPERLESS_ADMIN_USER="$ADMIN_USER" PAPERLESS_ADMIN_PASSWORD="$(grep '^PAPERLESS_ADMIN_PASSWORD=' "$d/.env" | cut -d= -f2-)" python3 "$HERE/lib/paperless-workflows.py" || warn "workflow bootstrap failed — re-run lib/paperless-workflows.py later" ;;
  calibre-web)
    msg "waiting for Calibre-Web (the Calibre tools install on first start, ~1-2 min), then enabling header login"
    dry || TRAEFIK_PROXY_IP="$TRAEFIK_PROXY_IP" ADMIN_USER="$ADMIN_USER" ADMIN_EMAIL="$ADMIN_EMAIL" CALIBREWEB_ADMIN_PASSWORD="$(secret CALIBREWEB_ADMIN_PASSWORD 24)" HEARDOS_ROOT="$HEARDOS_ROOT" python3 "$HERE/lib/calibre-web-setup.py" ;;
  vaultwarden) [ -n "$vw_fix" ] && ! dry && docker restart vaultwarden >/dev/null   # pick up the new config.json
    msg "admin page: https://vault.$DOMAIN/admin (token VAULTWARDEN_ADMIN_TOKEN in secrets.env); people sign in with their family account" ;;
  actual) msg "the first person to sign in to https://budget.$DOMAIN becomes the budget owner — sign in as $ADMIN_USER first" ;;
  ocr) msg "check: docker exec paperless-webserver python3 -c \"import urllib.request;print(urllib.request.urlopen('http://heardos-ocr:8000/health').read())\"; then consume a scanned PDF and confirm its text in Paperless" ;;
  onlyoffice) msg "Document Server builds fonts on first start (several minutes); check: curl -s http://office.$LAN_DOMAIN/healthcheck -> true. People only need access to Nextcloud; the office gate only guards Document Server's own pages." ;;
  nextcloud)
    msg "waiting for the Nextcloud installer"; n=0; until curl -s -m 5 "http://docs.$LAN_DOMAIN/status.php" 2>/dev/null | grep -q '"installed":true'; do sleep 10; n=$((n+1)); [ $n -gt 90 ] && die "Nextcloud did not finish installing"; done
    NC_OIDC_CLIENT_SECRET="$OIDC_CLIENT_SECRET" NC_JWT_SECRET="$(secret ONLYOFFICE_JWT_SECRET 40)" \
      bash "$HERE/lib/nc-configure.sh" "$DOMAIN" "$LAN_DOMAIN" "$OIDC_CLIENT_ID" "$TRAEFIK_PROXY_IP" "$NEBULA_LH_OVERLAY"
    msg "log in once via Authentik, then make yourself admin: docker exec -u www-data nextcloud php occ group:adduser admin $ADMIN_USER" ;;
  baikal) msg "finish Baïkal in the browser: http://dav.$LAN_DOMAIN/admin/install/  (choose DAV auth type BASIC; set an admin password; create users)" ;;
  homeassistant) [ -n "$ha_fix" ] && ! dry && docker restart homeassistant >/dev/null   # trusted_proxies narrowed
    msg "finish HA onboarding at http://home.$LAN_DOMAIN (create the owner account); OIDC login appears on the login page afterwards" ;;
  hortusfox) msg "next: 63-post-install.py hortusfox-cron (recurring watering tasks only come back via its cron endpoint)" ;;
  listmonk) msg "next: [MAIL_MODE=resend RESEND_API_KEY=… | MAIL_MODE=smtp SMTP_*=…] 63-post-install.py listmonk-config" ;;
  kiwix)
    z="$(grep '^KIWIX_ZIM=' "$d/.env" | cut -d= -f2-)"
    if [ -s "$HEARDOS_ROOT/data/kiwix/$z" ] && [ ! -f "$HEARDOS_ROOT/data/kiwix/$z.aria2" ]; then msg "$z already present"
    elif dry; then msg "[dry-run] would start kiwix-fetch (aria2 download of $z)"
    elif systemctl is-active -q kiwix-fetch; then msg "kiwix-fetch already running"
    else systemctl reset-failed kiwix-fetch 2>/dev/null || true
      systemd-run -q --unit=kiwix-fetch sh "$HERE/../apps/kiwix/fetch.sh" "$HEARDOS_ROOT/data/kiwix" "$z"
      msg "downloading $z in the background (unit kiwix-fetch; progress: journalctl -fu kiwix-fetch). The wiki starts by itself once it is verified."
    fi ;;
  searxng) msg "set https://search.$DOMAIN as the browser's search engine (URL: https://search.$DOMAIN/search?q=%s)" ;;
  paperless-gpt) msg "next: 63-post-install.py paperless-gpt (pulls the model, adds the Paperless workflow, queues existing documents)" ;;
esac
if [ -f "$HEARDOS_ROOT/stacks/homepage/compose.yml" ]; then DOMAIN="$DOMAIN" HEARDOS_ROOT="$HEARDOS_ROOT" BRAND_TITLE="${BRAND_TITLE:-}" BRAND_COLOR="${BRAND_COLOR:-}" python3 "$HERE/lib/homepage-config.py" "$HEARDOS_ROOT/data/homepage"; fi
msg "$app deployed. Public URL works once 30-vps-caddy.sh + 32-dns.py include it. Secrets: $HEARDOS_ROOT/secrets/secrets.env"
