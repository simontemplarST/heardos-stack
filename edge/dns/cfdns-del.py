#!/usr/bin/env python3
"""cfdns-del.py NAME [NAME...] — delete the unproxied A record <name>.example.com -> 203.0.113.10 (same token source as cfdns-add.py)."""
import base64, json, re, sys, urllib.request
LH="203.0.113.10"
pem=open("/root/.cloudflared/cert.pem").read()
tok=json.loads(base64.b64decode("".join(re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END",pem,re.S).group(1).split())))
zone,api=tok["zoneID"],tok["apiToken"]
def cf(m,p):
    r=urllib.request.Request("https://api.cloudflare.com/client/v4"+p,method=m,headers={"Authorization":"Bearer "+api,"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=30) as x: return json.load(x)
for n in sys.argv[1:]:
    name=n if n.endswith(".example.com") else n+".example.com"
    for rec in cf("GET",f"/zones/{zone}/dns_records?name={name}")["result"]:
        if rec["type"]=="A" and rec["content"]==LH: print(name, "deleted:", cf("DELETE",f"/zones/{zone}/dns_records/{rec['id']}")["success"])
    print(name, "remaining:", [(r["type"],r["content"]) for r in cf("GET",f"/zones/{zone}/dns_records?name={name}")["result"]])
