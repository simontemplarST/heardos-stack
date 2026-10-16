import base64, json, re, sys, urllib.request, urllib.error
pem = open("/root/.cloudflared/cert.pem").read()
m = re.search(r"-----BEGIN ARGO TUNNEL TOKEN-----\s*(.*?)\s*-----END ARGO TUNNEL TOKEN-----", pem, re.S)
tok = json.loads(base64.b64decode("".join(m.group(1).split())))
zone, api = tok["zoneID"], tok["apiToken"]
def cf(method, path):
    r = urllib.request.Request("https://api.cloudflare.com/client/v4" + path, method=method, headers={"Authorization": "Bearer " + api, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as x: return json.load(x)
    except urllib.error.HTTPError as e: return {"success": False, "status": e.code, "body": e.read().decode()[:300]}
recs = cf("GET", f"/zones/{zone}/dns_records?name=photos.example.com")
print("zone", zone, "| matches:", [(r["type"], r["name"], r["content"], r["proxied"], r["id"]) for r in recs.get("result", [])] or recs)
if sys.argv[1:] == ["--delete"]:
    for r in recs.get("result", []):
        if r["name"] == "photos.example.com" and r["type"] == "CNAME" and r["content"].endswith(".cfargotunnel.com"):
            print("delete", r["id"], "->", cf("DELETE", f"/zones/{zone}/dns_records/{r['id']}").get("success"))
    print("after:", [(r["type"], r["name"]) for r in cf("GET", f"/zones/{zone}/dns_records?name=photos.example.com").get("result", [])])
