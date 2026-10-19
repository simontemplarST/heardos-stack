"""Tiny Authentik API client shared by 41-authentik-bootstrap.py and 42-ak-app.py."""
import json, os, sys, urllib.request, urllib.error

def env_path():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get("HEARDOS_ENV", os.path.join(here, "heardos.env"))

def load_env():
    env = {}
    for line in open(env_path()):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k] = v.split(" #")[0].strip().strip('"')
    return env

def set_env_value(key, value):
    """Replace (or append) KEY=VALUE in the plan file, atomically and 0600; the value never reaches a command line."""
    p = env_path(); lines = open(p).read().splitlines() if os.path.exists(p) else []
    out = [f"{key}={value}" if l.startswith(key + "=") else l for l in lines]
    if out == lines and f"{key}={value}" not in lines: out.append(f"{key}={value}")
    tmp = p + ".tmp"; fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: f.write("\n".join(out) + "\n")
    os.replace(tmp, p)

def read_secret_input(stdin_flag=False):
    """A password handed over without argv: $HEARDOS_PASSWORD_FILE (non-empty) or one line on stdin (--password-stdin).
    Returns None when neither was given (the caller then generates one)."""
    f = os.environ.get("HEARDOS_PASSWORD_FILE")
    if f and os.path.exists(f) and os.path.getsize(f):
        return open(f).read().strip()
    if stdin_flag:
        v = sys.stdin.readline().rstrip("\n")
        if not v: raise SystemExit("--password-stdin given but stdin was empty")
        return v
    return None

def hand_back_secret(name, value):
    """Give a generated secret to the caller without printing it in clear: into $HEARDOS_PASSWORD_FILE when set,
    otherwise as a HEARDOS_OUTPUT line (the installer masks those in its logs and moves them into its vault)."""
    f = os.environ.get("HEARDOS_PASSWORD_FILE")
    if f:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, value.encode()); os.close(fd)
        print(f"  {name} written to {f}")
    else:
        print(f"HEARDOS_OUTPUT {name}={value}")

class AK:
    def __init__(self, env):
        self.base = env["AUTHENTIK_URL"].rstrip("/") + "/api/v3"; self.tok = env["AUTHENTIK_TOKEN"]
        if not self.tok: raise SystemExit("AUTHENTIK_TOKEN is empty in the plan file")
    def call(self, method, path, data=None, ok=(200, 201, 204)):
        r = urllib.request.Request(self.base + path, method=method, data=json.dumps(data).encode() if data is not None else None,
                                   headers={"Authorization": "Bearer " + self.tok, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=60) as x: b = x.read(); return json.loads(b) if b else {}
        except urllib.error.HTTPError as e:
            raise SystemExit(f"authentik {method} {path} -> {e.code}: {e.read().decode()[:400]}")
    def get(self, p): return self.call("GET", p)
    def post(self, p, d): return self.call("POST", p, d)
    def patch(self, p, d): return self.call("PATCH", p, d)
    def put(self, p, d): return self.call("PUT", p, d)
    def delete(self, p): return self.call("DELETE", p)
    def results(self, p): return self.get(p + ("&" if "?" in p else "?") + "page_size=500")["results"]
    def one(self, p, **match):
        for r in self.results(p):
            if all(r.get(k) == v for k, v in match.items()): return r
        return None
    # look-ups used everywhere
    def flow(self, slug): return self.one("/flows/instances/", slug=slug)
    def group(self, name): return self.one("/core/groups/", name=name)
    def user(self, username): return self.one("/core/users/", username=username)
    def mapping(self, name): return self.one("/propertymappings/all/", name=name)
    def embedded_outpost(self): return self.one("/outposts/instances/", name="authentik Embedded Outpost")
    def ensure_group(self, name, users=None, notes=""):
        g = self.group(name)
        if g: return g
        return self.post("/core/groups/", {"name": name, "is_superuser": False, "users": users or [], "attributes": {"notes": notes}})
    def ensure_binding(self, target, order=0, **kw):
        for b in self.results(f"/policies/bindings/?target={target}"):
            if all(b.get(k) == v for k, v in kw.items() if k in ("policy", "group", "user")): return b
        body = {"target": target, "order": order, "enabled": True, "timeout": 30, "negate": False, "failure_result": False}; body.update(kw)
        return self.post("/policies/bindings/", body)
