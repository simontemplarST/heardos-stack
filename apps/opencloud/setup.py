#!/usr/bin/env python3
"""setup.py — OpenCloud users, shared spaces, members, and adoption of the real media folders. Run ON the host as root. Idempotent.

  setup.py --spec spaces.json [--container opencloud] [--url http://127.0.0.1:9201] [--users a,b,c] [--admin-user alice]

spec: {"data": "/opt/heardos/data/opencloud", "stack": "/opt/heardos/stacks/opencloud", "gid": 1500,
       "spaces": [{"name": "Music", "path": "/opt/heardos/media/music"}, ...]}

Why the adoption dance: PosixFS keeps each project space under <data>/storage/users/projects/<space name> (path template), but it
refuses to create a space over an existing folder ("already exists"). So, once per space:
  1. create the space while nothing is mounted there (OpenCloud makes an empty folder with the space's identity in xattrs + .oc-nodes),
  2. stop OpenCloud, copy that identity (user.* xattrs, .oc-nodes) onto the real folder, empty the placeholder,
  3. bind-mount the real folder over the placeholder (compose.override.yml), start again: the startup scan adopts every file.
The real folder's own permissions are kept; files get user.oc.* xattrs (ids, checksums) and nothing else changes.
Exit code 10 = compose.override.yml changed, redeploy the stack. Users are created in OpenCloud's directory with the Authentik
username (OIDC logins map by preferred_username), so they can be added to spaces before their first login.
"""
import argparse, base64, json, os, secrets, shutil, subprocess, sys, time, urllib.error, urllib.request

ROLE = {"manager": "312c0871-5ef7-4b3a-85b6-0e4074c64049", "editor": "58c63c02-1d89-4572-916a-870abc5a1b7d",
        "viewer": "a8d5fe5e-96e3-418d-825b-534dbdf22b99"}

ap = argparse.ArgumentParser()
ap.add_argument("--spec", required=True); ap.add_argument("--container", default="opencloud"); ap.add_argument("--url", default="http://127.0.0.1:9201")
ap.add_argument("--users", default="", help="username:Display Name:mail,... (people who get the spaces)")
ap.add_argument("--admin-user", default="", help="space manager (also an editor of nothing else)")
a = ap.parse_args()
spec = json.load(open(a.spec)); DATA, STACK, GID = spec["data"], spec["stack"], int(spec.get("gid", 1500))
PROJ = f"{DATA}/data/storage/users/projects"


def sh(*cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(cmd[:4])}…: {r.stderr.strip()[-500:]}")
    return r.stdout


def admin_auth():
    out = sh("docker", "exec", a.container, "opencloud", "auth-app", "create", "--user-name=admin", "--expiration=30m")
    # output: "App token created for admin\n token: <six words>" — the token is the whole passphrase, spaces included
    tok = next((l.split("token:", 1)[1].strip() for l in out.splitlines() if "token:" in l), None)
    if not tok:
        sys.exit("could not read an app token from: " + out[-300:])
    return "Basic " + base64.b64encode(f"admin:{tok}".encode()).decode()


def api(method, path, body=None, ok=(200, 201, 204)):
    r = urllib.request.Request(a.url + path, method=method, headers={"Authorization": AUTH, "Content-Type": "application/json"},
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            t = x.read(); return json.loads(t) if t else None
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:400]
        if e.code in ok: return None
        raise SystemExit(f"{method} {path}: {e.code} {msg}")


def wait_up():
    for _ in range(90):
        try:
            urllib.request.urlopen(a.url + "/status.php", timeout=5); return
        except Exception:
            time.sleep(2)
    sys.exit("OpenCloud did not come up on " + a.url)


wait_up(); time.sleep(5); AUTH = admin_auth()

# --- users (created ahead of their first login so they can be space members)
people = [u.split(":") for u in filter(None, a.users.split(","))]
existing = {u.get("onPremisesSamAccountName"): u for u in api("GET", "/graph/v1.0/users")["value"]}
ids = {}
for p in people:
    name, disp, mail = (p + ["", ""])[:3]
    if name not in existing:
        u = api("POST", "/graph/v1.0/users", {"onPremisesSamAccountName": name, "displayName": disp or name, "mail": mail or f"{name}@invalid",
                                             "passwordProfile": {"password": secrets.token_urlsafe(32)}, "accountEnabled": True})
        print("created user", name); existing[name] = u
    ids[name] = existing[name]["id"]

# --- spaces: create missing ones (only possible while nothing is mounted at their folder)
drives = {d["name"]: d for d in api("GET", "/graph/v1.0/drives?$filter=driveType+eq+'project'")["value"]}
override_needed = False
for s in spec["spaces"]:
    real = s["path"]
    adopted = b"user.oc.space.id" in [x.encode() for x in os.listxattr(real)] if os.path.exists(real) else False
    if s["name"] not in drives:
        if adopted:
            sys.exit(f"{real} carries a space identity but OpenCloud has no space '{s['name']}' — restore the matching OpenCloud data first")
        ph = f"{PROJ}/{s['name']}"
        if os.path.isdir(ph) and os.listdir(ph):
            sys.exit(f"placeholder {ph} is not empty (bind mount still active?) — remove the space from compose.override.yml, redeploy, rerun")
        if os.path.isdir(ph): os.rmdir(ph)
        drives[s["name"]] = api("POST", "/graph/v1.0/drives", {"name": s["name"], "driveType": "project", "description": s.get("description", "")})
        print("created space", s["name"])
    if not adopted:
        override_needed = True

# --- adoption: move each new space's identity onto the real folder (OpenCloud stopped meanwhile)
if override_needed:
    print("stopping OpenCloud for adoption"); sh("docker", "stop", a.container)
    for s in spec["spaces"]:
        real, ph = s["path"], f"{PROJ}/{s['name']}"
        if b"user.oc.space.id" in [x.encode() for x in os.listxattr(real)]:
            continue
        if os.path.exists(os.path.join(real, ".oc-nodes")):
            sys.exit(f"{real}/.oc-nodes already exists — refusing to mix two space identities")
        # OpenCloud (uid 33, gid GID) must own what it indexes
        sh("chown", "-R", f"33:{GID}", real); sh("chmod", "2775", real)
        n = 0
        for x in os.listxattr(ph):
            if x.startswith("user."):
                os.setxattr(real, x, os.getxattr(ph, x)); n += 1
        for e in os.listdir(ph):
            shutil.move(os.path.join(ph, e), os.path.join(real, e))
        print(f"adopted {real} as space '{s['name']}' ({n} xattrs)")
    lines = ["# written by apps/opencloud/setup.py — the real folders behind the shared spaces (do not edit by hand)", "services:",
             "  opencloud:", "    volumes:"]
    lines += [f"      - {s['path']}:/var/lib/opencloud/storage/users/projects/{s['name']}" for s in spec["spaces"]]
    lines += ["  opencloud-perms:", "    volumes:"] + [f"      - {s['path']}:/spaces/{s['name']}" for s in spec["spaces"]]
    open(f"{STACK}/compose.override.yml", "w").write("\n".join(lines) + "\n")
    print("compose.override.yml written — redeploy the stack, then run setup.py again for the members")
    sys.exit(10)

# --- members (space manager = admin user, everyone else editor)
for s in spec["spaces"]:
    d = drives[s["name"]]
    perms = api("GET", f"/graph/v1beta1/drives/{d['id']}/root/permissions") or {}
    have = {g.get("grantedToV2", {}).get("user", {}).get("id") for g in perms.get("value", [])}
    for name, uid in ids.items():
        if uid in have:
            continue
        role = ROLE["manager"] if name == a.admin_user else ROLE[s.get("role", "editor")]
        api("POST", f"/graph/v1beta1/drives/{d['id']}/root/invite", {"recipients": [{"objectId": uid, "@libre.graph.recipient.type": "user"}], "roles": [role]})
        print(f"space '{s['name']}': {name} -> {'manager' if role == ROLE['manager'] else s.get('role', 'editor')}")
print("setup complete")
