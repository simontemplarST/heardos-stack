#!/usr/bin/env python3
"""cfdns-add.py NAME [NAME...] — create unproxied A records <name>.example.com -> Nebula lighthouse (203.0.113.10).
Uses the zone-scoped token embedded in /root/.cloudflared/cert.pem (same trick as cfadd.py, 2026-10-11)."""
import base64, json, re, sys, urllib.request
LH="203.0.113.10"
pem=open("/root/.cloudflared/cert.pem").read()
tok=json.loads(base64.b64decode("".join(re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END",pem,re.S).group(1).split())))
zone,api=tok["zoneID"],tok["apiToken"]
def cf(m,p,d=None):
    r=urllib.request.Request("https://api.cloudflare.com/client/v4"+p,method=m,data=json.dumps(d).encode() if d else None,headers={"Authorization":"Bearer "+api,"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=30) as x: return json.load(x)
for n in sys.argv[1:]:
    name=n if n.endswith(".example.com") else n+".example.com"
    ex=cf("GET",f"/zones/{zone}/dns_records?name={name}")["result"]
    print(name,"existing:",[(r["type"],r["content"],r["proxied"]) for r in ex])
    if not any(r["type"]=="A" and r["content"]==LH for r in ex):
        print("  created:",cf("POST",f"/zones/{zone}/dns_records",{"type":"A","name":name,"content":LH,"ttl":300,"proxied":False,"comment":"nebula lighthouse ingress 2026-10-12"})["success"])
