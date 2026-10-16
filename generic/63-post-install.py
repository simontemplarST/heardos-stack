#!/usr/bin/env python3
"""63-post-install.py TASK — finish an app's first-run setup over its API instead of in a browser. Run ON THE HOST as root.

  ha-onboard        create the Home Assistant owner (ADMIN_USER), finish onboarding, mint a long-lived token -> HA_TOKEN
  mealie-token      log in as Mealie's built-in admin and create an API token for the family dashboard -> MEALIE_TOKEN
  nextcloud-admin   put ADMIN_USER in Nextcloud's admin group (exit 3 = they have not signed in to Nextcloud yet)
  baikal-user       (exit 3 = Baïkal wizard not finished) create the Baïkal user for ADMIN_USER -> BAIKAL_ADMIN_PASSWORD

Results are stored in $HEARDOS_ROOT/secrets/secrets.env and printed as HEARDOS_OUTPUT lines for the installer's vault.
Idempotent: a value already in secrets.env is reused. stdlib only.
"""
import base64, json, os, secrets, socket, struct, subprocess, sys, time, urllib.parse, urllib.request, urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
from ak import load_env  # noqa: E402

env = load_env()
ROOT = env.get("HEARDOS_ROOT", "/opt/heardos")
SECRETS = f"{ROOT}/secrets/secrets.env"
LAN, IP, USER, EMAIL = env["LAN_DOMAIN"], env["HOST_LAN_IP"], env["ADMIN_USER"], env["ADMIN_EMAIL"]
NAME = env.get("ADMIN_NAME") or USER


def have(k):
    try:
        for line in open(SECRETS):
            if line.startswith(k + "="):
                return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""


def keep(k, v):
    lines = [l for l in open(SECRETS)] if os.path.exists(SECRETS) else []
    lines = [l for l in lines if not l.startswith(k + "=")] + [f"{k}={v}\n"]
    with open(SECRETS, "w") as f:
        f.writelines(lines)
    os.chmod(SECRETS, 0o600)
    print(f"HEARDOS_OUTPUT {k}={v}")


def http(method, url, data=None, headers=None, form=False, timeout=60):
    h = dict(headers or {})
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[", b'"') else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:400]


def wait(url, secs=600):
    t = time.time()
    while time.time() - t < secs:
        try:
            urllib.request.urlopen(url, timeout=5); return
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(5)
    sys.exit(f"{url} did not answer within {secs}s")


# ------------------------------------------------------------------ Home Assistant
def ws_call(host, port, path, token, msg):
    """Minimal websocket client (enough for HA's auth + one command)."""
    s = socket.create_connection((host, port), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    if b" 101 " not in buf.split(b"\r\n", 1)[0]:
        raise SystemExit("HA websocket upgrade refused")
    rest = buf.split(b"\r\n\r\n", 1)[1]

    def recv_exact(n):
        nonlocal rest
        while len(rest) < n:
            chunk = s.recv(65536)
            if not chunk:
                raise SystemExit("HA websocket closed")
            rest += chunk
        out, rest = rest[:n], rest[n:]
        return out

    def recv():
        b1, b2 = recv_exact(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", recv_exact(8))[0]
        return json.loads(recv_exact(n))

    def send(obj):
        data = json.dumps(obj).encode(); mask = os.urandom(4); n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    assert recv()["type"] == "auth_required"
    send({"type": "auth", "access_token": token})
    if recv()["type"] != "auth_ok":
        raise SystemExit("HA websocket auth failed")
    send({"id": 1, **msg})
    while True:
        m = recv()
        if m.get("id") == 1:
            s.close()
            if not m.get("success"):
                raise SystemExit(f"HA command failed: {m.get('error')}")
            return m["result"]


def ha_onboard():
    base = f"http://{IP}:8209"
    if have("HA_TOKEN"):
        st, _ = http("GET", f"{base}/api/", headers={"Authorization": "Bearer " + have("HA_TOKEN")})
        if st == 200:
            print("HA already onboarded; token works"); print(f"HEARDOS_OUTPUT HA_TOKEN={have('HA_TOKEN')}"); return
    wait(f"{base}/api/onboarding", 900)
    st, steps = http("GET", f"{base}/api/onboarding")
    done = {s["step"] for s in steps if s.get("done")} if isinstance(steps, list) else set()
    if "user" in done:
        sys.exit("Home Assistant already has an owner that this installer did not create; mint a token in its profile and set HA_TOKEN")
    cid = f"{base}/"
    pw = have("HA_OWNER_PASSWORD") or secrets.token_urlsafe(18)
    keep("HA_OWNER_PASSWORD", pw)
    st, r = http("POST", f"{base}/api/onboarding/users", {"client_id": cid, "name": NAME, "username": USER, "password": pw, "language": "en"})
    if st != 200:
        sys.exit(f"HA onboarding (user) failed: {st} {r}")
    st, t = http("POST", f"{base}/auth/token", {"grant_type": "authorization_code", "code": r["auth_code"], "client_id": cid}, form=True)
    if st != 200:
        sys.exit(f"HA token exchange failed: {st} {t}")
    auth = {"Authorization": "Bearer " + t["access_token"]}
    http("POST", f"{base}/api/onboarding/core_config", {}, headers=auth)
    http("POST", f"{base}/api/onboarding/analytics", {}, headers=auth)
    http("POST", f"{base}/api/onboarding/integration", {"client_id": cid, "redirect_uri": f"{base}/?auth_callback=1"}, headers=auth)
    tok = ws_call(IP, 8209, "/api/websocket", t["access_token"],
                  {"type": "auth/long_lived_access_token", "client_name": "HeardOS installer", "lifespan": 3650})
    keep("HA_TOKEN", tok)
    print(f"Home Assistant onboarded: owner {USER}; long-lived token stored as HA_TOKEN")


# ------------------------------------------------------------------ Mealie
def mealie_token():
    if have("MEALIE_TOKEN"):
        print(f"HEARDOS_OUTPUT MEALIE_TOKEN={have('MEALIE_TOKEN')}"); return
    base = f"http://mealie.{LAN}"
    wait(f"{base}/api/app/about", 600)
    st, t = http("POST", f"{base}/api/auth/token", {"username": EMAIL, "password": have("MEALIE_ADMIN_PASSWORD")}, form=True)
    if st != 200:
        sys.exit(f"Mealie admin login failed: {st} {t}")
    st, r = http("POST", f"{base}/api/users/api-tokens", {"name": "heardos-dash"}, headers={"Authorization": "Bearer " + t["access_token"]})
    if st not in (200, 201):
        sys.exit(f"Mealie API token failed: {st} {r}")
    keep("MEALIE_TOKEN", r["token"])
    print("Mealie API token created for the dashboard")


# ------------------------------------------------------------------ Nextcloud
def occ(*args):
    return subprocess.run(["docker", "exec", "-u", "www-data", "nextcloud", "php", "occ", *args], capture_output=True, text=True)


def nextcloud_admin():
    if occ("user:info", USER).returncode != 0:
        print(f"{USER} has not signed in to Nextcloud yet"); sys.exit(3)
    r = occ("group:adduser", "admin", USER)
    print(f"{USER} is a Nextcloud admin" if r.returncode == 0 or "already" in (r.stdout + r.stderr) else f"occ: {r.stderr.strip()}")


# ------------------------------------------------------------------ Baïkal
def baikal_user():
    db = f"{ROOT}/data/baikal/specific/db/db.sqlite"
    if not (os.path.exists(db) and os.path.exists(f"{ROOT}/data/baikal/config/baikal.yaml")):
        print("Baïkal wizard not finished yet"); sys.exit(3)
    pw = have("BAIKAL_ADMIN_PASSWORD") or secrets.token_urlsafe(14)
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "60-baikal.py"), "user", USER, EMAIL, NAME, pw],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr.strip() or r.stdout.strip())
    keep("BAIKAL_ADMIN_PASSWORD", pw)
    print(f"Baïkal user {USER} ready (calendar password stored as BAIKAL_ADMIN_PASSWORD)")


TASKS = {"ha-onboard": ha_onboard, "mealie-token": mealie_token, "nextcloud-admin": nextcloud_admin, "baikal-user": baikal_user}
if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        sys.exit(__doc__)
    if os.environ.get("HEARDOS_DRY_RUN"):
        print(f"[dry-run] would run post-install task {sys.argv[1]}"); sys.exit(0)
    TASKS[sys.argv[1]]()
