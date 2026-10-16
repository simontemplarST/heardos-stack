#!/usr/bin/env bash
# security-check.sh — read-only posture check for HeardOS (VPS edge, prox, CT 108, Authentik, DNS, laptop).
# Prints one line per check: PASS / WARN / INFO. Needs: ssh to nebula-lh, root@192.168.10.2, root@192.168.10.20
# (LAN or the Nebula LAN route) and AUTHENTIK_TOKEN in ~/.config/credentials.env. Changes nothing.
# Each remote block prints exactly one line per check (in order); lines are read back positionally.
set -uo pipefail
set -a; . ~/.config/credentials.env 2>/dev/null; set +a
LH=nebula-lh; PROX=root@192.168.10.2; CT=root@192.168.10.20
S="ssh -o BatchMode=yes -o ConnectTimeout=8"
pass(){ printf "PASS  %s\n" "$*"; } ; warn(){ printf "WARN  %s\n" "$*"; } ; info(){ printf "INFO  %s\n" "$*"; }
chk(){ if [[ "${3:-}" =~ $2 ]]; then pass "$1: $3"; else warn "$1: '${3:-}' (want: $2)"; fi; }
SSHD='sshd -T 2>/dev/null | grep -E "^(passwordauthentication|permitrootlogin) " | sort | tr "\n" " "'
SSHD_OK="^passwordauthentication no permitrootlogin without-password $"

echo "== VPS (nebula-lh) =="
if mapfile -t L < <($S $LH "$SSHD; echo
ss -ltn | awk 'NR>1{print \$4}' | grep ':22\$' | tr '\n' ' '; echo
ufw status | grep -c '22/tcp on nebula1'
[ -f /var/run/reboot-required ] && echo reboot-required || echo no-reboot
systemctl is-active caddy nebula fail2ban unattended-upgrades | tr '\n' ' '; echo
apt list --upgradable 2>/dev/null | grep -c upgradable
ss -ltnp | grep -c '127.0.0.1:2019'" 2>/dev/null) && [ "${#L[@]}" -ge 7 ]; then
  chk "sshd" "$SSHD_OK" "${L[0]}"
  chk "ssh bound to overlay only" "^10\.42\.0\.1:22 $" "${L[1]}"
  chk "ufw: ssh only on nebula1" "^[1-9]" "${L[2]}"
  chk "no pending reboot" "^no-reboot$" "${L[3]}"
  chk "caddy nebula fail2ban unattended" "^active active active active $" "${L[4]}"
  chk "pending upgrades == 0" "^0$" "${L[5]}"
  chk "caddy admin on loopback" "^1$" "${L[6]}"
  h=$(curl -s -o /dev/null -D - -m 10 https://auth.example.com/ | tr -d "\r" | grep -i -E "^(strict-transport-security|x-content-type-options|referrer-policy|server):" | cut -d: -f1 | tr "A-Z" "a-z" | sort | tr "\n" ",")
  chk "edge headers (hsts,nosniff,referrer; no server)" "^referrer-policy,strict-transport-security,x-content-type-options,$" "$h"
else warn "VPS unreachable over ssh"; fi

echo "== prox =="
if mapfile -t L < <($S $PROX "$SSHD; echo
systemctl is-active rpcbind.socket
systemctl is-active unattended-upgrades
[ -f /var/run/reboot-required ] && echo reboot-required || echo no-reboot
apt-get update 2>&1 | grep -c -E '^(Err|W:)'
apt list --upgradable 2>/dev/null | grep -c upgradable
ss -ltn | awk 'NR>1 && \$4 !~ /^(127\.|\[::1\])/ {print \$4}' | sed 's/.*://' | sort -un | tr '\n' ' '; echo
grep -c '^unprivileged: 1' /etc/pve/lxc/108.conf" 2>/dev/null) && [ "${#L[@]}" -ge 8 ]; then
  chk "sshd" "$SSHD_OK" "${L[0]}"
  chk "rpcbind off" "^inactive$" "${L[1]}"
  chk "unattended-upgrades" "^active$" "${L[2]}"
  chk "no pending reboot" "^no-reboot$" "${L[3]}"
  chk "apt sources clean (0 errors)" "^0$" "${L[4]}"
  chk "pending upgrades == 0" "^0$" "${L[5]}"
  info "listening ports: ${L[6]}"; case " ${L[6]}" in *" 111 "*) warn "rpcbind port 111 is listening";; esac
  chk "CT 108 unprivileged" "^1$" "${L[7]}"
else warn "prox unreachable over ssh"; fi

echo "== CT 108 =="
if mapfile -t L < <($S $CT "$SSHD; echo
grep -A2 '^api:' /root/runtipi/traefik/traefik.yml | grep -c 'insecure: false'
grep -c '^ALLOW_ERROR_MONITORING=false' /root/runtipi/.env
apt list --upgradable 2>/dev/null | grep -c upgradable
docker ps -a --format '{{.Names}} {{.Status}}' | grep -v -E ' Up ' | tr '\n' ';'; echo
ss -ltn | awk 'NR>1 && \$4 !~ /^(127\.|\[::1\])/ {print \$4}' | sed 's/.*://' | sort -un | tr '\n' ' '; echo" 2>/dev/null) && [ "${#L[@]}" -ge 6 ]; then
  chk "sshd" "$SSHD_OK" "${L[0]}"
  chk "traefik api insecure=false" "^1$" "${L[1]}"
  chk "runtipi telemetry off" "^1$" "${L[2]}"
  chk "pending upgrades == 0" "^0$" "${L[3]}"
  if [ -z "${L[4]}" ]; then pass "all containers Up"; else warn "containers not Up: ${L[4]}"; fi
  info "LAN host ports: ${L[5]}"
  for p in 6556 9925 8012 7745; do case " ${L[5]}" in *" $p "*) warn "LAN port $p still published (baseline: closed)";; esac; done
else warn "CT 108 unreachable over ssh"; fi

echo "== Authentik =="
if [ -n "${AUTHENTIK_TOKEN:-}" ]; then
python3 - <<'EOF'
import os, json, urllib.request
A="https://auth.example.com/api/v3"; H={"Authorization":"Bearer "+os.environ["AUTHENTIK_TOKEN"]}
def g(p):
    with urllib.request.urlopen(urllib.request.Request(A+p,headers=H),timeout=60) as x: return json.load(x)
def pas(s): print("PASS ",s)
def wrn(s): print("WARN ",s)
users=g("/core/users/?page_size=50")["results"]
devs={}
for kind in ("webauthn","totp","static","duo","sms","email"):
    try:
        for d in g("/authenticators/admin/%s/?page_size=100"%kind)["results"]:
            u=d.get("user"); u=u.get("username") if isinstance(u,dict) else u; devs[u]=devs.get(u,0)+1
    except Exception: pass
for u in users:
    if u["username"]=="akadmin" and u["type"]=="internal": wrn("akadmin is an interactive (internal) user again — should be service_account with an unusable password")
    if u["type"]!="internal": continue
    n=devs.get(u["username"],0)
    if u["is_superuser"] and n==0: wrn("superuser %s has NO MFA device" % u["username"])
    elif u["is_superuser"]: pas("superuser %s has %d MFA device(s)" % (u["username"],n))
    elif n==0: wrn("user %s has no MFA device (last login %s)" % (u["username"],(u.get("last_login") or "never")[:10]))
    if u["username"]=="akadmin" and u["is_active"]: wrn("akadmin still active (default superuser)")
for t in g("/core/tokens/?page_size=50")["results"]:
    if t["intent"]=="api" and not t["expiring"] and not t["user_obj"]["username"].startswith("ak-outpost"): wrn("API token %s (%s) never expires" % (t["identifier"],t["user_obj"]["username"]))
apps=g("/core/applications/?page_size=100&superuser_full_list=true")["results"]
bound={b["target"] for b in g("/policies/bindings/?page_size=500")["results"]}
openapps=[a["slug"] for a in apps if a["pk"] not in bound]
(wrn if openapps else pas)("applications without a policy binding: %s" % (openapps or "none"))
p=[p for p in g("/policies/password/")["results"] if p["name"]=="default-password-change-password-policy"][0]
(pas if p["length_min"]>=12 and p["check_have_i_been_pwned"] else wrn)("password policy min=%s hibp=%s zxcvbn=%s" % (p["length_min"],p["check_have_i_been_pwned"],p.get("zxcvbn_score_threshold")))
bad=[]
for pr in g("/providers/oauth2/?page_size=100")["results"]:
    for r in pr.get("redirect_uris") or []:
        u=r["url"] if isinstance(r,dict) else r
        if u.startswith("http://") and ".example.com" in u: bad.append(pr["name"]+": "+u)
    if pr["client_type"]=="public" and pr["name"]!="paperless": bad.append(pr["name"]+": public client")
(wrn if bad else pas)("provider hygiene: %s" % (bad or "ok"))
EOF
else warn "AUTHENTIK_TOKEN not set"; fi

echo "== DNS =="
for n in example.com _dmarc.example.com; do t=$(dig +short TXT "$n" @1.1.1.1 | tr -d '"' | tr "\n" " "); case "$t" in *spf1*|*DMARC1*) pass "$n TXT: $t";; *) warn "$n TXT missing: '$t'";; esac; done
$S $CT 'python3 - <<PY
import base64,json,re,urllib.request
pem=open("/root/.cloudflared/cert.pem").read()
tok=json.loads(base64.b64decode("".join(re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END",pem,re.S).group(1).split())))
r=urllib.request.Request("https://api.cloudflare.com/client/v4/zones/%s/dns_records?per_page=200"%tok["zoneID"],headers={"Authorization":"Bearer "+tok["apiToken"]})
recs=json.load(urllib.request.urlopen(r,timeout=30))["result"]
known={"example.com":"github pages","dco.example.com":"vps-provider dco","forge.example.com":"vps-provider forge"}
odd=[(x["name"],x["content"]) for x in recs if x["type"]=="A" and x["content"]!="203.0.113.10" and x["name"] not in known]
print(("WARN  A records at unknown targets (dangling?): %s" % odd) if odd else "PASS  every A record is the lighthouse or a known host (%s)" % ", ".join(known))
PY' 2>/dev/null || warn "could not list the DNS zone from CT 108"

echo "== laptop =="
for f in ~/.config/credentials.env ~/.config/nebula-ca/ca.key; do m=$(stat -c %a "$f" 2>/dev/null); chk "mode $f" "^600$" "${m:-missing}"; done
if grep -rqE "(PASSWORD|SECRET|TOKEN)=[A-Za-z0-9+/]{16,}$" ~/heardos-install --include=*.sh --include=*.py --include=*.yml --include=*.env 2>/dev/null; then warn "possible literal secret in the kit: $(grep -rlE '(PASSWORD|SECRET|TOKEN)=[A-Za-z0-9+/]{16,}$' ~/heardos-install | head -3 | tr '\n' ' ')"; else pass "no literal secrets in the kit"; fi
