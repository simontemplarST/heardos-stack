#!/usr/bin/env python3
"""cfdns-txt.py NAME 'content' [NAME 'content' ...] — create/replace a TXT record in example.com (same token source as cfdns-add.py).
   cfdns-txt.py --delete-a NAME  — delete every A record for NAME (any target), e.g. a dangling host."""
import base64, json, re, sys, urllib.request
pem=open("/root/.cloudflared/cert.pem").read()
tok=json.loads(base64.b64decode("".join(re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END",pem,re.S).group(1).split())))
zone,api=tok["zoneID"],tok["apiToken"]
def cf(m,p,d=None):
    r=urllib.request.Request("https://api.cloudflare.com/client/v4"+p,method=m,data=json.dumps(d).encode() if d else None,headers={"Authorization":"Bearer "+api,"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=30) as x: return json.load(x)
fq=lambda n: n if n.endswith(".example.com") or n=="example.com" else n+".example.com"
args=sys.argv[1:]
if args and args[0]=="--delete-a":
    for n in args[1:]:
        for rec in cf("GET",f"/zones/{zone}/dns_records?name={fq(n)}&type=A")["result"]:
            print(fq(n),"delete A",rec["content"],cf("DELETE",f"/zones/{zone}/dns_records/{rec['id']}")["success"])
    sys.exit()
for n,content in zip(args[::2],args[1::2]):
    name=fq(n); ex=[r for r in cf("GET",f"/zones/{zone}/dns_records?name={name}&type=TXT")["result"]]
    same=[r for r in ex if r["content"].strip('"')==content]
    if same: print(name,"TXT already present"); continue
    for r in ex:
        if r["content"].strip('"').split(";")[0].split("=")[0].strip().lower()==content.split(";")[0].split("=")[0].strip().lower():
            print(name,"replacing",r["content"][:40],cf("PUT",f"/zones/{zone}/dns_records/{r['id']}",{"type":"TXT","name":name,"content":content,"ttl":300})["success"]); break
    else: print(name,"created:",cf("POST",f"/zones/{zone}/dns_records",{"type":"TXT","name":name,"content":content,"ttl":300,"comment":"security baseline 2026-10-15"})["success"])
