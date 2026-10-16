#!/usr/bin/env bash
# 90-verify.sh — run ON THE LAPTOP. Automated version of the final checklist. Exit status 0 = all green.
. "$(dirname "$0")/lib/common.sh"; load_env
UA='Mozilla/5.0 (X11; Linux x86_64) Firefox/131.0'; fail=0
ok()   { printf '  \033[1;32mOK\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fail=1; }
code() { curl -s -o /dev/null -m 15 -A "$UA" -w '%{http_code}' "$@" 2>/dev/null || echo 000; }

msg "edge and certificates"
for h in $PUBLIC_HOSTS; do
  c="$(code "https://$h.$DOMAIN/")"; iss="$(echo | openssl s_client -connect "$h.$DOMAIN:443" -servername "$h.$DOMAIN" 2>/dev/null | openssl x509 -noout -issuer 2>/dev/null | grep -oE "O ?= ?[^,/]+" | head -1)"
  case "$c" in 2??|3??) ok "$h.$DOMAIN -> $c ($iss)";; *) bad "$h.$DOMAIN -> $c";; esac
done
curl -sI -m 10 "https://auth.$DOMAIN/" | grep -qi strict-transport-security && ok "HSTS present" || bad "HSTS header missing"
[ "$(code "http://auth.$DOMAIN/")" = 308 ] && ok "http redirects to https" || bad "http->https redirect"

msg "authentik"
comp="$(curl -s -m 15 -A "$UA" -H 'Accept: application/json' "https://auth.$DOMAIN/api/v3/flows/executor/default-authentication-flow/?query=" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("component",""))' 2>/dev/null)"
[ "$comp" = ak-stage-identification ] && ok "login flow starts for anonymous users" || bad "login flow: $comp (reputation binding not negated?)"
if [ -n "${AUTHENTIK_TOKEN:-}" ]; then
  open_apps="$(python3 - "$AUTHENTIK_URL" "$AUTHENTIK_TOKEN" <<'PY'
import sys,json,urllib.request
u,t=sys.argv[1:3]
def g(p): return json.load(urllib.request.urlopen(urllib.request.Request(u+"/api/v3"+p,headers={"Authorization":"Bearer "+t})))["results"]
print(" ".join(a["slug"] for a in g("/core/applications/?page_size=100") if not g(f"/policies/bindings/?target={a['pk']}")))
PY
)"
  [ -z "$open_apps" ] && ok "every application has a group binding" || bad "applications open to all users: $open_apps"
fi

msg "header-login apps must not accept a spoofed header on LAN names"
for h in music; do
  body="$(curl -s -m 10 -H 'X-authentik-username: anyone' "http://$h.$LAN_DOMAIN/app/" 2>/dev/null)"
  echo "$body" | grep -q '"auth":{"id"' && bad "$h.$LAN_DOMAIN logs in with a spoofed header" || ok "$h.$LAN_DOMAIN strips the header"
done

msg "overlay"
ping -c 2 -W 2 "$NEBULA_LH_OVERLAY" >/dev/null 2>&1 && ok "lighthouse reachable over Nebula" || bad "no overlay path to the lighthouse (is this laptop enrolled?)"
ping -c 2 -W 2 "$HOST_OVERLAY" >/dev/null 2>&1 && ok "host reachable over Nebula" || bad "host not reachable over Nebula"

msg "vps ssh"
out="$(ssh -o BatchMode=yes -o ConnectTimeout=8 -o PubkeyAuthentication=no -o PreferredAuthentications=password "root@$NEBULA_LH_IP" true 2>&1 | tail -1)"
echo "$out" | grep -q 'publickey' && ok "password SSH refused on the VPS" || bad "VPS password SSH not refused: $out"

msg "host (over ssh)"
ssh -o BatchMode=yes -o ConnectTimeout=8 "root@$HOST_LAN_IP" 'docker ps --format "{{.Names}} {{.Status}}" | grep -ciE "unhealthy|restarting"' 2>/dev/null | grep -q '^0$' && ok "no unhealthy containers" || bad "unhealthy/restarting containers on the host"
ssh -o BatchMode=yes -o ConnectTimeout=8 "root@$HOST_LAN_IP" 'systemctl is-active heardos-backup.timer heardos-feeds.timer' 2>/dev/null | grep -vq inactive && ok "backup and feed timers active" || bad "backup/feed timers missing"

[ $fail = 0 ] && msg "all checks passed" || { warn "some checks failed"; exit 1; }
