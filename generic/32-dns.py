#!/usr/bin/env python3
"""32-dns.py — upsert unproxied A records for every PUBLIC_HOST at the VPS IP (Cloudflare API).
usage: 32-dns.py [--apply]                         (reads heardos.env next to this script, or $HEARDOS_ENV)
       32-dns.py --delete NAME [TYPE [CONTENT]] [--apply]   remove <NAME>.<DOMAIN> records of TYPE (default A),
                                                   only those whose content is CONTENT when given
Without --apply it only prints the plan. Every record is printed as JSON before it is deleted (keep the output: it is
what you need to put a record back). An existing AAAA/CNAME on a public name blocks the A record; it is only replaced
with --apply, and the plan says so first.
Needs CF_API_TOKEN (zone-scoped, DNS:Edit) and CF_ZONE_ID. Other DNS hosts: create the same A records by hand."""
import json, os, re, shlex, sys, urllib.request, urllib.error

here = os.path.dirname(os.path.abspath(__file__))
envf = os.environ.get("HEARDOS_ENV", os.path.join(here, "heardos.env"))


def env_value(v):
    """Quoted values run to the closing quote (a " #" inside stays); unquoted ones lose a trailing " # comment"."""
    v = v.strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        if end > 0: return v[1:end]
    v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
    try: return " ".join(shlex.split(v)) if v else ""
    except ValueError: return v.strip("'\"")


env = {}
for line in open(envf):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k.strip()] = env_value(v)
TOK, ZONE, DOMAIN, IP = env.get("CF_API_TOKEN", ""), env.get("CF_ZONE_ID", ""), env["DOMAIN"], env.get("NEBULA_LH_IP", "")
if not (TOK and ZONE and IP): raise SystemExit("set CF_API_TOKEN, CF_ZONE_ID and NEBULA_LH_IP in the plan file")
args = [a for a in sys.argv[1:] if a != "--apply"]; APPLY = "--apply" in sys.argv[1:]


def cf(method, path, data=None):
    r = urllib.request.Request("https://api.cloudflare.com/client/v4" + path, method=method,
                               data=json.dumps(data).encode() if data else None,
                               headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as x: return json.load(x)
    except urllib.error.HTTPError as e: raise SystemExit(f"cloudflare {method} {path}: {e.code} {e.read().decode()[:200]}")


def delete(r):
    print("deleting", json.dumps({k: r.get(k) for k in ("id", "type", "name", "content", "proxied", "ttl", "priority", "comment")}))
    print("  ->", cf("DELETE", f"/zones/{ZONE}/dns_records/{r['id']}")["success"])


recs = cf("GET", f"/zones/{ZONE}/dns_records?per_page=500")["result"]
if args and args[0] == "--delete":
    if len(args) < 2: raise SystemExit(__doc__)
    name = f"{args[1]}.{DOMAIN}"; rtype = (args[2] if len(args) > 2 else "A").upper(); content = args[3] if len(args) > 3 else None
    hit = [r for r in recs if r["name"] == name and r["type"] == rtype and (content is None or r["content"] == content)]
    if not hit: print(f"nothing to delete: no {rtype} record {name}" + (f" -> {content}" if content else "")); sys.exit(0)
    for r in hit:
        if APPLY: delete(r)
        else: print(f"would delete {r['type']} {name} -> {r['content']}")
    if not APPLY: print("plan only: re-run with --apply to delete")
    sys.exit(0)

pending = False
for h in env["PUBLIC_HOSTS"].split():
    name = f"{h}.{DOMAIN}"; cur = [r for r in recs if r["name"] == name]
    if [r for r in cur if r["type"] == "A" and r["content"] == IP and not r["proxied"]]:
        print(f"ok      {name} -> {IP}"); continue
    # an A record can't coexist with a CNAME, and a stale A/AAAA would split traffic: these are replaced, nothing else
    clash = [r for r in cur if r["type"] in ("A", "AAAA", "CNAME")]
    for r in clash: print(f"{'replace' if APPLY else 'would replace'} {name} {r['type']} {r['content']} (proxied={r['proxied']})")
    if not APPLY:
        print(f"would create {name} A -> {IP}"); pending = True; continue
    for r in clash: delete(r)
    cf("POST", f"/zones/{ZONE}/dns_records", {"type": "A", "name": name, "content": IP, "ttl": 300, "proxied": False, "comment": "heardos edge"})
    print(f"created {name} -> {IP}")
if pending: print("plan only: re-run with --apply to make these changes")
