import base64, json, re, sys, urllib.request, urllib.error
pem=open("/root/.cloudflared/cert.pem").read(); tok=json.loads(base64.b64decode("".join(re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END",pem,re.S).group(1).split())))
zone,api=tok["zoneID"],tok["apiToken"]; LH=sys.argv[1]; hosts=sys.argv[2].split(); do=len(sys.argv)>3 and sys.argv[3]=="--apply"
def cf(m,p,d=None):
    r=urllib.request.Request("https://api.cloudflare.com/client/v4"+p,method=m,data=json.dumps(d).encode() if d else None,headers={"Authorization":"Bearer "+api,"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(r,timeout=30) as x: return json.load(x)
    except urllib.error.HTTPError as e: return {"success":False,"status":e.code,"body":e.read().decode()[:200]}
recs=cf("GET",f"/zones/{zone}/dns_records?per_page=200")["result"]
tun=[r for r in recs if r["type"]=="CNAME" and r["content"].endswith(".cfargotunnel.com")]
print("tunnel CNAMEs in zone:",[(r["name"],r["proxied"]) for r in tun])
for h in hosts:
    name=f"{h}.example.com"; cur=[r for r in recs if r["name"]==name]
    print(f"  {name}: "+", ".join(f'{r["type"]} {r["content"][:40]} proxied={r["proxied"]}' for r in cur))
    if not do: continue
    for r in cur:
        if r["type"] in ("CNAME","A","AAAA"): print("    delete",r["type"],cf("DELETE",f"/zones/{zone}/dns_records/{r['id']}").get("success"))
    print("    create A ->",LH,cf("POST",f"/zones/{zone}/dns_records",{"type":"A","name":name,"content":LH,"ttl":300,"proxied":False,"comment":"nebula lighthouse ingress 2026-10-11"}).get("success"))
if do:
    after=cf("GET",f"/zones/{zone}/dns_records?per_page=200")["result"]
    print("after:",[(r["name"].split(".")[0],r["type"],r["content"],r["proxied"]) for r in after if r["name"].split(".")[0] in hosts])
