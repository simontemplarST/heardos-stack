#!/usr/bin/env bash
# resend-dns.sh — publish the DNS records Resend requires for example.com, then ask Resend to verify.
#
#   bash resend-dns.sh                 needs a FULL-ACCESS Resend key in RESEND_ADMIN_KEY (credentials.env): fetches the
#                                      records from the API (creating the domain if needed), publishes them, triggers verify
#   bash resend-dns.sh records.json    publish records pasted from the Resend dashboard (Domains -> example.com -> the JSON
#                                      shown by "API" / or build it as [{"type":"TXT","name":"resend._domainkey","value":"p=..."},
#                                      {"type":"MX","name":"send","value":"feedback-smtp.us-east-1.amazonses.com","priority":10},
#                                      {"type":"TXT","name":"send","value":"v=spf1 include:amazonses.com ~all"}])
#
# Publishing goes through CT 108 with the tunnel cert.pem token (the only token that can edit the zone). Idempotent.
# The apex SPF (v=spf1 -all) and DMARC (p=reject adkim=s aspf=s) stay: Resend signs DKIM with d=example.com, which satisfies
# strict DMARC alignment; the send.* return path only needs its own SPF record.
set -euo pipefail
set -a; . ~/.config/credentials.env; set +a
UA="Mozilla/5.0 (X11; Linux x86_64) heardos-setup"   # Resend's API is behind Cloudflare, which 403s the default python UA
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
if [ -n "${1:-}" ]; then cp "$1" "$T/records.json"; DOMAIN_ID=""
else
  : "${RESEND_ADMIN_KEY:?need RESEND_ADMIN_KEY (full access) in credentials.env, or pass a records.json}"
  R(){ curl -s -m 30 -A "$UA" -H "Authorization: Bearer $RESEND_ADMIN_KEY" -H "Content-Type: application/json" "$@"; }
  DOMAIN_ID=$(R https://api.resend.com/domains | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((x["id"] for x in d.get("data",[]) if x["name"]=="example.com"),""))')
  [ -n "$DOMAIN_ID" ] || DOMAIN_ID=$(R -X POST -d '{"name":"example.com","region":"us-east-1"}' https://api.resend.com/domains | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  R "https://api.resend.com/domains/$DOMAIN_ID" | python3 -c 'import json,sys; d=json.load(sys.stdin); json.dump([{"type":r["type"],"name":r["name"],"value":r["value"],"priority":r.get("priority"),"ttl":r.get("ttl")} for r in d["records"]],sys.stdout)' > "$T/records.json"
fi
echo "records to publish:"; python3 -c 'import json,sys; [print("  %-5s %-28s %s" % (r["type"],r["name"],r["value"][:80])) for r in json.load(open(sys.argv[1]))]' "$T/records.json"
scp -q "$T/records.json" root@192.168.10.20:/root/resend-records.json
ssh -o BatchMode=yes root@192.168.10.20 'python3 - <<PY
import base64, json, re, urllib.request
pem=open("/root/.cloudflared/cert.pem").read()
tok=json.loads(base64.b64decode("".join(re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END",pem,re.S).group(1).split())))
zone,api=tok["zoneID"],tok["apiToken"]
def cf(m,p,d=None):
    r=urllib.request.Request("https://api.cloudflare.com/client/v4"+p,method=m,data=json.dumps(d).encode() if d else None,headers={"Authorization":"Bearer "+api,"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=30) as x: return json.load(x)
for r in json.load(open("/root/resend-records.json")):
    name=r["name"] if r["name"].endswith("example.com") else r["name"]+".example.com"
    body={"type":r["type"],"name":name,"content":r["value"],"ttl":300,"proxied":False,"comment":"resend 2026-10"}
    if r["type"]=="MX": body["priority"]=int(r.get("priority") or 10)
    ex=[x for x in cf("GET","/zones/%s/dns_records?name=%s&type=%s"%(zone,name,r["type"]))["result"]]
    same=[x for x in ex if x["content"].strip("\"")==r["value"]]
    if same: print("exists ", r["type"], name); continue
    if ex and r["type"]=="TXT" and name.startswith("send.") or (ex and r["type"]=="MX"):
        print("update ", r["type"], name, cf("PUT","/zones/%s/dns_records/%s"%(zone,ex[0]["id"]),body)["success"])
    else:
        print("create ", r["type"], name, cf("POST","/zones/%s/dns_records"%zone,body)["success"])
PY
rm -f /root/resend-records.json'
if [ -n "$DOMAIN_ID" ]; then
  sleep 20; R -X POST "https://api.resend.com/domains/$DOMAIN_ID/verify" >/dev/null
  for i in 1 2 3 4 5 6; do sleep 20; st=$(R "https://api.resend.com/domains/$DOMAIN_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'); echo "resend status: $st"; [ "$st" = "verified" ] && break; done
else echo "now click Verify in the Resend dashboard (DNS propagates in ~1 min)"; fi
echo "test send:"; curl -s -m 30 -A "$UA" -H "Authorization: Bearer $RESEND_API_KEY" -H "Content-Type: application/json" -X POST https://api.resend.com/emails -d '{"from":"HeardOS <authentik@example.com>","to":["admin@example.net"],"subject":"HeardOS mail is live","text":"example.com is verified in Resend; Authentik recovery and sign-up mail will arrive from this address."}'; echo
