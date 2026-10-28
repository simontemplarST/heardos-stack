#!/usr/bin/env python3
"""Create or update a Portainer standalone stack from a compose file.
usage: portainer-stack.py NAME COMPOSE_FILE [ENV_FILE]
env:   PORTAINER_URL, PORTAINER_TOKEN (access token from Portainer: My account -> Access tokens)
       PORTAINER_PIN (default $HEARDOS_ROOT/secrets/portainer.pem): Portainer's self-signed certificate, pinned on first
       use and required to match afterwards. If Portainer's certificate changes on purpose, check it and delete the file.
Stacks created this way are fully managed in the Portainer UI (editor, redeploy, logs)."""
import hashlib, http.client, json, os, ssl, sys, time, urllib.parse

name, compose = sys.argv[1], sys.argv[2]
envfile = sys.argv[3] if len(sys.argv) > 3 else None
URL = os.environ["PORTAINER_URL"].rstrip("/"); TOK = os.environ["PORTAINER_TOKEN"]
PIN = os.environ.get("PORTAINER_PIN") or os.path.join(os.environ.get("HEARDOS_ROOT", "/opt/heardos"), "secrets", "portainer.pem")
u = urllib.parse.urlsplit(URL); HOST, PORT = u.hostname, u.port or 443

# The LAN Portainer has a self-signed certificate, so the CA chain can't vouch for it: instead its exact certificate is
# trusted on first use (the host talking to its own LAN address) and every later connection must present the same one.
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE   # checked by fingerprint below
if not os.path.exists(PIN):
    pem = ssl.get_server_certificate((HOST, PORT))
    os.makedirs(os.path.dirname(PIN), mode=0o700, exist_ok=True)
    fd = os.open(PIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f: f.write(pem)
    print(f"portainer: pinned its certificate {hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()[:16]}... in {PIN}")
WANT = hashlib.sha256(ssl.PEM_cert_to_DER_cert(open(PIN).read())).hexdigest()


def api(method, path, data=None):
    c = http.client.HTTPSConnection(HOST, PORT, context=ctx, timeout=120)
    c.connect()
    got = hashlib.sha256(c.sock.getpeercert(binary_form=True)).hexdigest()
    if got != WANT:
        c.close(); raise SystemExit(f"portainer: certificate {got[:16]}... at {HOST}:{PORT} is not the pinned one ({PIN}); refusing to send the token")
    c.request(method, (u.path.rstrip("/") or "") + path, body=json.dumps(data).encode() if data is not None else None,
              headers={"X-API-Key": TOK, "Content-Type": "application/json"})
    r = c.getresponse(); b = r.read(); c.close()
    if r.status >= 400: raise SystemExit(f"portainer {method} {path} -> {r.status}: {b.decode()[:300]}")
    return json.loads(b) if b else {}

t0 = int(time.time()) - 2   # deployment-status entries older than this belong to earlier deploys
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
    api("PUT", f"/api/stacks/{existing['Id']}?endpointId={eid}", {"stackFileContent": content, "env": env, "prune": True, "pullImage": False})   # no pull: images are pinned, and a locally built one (heardos-dash:local) cannot be pulled
    print(f"portainer: updated stack {name} (id {existing['Id']})")
    # a stack someone stopped in Portainer (status 2) keeps the new file but stays down after an update: start it
    cur = next((s for s in api("GET", "/api/stacks") if s["Id"] == existing["Id"]), None)
    if cur and cur.get("Status") == 2:
        api("POST", f"/api/stacks/{existing['Id']}/start?endpointId={eid}")
        print(f"portainer: started stack {name} (it was stopped)")
else:
    s = api("POST", f"/api/stacks/create/standalone/string?endpointId={eid}", {"name": name, "stackFileContent": content, "env": env, "fromAppTemplate": False})
    print(f"portainer: created stack {name} (id {s.get('Id')})")


def wait_deployed(sid, timeout=900):
    """Portainer >= 2.2x answers the PUT before compose has finished (DeploymentStatus 3 = deploying, 1 = done,
    4 = failed). Wait for the outcome, so callers never exec into a container that is about to be replaced
    (2026-10-25: a post-install step raced Portainer's recreate of a container and left the stack half-replaced)."""
    start = time.time(); end = start + timeout
    while time.time() < end:
        st = [x for x in (api("GET", f"/api/stacks/{sid}").get("DeploymentStatus") or []) if x.get("Time", 0) >= t0]
        last = st[-1] if st else None
        if last is None:
            if time.time() - start > 20:   # this Portainer records no deployment status: nothing to wait for
                return
            time.sleep(2); continue
        if last.get("Status") == 1:
            return
        if last.get("Status") == 4:
            raise SystemExit(f"portainer: stack {name} failed to deploy: {last.get('Message', '')[:300]}")
        time.sleep(3)
    raise SystemExit(f"portainer: stack {name} still deploying after {timeout}s")


wait_deployed(existing["Id"] if existing else s.get("Id"))
