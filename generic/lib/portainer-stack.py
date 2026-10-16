#!/usr/bin/env python3
"""Create or update a Portainer standalone stack from a compose file.
usage: portainer-stack.py NAME COMPOSE_FILE [ENV_FILE]
env:   PORTAINER_URL, PORTAINER_TOKEN (access token from Portainer: My account -> Access tokens)
Stacks created this way are fully managed in the Portainer UI (editor, redeploy, logs)."""
import json, os, ssl, sys, urllib.request, urllib.error

name, compose = sys.argv[1], sys.argv[2]
envfile = sys.argv[3] if len(sys.argv) > 3 else None
URL = os.environ["PORTAINER_URL"].rstrip("/"); TOK = os.environ["PORTAINER_TOKEN"]
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE  # self-signed Portainer cert on the LAN

def api(method, path, data=None):
    r = urllib.request.Request(URL + path, method=method, data=json.dumps(data).encode() if data is not None else None,
                               headers={"X-API-Key": TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, context=ctx, timeout=120) as x: b = x.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e: raise SystemExit(f"portainer {method} {path} -> {e.code}: {e.read().decode()[:300]}")

env = []
if envfile and os.path.exists(envfile):
    for line in open(envfile):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env.append({"name": k, "value": v})
content = open(compose).read()
endpoints = api("GET", "/api/endpoints"); eid = next((e["Id"] for e in endpoints if e.get("Type") == 1), endpoints[0]["Id"])
existing = next((s for s in api("GET", "/api/stacks") if s["Name"] == name), None)
if existing:
    api("PUT", f"/api/stacks/{existing['Id']}?endpointId={eid}", {"stackFileContent": content, "env": env, "prune": True, "pullImage": True})
    print(f"portainer: updated stack {name} (id {existing['Id']})")
else:
    s = api("POST", f"/api/stacks/create/standalone/string?endpointId={eid}", {"name": name, "stackFileContent": content, "env": env, "fromAppTemplate": False})
    print(f"portainer: created stack {name} (id {s.get('Id')})")
