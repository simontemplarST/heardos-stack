import base64, json, re, urllib.request
pem=open("/root/.cloudflared/cert.pem").read(); tok=json.loads(base64.b64decode("".join(re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END",pem,re.S).group(1).split())))
zone,api=tok["zoneID"],tok["apiToken"]
def cf(m,p,d=None):
    r=urllib.request.Request("https://api.cloudflare.com/client/v4"+p,method=m,data=json.dumps(d).encode() if d else None,headers={"Authorization":"Bearer "+api,"Content-Type":"application/json"})
    with urllib.request.urlopen(r,timeout=30) as x: return json.load(x)
ex=[r for r in cf("GET",f"/zones/{zone}/dns_records?name=photos.example.com")["result"]]
print("existing:",[(r["type"],r["content"]) for r in ex])
if not any(r["type"]=="A" and r["content"]=="203.0.113.10" for r in ex):
    print("created:",cf("POST",f"/zones/{zone}/dns_records",{"type":"A","name":"photos.example.com","content":"203.0.113.10","ttl":300,"proxied":False,"comment":"nebula lighthouse ingress 2026-10-11"})["success"])
