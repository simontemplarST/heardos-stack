#!/usr/bin/env bash
# resend-dns.sh — publish the DNS records Resend requires for example.com, then ask Resend to verify.
#
#   bash resend-dns.sh [--apply]       needs a FULL-ACCESS Resend key in RESEND_ADMIN_KEY (credentials.env): fetches the
#                                      records from the API (creating the domain if needed), publishes them, triggers verify
#   bash resend-dns.sh records.json [--apply]
#                                      publish records pasted from the Resend dashboard (Domains -> example.com -> the JSON
#                                      shown by "API" / or build it as [{"type":"TXT","name":"resend._domainkey","value":"p=..."},
#                                      {"type":"MX","name":"send","value":"feedback-smtp.us-east-1.amazonses.com","priority":10},
#                                      {"type":"TXT","name":"send","value":"v=spf1 include:amazonses.com ~all"}])
#
# 2026-10-19: publishes from this laptop with CF_API_TOKEN (zone-scoped DNS:Edit, credentials.env) via edge/dns/cfapi.py,
# no longer through CT 108's tunnel cert.pem. Without --apply it prints the DNS plan and stops; records it overwrites are
# printed as JSON first. Idempotent. Reads only RESEND_ADMIN_KEY, RESEND_API_KEY and CF_* from credentials.env, and
# keys go to curl through 0600 header files, never on a command line.
# The apex SPF (v=spf1 -all) and DMARC (p=reject adkim=s aspf=s) stay: Resend signs DKIM with d=example.com, which satisfies
# strict DMARC alignment; the send.* return path only needs its own SPF record.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# cred NAME... — read just these keys from credentials.env into shell (not exported) variables
cred() { local k v; for k; do v="$(grep -E "^(export )?$k=" ~/.config/credentials.env 2>/dev/null | tail -1 | cut -d= -f2-)" || true
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"; printf -v "$k" '%s' "$v"; done; }
cred RESEND_ADMIN_KEY RESEND_API_KEY
APPLY=""; ARG=""; for a in "$@"; do [ "$a" = --apply ] && APPLY=--apply || ARG="$a"; done
UA="Mozilla/5.0 (X11; Linux x86_64) heardos-setup"   # Resend's API is behind Cloudflare, which 403s the default python UA
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
( umask 077; printf 'Authorization: Bearer %s\n' "$RESEND_ADMIN_KEY" > "$T/admin.h"; printf 'Authorization: Bearer %s\n' "$RESEND_API_KEY" > "$T/send.h" )
if [ -n "$ARG" ]; then cp "$ARG" "$T/records.json"; DOMAIN_ID=""
else
  [ -n "$RESEND_ADMIN_KEY" ] || { echo "need RESEND_ADMIN_KEY (full access) in credentials.env, or pass a records.json" >&2; exit 1; }
  R(){ curl -s -m 30 -A "$UA" -H @"$T/admin.h" -H "Content-Type: application/json" "$@"; }
  DOMAIN_ID=$(R https://api.resend.com/domains | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((x["id"] for x in d.get("data",[]) if x["name"]=="example.com"),""))')
  [ -n "$DOMAIN_ID" ] || DOMAIN_ID=$(R -X POST -d '{"name":"example.com","region":"us-east-1"}' https://api.resend.com/domains | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  R "https://api.resend.com/domains/$DOMAIN_ID" | python3 -c 'import json,sys; d=json.load(sys.stdin); json.dump([{"type":r["type"],"name":r["name"],"value":r["value"],"priority":r.get("priority"),"ttl":r.get("ttl")} for r in d["records"]],sys.stdout)' > "$T/records.json"
fi
echo "records to publish:"; python3 -c 'import json,sys; [print("  %-5s %-28s %s" % (r["type"],r["name"],r["value"][:80])) for r in json.load(open(sys.argv[1]))]' "$T/records.json"
python3 - "$T/records.json" "$HERE/../edge/dns" $APPLY <<'PY'
import json, os, sys
sys.path.insert(0, sys.argv[2])
from cfapi import create, done, records, replace
for r in json.load(open(sys.argv[1])):
    name = r["name"] if r["name"].endswith("example.com") else r["name"] + ".example.com"
    body = {"type": r["type"], "name": name, "content": r["value"], "ttl": 300, "proxied": False, "comment": "resend 2026-10"}
    if r["type"] == "MX": body["priority"] = int(r.get("priority") or 10)
    ex = records(name, r["type"])
    if [x for x in ex if x["content"].strip('"') == r["value"]]: print("exists ", r["type"], name); continue
    # only the send.* SPF TXT and the MX are ever overwritten (one of each per name); anything else is added alongside
    if ex and ((r["type"] == "TXT" and name.startswith("send.")) or r["type"] == "MX"): replace(ex[0], body)
    else: create(body)
done()
PY
[ -n "$APPLY" ] || exit 0
if [ -n "$DOMAIN_ID" ]; then
  sleep 20; R -X POST "https://api.resend.com/domains/$DOMAIN_ID/verify" >/dev/null
  for i in 1 2 3 4 5 6; do sleep 20; st=$(R "https://api.resend.com/domains/$DOMAIN_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'); echo "resend status: $st"; [ "$st" = "verified" ] && break; done
else echo "now click Verify in the Resend dashboard (DNS propagates in ~1 min)"; fi
echo "test send:"; curl -s -m 30 -A "$UA" -H @"$T/send.h" -H "Content-Type: application/json" -X POST https://api.resend.com/emails -d '{"from":"HeardOS <authentik@example.com>","to":["admin@example.net"],"subject":"HeardOS mail is live","text":"example.com is verified in Resend; Authentik recovery and sign-up mail will arrive from this address."}'; echo
