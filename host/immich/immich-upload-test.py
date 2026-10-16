#!/usr/bin/env python3
"""Upload a ~150 MB file to Immich through the public URL to prove no size cap, then delete it.
usage: immich-upload-test.py <base_url> <email> <password>"""
import io, json, os, sys, time, urllib.request, urllib.error, uuid

BASE, EMAIL, PW = sys.argv[1:4]
SIZE = 150 * 1024 * 1024

def req(method, path, data=None, headers=None, token=None):
    h = {"Accept": "application/json"}; h.update(headers or {})
    if token: h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=600) as x: b = x.read(); return x.status, (json.loads(b) if b else {})
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]

st, login = req("POST", "/api/auth/login", json.dumps({"email": EMAIL, "password": PW}).encode(), {"Content-Type": "application/json"})
tok = login["accessToken"]

boundary = "----immichtest" + uuid.uuid4().hex
now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
fields = {"deviceAssetId": "ingress-test-" + uuid.uuid4().hex[:8], "deviceId": "ingress-test", "fileCreatedAt": now, "fileModifiedAt": now, "isFavorite": "false"}
head = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items())
head += f"--{boundary}\r\nContent-Disposition: form-data; name=\"assetData\"; filename=\"ingress-test.mp4\"\r\nContent-Type: video/mp4\r\n\r\n".encode()
tail = f"\r\n--{boundary}--\r\n".encode()
body = head + os.urandom(SIZE) + tail
t0 = time.time()
st, out = req("POST", "/api/assets", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))}, tok)
dt = time.time() - t0
print(f"upload: status={st} bytes={len(body)} in {dt:.1f}s ({len(body)/dt/1e6:.1f} MB/s) -> {out if not isinstance(out, dict) else {k: out.get(k) for k in ('id', 'status')}}")
if isinstance(out, dict) and out.get("id"):
    st, d = req("DELETE", "/api/assets", json.dumps({"ids": [out["id"]], "force": True}).encode(), {"Content-Type": "application/json"}, tok)
    print("cleanup delete:", st)
    st, stats = req("GET", "/api/assets/statistics", token=tok); print("asset stats after cleanup:", stats)
