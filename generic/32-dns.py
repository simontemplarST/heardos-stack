#!/usr/bin/env python3
"""32-dns.py — upsert unproxied A records for every PUBLIC_HOST at the VPS IP (Cloudflare API).
usage: 32-dns.py            (reads heardos.env next to this script, or $HEARDOS_ENV)
       32-dns.py --delete NAME   remove <NAME>.<DOMAIN>
Needs CF_API_TOKEN (zone-scoped, DNS:Edit) and CF_ZONE_ID. Other DNS hosts: create the same A records by hand."""
import json, os, sys, urllib.request, urllib.error

here = os.path.dirname(os.path.abspath(__file__))
envf = os.environ.get("HEARDOS_ENV", os.path.join(here, "heardos.env"))
env = {}
for line in open(envf):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k] = v.split(" #")[0].strip().strip('"')
TOK, ZONE, DOMAIN, IP = env["CF_API_TOKEN"], env["CF_ZONE_ID"], env["DOMAIN"], env["NEBULA_LH_IP"]
if not (TOK and ZONE and IP): raise SystemExit("set CF_API_TOKEN, CF_ZONE_ID and NEBULA_LH_IP in the plan file")

def cf(method, path, data=None):
    r = urllib.request.Request("https://api.cloudflare.com/client/v4" + path, method=method,
                               data=json.dumps(data).encode() if data else None,
                               headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as x: return json.load(x)
    except urllib.error.HTTPError as e: raise SystemExit(f"cloudflare {method} {path}: {e.code} {e.read().decode()[:200]}")

recs = cf("GET", f"/zones/{ZONE}/dns_records?per_page=500")["result"]
if len(sys.argv) == 3 and sys.argv[1] == "--delete":
    name = f"{sys.argv[2]}.{DOMAIN}"
    for r in recs:
        if r["name"] == name: print("delete", r["type"], name, cf("DELETE", f"/zones/{ZONE}/dns_records/{r['id']}")["success"])
    sys.exit(0)
for h in env["PUBLIC_HOSTS"].split():
    name = f"{h}.{DOMAIN}"; cur = [r for r in recs if r["name"] == name]
    ok = [r for r in cur if r["type"] == "A" and r["content"] == IP and not r["proxied"]]
    if ok: print(f"ok      {name} -> {IP}"); continue
    for r in cur:
        if r["type"] in ("A", "AAAA", "CNAME"): cf("DELETE", f"/zones/{ZONE}/dns_records/{r['id']}"); print(f"removed {name} {r['type']} {r['content']}")
    cf("POST", f"/zones/{ZONE}/dns_records", {"type": "A", "name": name, "content": IP, "ttl": 300, "proxied": False, "comment": "heardos edge"})
    print(f"created {name} -> {IP}")
