"""cfapi.py — shared Cloudflare helpers for the edge/dns scripts (2026-10-19).

Token: CF_API_TOKEN, a zone-scoped API token with only Zone.DNS:Edit on the one zone, from the environment or
~/.config/credentials.env (only that key is read). Zone: CF_ZONE_ID the same way, else looked up by CF_ZONE (default
example.com). The old trick of decoding the tunnel token out of /root/.cloudflared/cert.pem is gone: that credential is
account-wide and long-lived.
Every mutating script prints its plan and changes nothing without --apply; every record is printed as JSON before it is
deleted or overwritten, so the output is enough to put it back."""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request

ZONE_NAME = os.environ.get("CF_ZONE", "example.com")
APPLY = "--apply" in sys.argv[1:]
ARGS = [a for a in sys.argv[1:] if a != "--apply"]


def _cred(key):
    if os.environ.get(key):
        return os.environ[key]
    f = os.path.expanduser("~/.config/credentials.env")
    if os.path.exists(f):
        for line in open(f):
            m = re.match(rf"\s*(?:export\s+)?{key}=(.*)$", line)
            if m:
                v = m.group(1).strip()
                return v[1:v.find(v[0], 1)] if v[:1] in "'\"" and v.find(v[0], 1) > 0 else re.split(r"\s+#", v, maxsplit=1)[0]
    return ""


TOKEN = _cred("CF_API_TOKEN")
if not TOKEN:
    raise SystemExit("set CF_API_TOKEN (a Cloudflare API token scoped to Zone.DNS:Edit on %s only) in the environment or "
                     "~/.config/credentials.env. The cloudflared cert.pem token is no longer used: it is account-wide." % ZONE_NAME)


def cf(method, path, data=None):
    r = urllib.request.Request("https://api.cloudflare.com/client/v4" + path, method=method,
                               data=json.dumps(data).encode() if data is not None else None,
                               headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as x: return json.load(x)
    except urllib.error.HTTPError as e:
        return {"success": False, "status": e.code, "body": e.read().decode()[:300], "result": []}


ZONE = _cred("CF_ZONE_ID")
if not ZONE:
    z = cf("GET", "/zones?name=" + urllib.parse.quote(ZONE_NAME)).get("result") or []
    if not z: raise SystemExit(f"zone {ZONE_NAME} not visible to CF_API_TOKEN; set CF_ZONE_ID or check the token's zone scope")
    ZONE = z[0]["id"]


def fq(n):
    return n if n == ZONE_NAME or n.endswith("." + ZONE_NAME) else f"{n}.{ZONE_NAME}"


def records(name=None, rtype=None):
    q = {"per_page": "500"}
    if name: q["name"] = name
    if rtype: q["type"] = rtype
    r = cf("GET", f"/zones/{ZONE}/dns_records?" + urllib.parse.urlencode(q))
    if not r.get("success"): raise SystemExit(f"cloudflare: listing records failed: {r.get('status')} {r.get('body', '')[:200]}")
    return r.get("result") or []


def show(r):
    return json.dumps({k: r.get(k) for k in ("id", "type", "name", "content", "proxied", "ttl", "priority", "comment")})


def delete(r):
    """Delete one record (only with --apply), logging it as JSON first."""
    if not APPLY:
        print(f"  would delete {r['type']} {r['name']} -> {r['content']}"); return None
    print("  deleting", show(r))
    ok = cf("DELETE", f"/zones/{ZONE}/dns_records/{r['id']}").get("success"); print("  ->", ok); return ok


def create(body):
    if not APPLY:
        print(f"  would create {body['type']} {body['name']} -> {body['content']}"); return None
    ok = cf("POST", f"/zones/{ZONE}/dns_records", body).get("success"); print(f"  created {body['type']} {body['name']} -> {body['content']}:", ok); return ok


def replace(r, body):
    if not APPLY:
        print(f"  would replace {r['type']} {r['name']} {r['content'][:60]} -> {body['content'][:60]}"); return None
    print("  replacing", show(r))
    ok = cf("PUT", f"/zones/{ZONE}/dns_records/{r['id']}", body).get("success"); print("  ->", ok); return ok


def done():
    if not APPLY: print("plan only: re-run with --apply to make these changes")
