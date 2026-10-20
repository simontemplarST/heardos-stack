# @id: script_1792810000003_stackupd
# @title: Stack updates - check and apply
# @description: check = report which container images have a newer build or a newer version; apply = pin the new version/digest in the stack through Portainer and redeploy it (same major version only), keep the updated stack file for the laptop to sync. Scheduled: check Wednesdays 07:00, apply Sundays 02:30 Central
#!/usr/bin/env python3
"""stack-updates.py check|apply [--stack NAME] [--at-central-hour H]

Every stack's images are pinned (tag@sha256 after the 2026-10-19 security review), so an update is always an edit to
the stack file. For each running container's image, the registry (Docker Hub, ghcr.io, lscr.io, quay.io; anonymous
pull tokens) is asked:
  tag@digest, floating tag   (cronmaster:latest, invidious:master, redis:6.2, postgres:14-vectorchord…): has the tag
                             moved to a new digest?                      -> "new build"      (applied)
  tag@digest / tag, version  (mealie:v3.28.0): is there a higher version of the same shape and the same major?
                                                                         -> "newer version"  (applied)
                             first number changed (for 0.x: the second)  -> "major upgrade"  (reported only)
                             date-shaped tags (2021.12.16) never count as newer for a short version
  digest only                (postgres@sha256:…, nginx@sha256:…): the tag is unknown, a move could be a major jump
                                                                         -> reported only ("pin a tag")
  locally built              (heardos-*)                                 -> skipped
apply = for each stack with something applicable: take the stack file Portainer holds, rewrite those image lines
(new tag and/or new digest), PUT it back with pullImage, wait for the deploy (Portainer only recreates containers
whose image changed), then prune dangling images. The changes (old ref, new ref, digest) are saved to
data/stack-updates/<stack>.json so the laptop's copy (the source of truth for pstack.py, with its own pins and
comments) can be brought up to date: ~/heardos-install/migration-20261016/stack-updates-sync.sh. pstack.py refuses to deploy a stack whose pins in
Portainer are newer than the local file until that sync is done.

Portainer access: stack-updates.env next to this script (PORTAINER_URL, PORTAINER_TOKEN, PORTAINER_ENDPOINT_ID).
The crontab is UTC, so each job is scheduled at both possible UTC hours and --at-central-hour makes the wrong one a no-op."""
import argparse, datetime, json, os, re, ssl, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, zoneinfo

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "data", "stack-updates")
ACCEPT = ", ".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])
FLOATING_WORDS = {"latest", "lts", "master", "main", "stable", "edge", "nightly", "dev"}
UA = "heardos-stack-updates"
sys.stdout.reconfigure(line_buffering=True)   # CronMaster/OliveTin read the log while it runs


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


# ---------------------------------------------------------------- image references
def parse_ref(image):
    """'ghcr.io/org/app:v1@sha256:..' -> (registry host, repo path, tag or None, digest or None)"""
    digest = None
    if "@" in image: image, digest = image.split("@", 1)
    first = image.split("/")[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"): host, path = first, image[len(first) + 1:]
    else: host, path = "docker.io", image
    if host == "docker.io" and "/" not in path: path = "library/" + path
    tag = None
    if ":" in path.rsplit("/", 1)[-1]: path, tag = path.rsplit(":", 1)
    return host, path, tag, digest


def make_ref(host, path, tag, digest):
    p = path[8:] if host == "docker.io" and path.startswith("library/") else path
    r = p if host == "docker.io" else f"{host}/{p}"
    if tag: r += ":" + tag
    if digest: r += "@" + digest
    return r


# ---------------------------------------------------------------- registry v2, anonymous (token from the WWW-Authenticate challenge)
class Registry:
    def __init__(self): self.tokens = {}

    def _url(self, host, path, what):
        return f"https://{'registry-1.docker.io' if host == 'docker.io' else host}/v2/{path}/{what}"

    def _request(self, host, path, what, method, token):
        req = urllib.request.Request(self._url(host, path, what), method=method, headers={"Accept": ACCEPT, "User-Agent": UA})
        if token: req.add_header("Authorization", "Bearer " + token)
        return urllib.request.urlopen(req, timeout=60)

    def get(self, host, path, what, method="GET"):
        key = (host, path)
        try:
            return self._request(host, path, what, method, self.tokens.get(key))
        except urllib.error.HTTPError as e:
            if e.code != 401 or key in self.tokens and self.tokens[key]: raise
            ch = dict(re.findall(r'(\w+)="([^"]*)"', e.headers.get("WWW-Authenticate", "")))
            if "realm" not in ch: raise
            q = {k: ch[k] for k in ("service", "scope") if k in ch}
            tok_req = urllib.request.Request(ch["realm"] + "?" + urllib.parse.urlencode(q), headers={"User-Agent": UA})
            tok = json.load(urllib.request.urlopen(tok_req, timeout=30))
            self.tokens[key] = tok.get("token") or tok.get("access_token") or ""
            return self._request(host, path, what, method, self.tokens[key])

    def digest(self, host, path, tag):
        with self.get(host, path, f"manifests/{tag}", method="HEAD") as r:
            return r.headers.get("Docker-Content-Digest")

    def tags(self, host, path):
        out, what = [], "tags/list?n=1000"
        for _ in range(30):   # Docker Hub paginates with a Link header
            with self.get(host, path, what) as r:
                out += json.load(r).get("tags") or []
                link = r.headers.get("Link")
            m = re.search(r"<([^>]+)>", link or "")
            if not m: break
            what = m.group(1).split(f"/v2/{path}/", 1)[-1]
        return out


# ---------------------------------------------------------------- versions
def version_shape(tag):
    """'v3.28.0' -> ('v', [3,28,0], ''); '0.26.2-rootless' -> ('', [0,26,2], '-rootless'); None when not a version"""
    m = re.fullmatch(r"([A-Za-z]*)(\d+(?:\.\d+)*)([-_.][A-Za-z][\w.-]*)?", tag)
    return (m.group(1), [int(x) for x in m.group(2).split(".")], m.group(3) or "") if m else None


def is_floating(tag):
    s = version_shape(tag)
    if s is None: return True                 # latest, lts, master, anything without a version in it
    return len(s[1]) < 3 or bool(re.search(r"\d", s[2]))   # postgres:17, onlyoffice:9.3; 14-vectorchord0.3.0 (suffix carries its own versions)


def newer_versions(current, tags):
    cur = version_shape(current)
    if not cur: return None, None
    same, major = [], []
    datey = lambda v: v[0] >= 1000                       # 2021.12.16-style date tags are a different scheme, not a newer version
    breaking = 1 if cur[1][0] else 2                     # semver: with a 0.x version the minor number is the breaking one
    for t in tags:
        s = version_shape(t)
        if not s or s[0] != cur[0] or s[2] != cur[2] or len(s[1]) != len(cur[1]) or s[1] <= cur[1] or datey(s[1]) != datey(cur[1]): continue
        (same if s[1][:breaking] == cur[1][:breaking] else major).append(s[1])
    fmt = lambda v: cur[0] + ".".join(map(str, v)) + cur[2]
    return (fmt(max(same)) if same else None), (fmt(max(major)) if major else None)


# ---------------------------------------------------------------- the check
def containers():
    out = []
    for r in sh("docker", "ps", "--format", "{{.Names}}\t{{.Label \"com.docker.compose.project\"}}\t{{.ID}}").strip().splitlines():
        name, stack, cid = r.split("\t")
        if not stack: continue
        insp = json.loads(sh("docker", "inspect", cid))[0]
        img = json.loads(sh("docker", "image", "inspect", insp["Image"]))[0]
        out.append({"container": name, "stack": stack, "image": insp["Config"]["Image"], "repoDigests": img.get("RepoDigests") or []})
    return out


def check(only=None, verbose=True):
    reg, seen, results = Registry(), set(), []
    for c in containers():
        if only and c["stack"] != only: continue
        if (c["stack"], c["image"]) in seen: continue
        seen.add((c["stack"], c["image"]))
        host, path, tag, digest = parse_ref(c["image"])
        row = {"stack": c["stack"], "container": c["container"], "image": c["image"], "kind": "", "state": "", "note": "", "new": None}
        results.append(row)
        try:
            if c["image"].startswith("heardos-"):
                row.update(kind="local build", state="skip", note="built on this host; rebuild to update"); continue
            if not tag:
                row.update(kind="digest only", state="unpinned tag", note="pinned to a digest with no tag: find out its version and pin <tag>@digest"); continue
            if is_floating(tag):
                row["kind"] = "floating tag"
                remote = reg.digest(host, path, tag)
                local = digest or next((d.split("@", 1)[1] for d in c["repoDigests"]), None)
                if not remote or not local: row.update(state="unknown", note="no digest to compare")
                elif remote == local: row.update(state="current")
                else: row.update(state="new build", note=f"{tag}: {local[7:19]} -> {remote[7:19]}", new=make_ref(host, path, tag, remote if digest else None))
            else:
                row["kind"] = "pinned version"
                same, major = newer_versions(tag, reg.tags(host, path))
                if same:
                    row.update(state="newer version", note=f"{tag} -> {same}" + (f" (major {major} exists too)" if major else ""),
                               new=make_ref(host, path, same, reg.digest(host, path, same) if digest else None))
                elif major: row.update(state="major upgrade", note=f"{tag} -> {major}: read the release notes, then pin it by hand")
                else: row.update(state="current")
        except Exception as e:
            row.update(state="unknown", note=f"{type(e).__name__}: {str(e)[:90]}")
    if verbose:
        w = max((len(r["stack"]) for r in results), default=5)
        order = {"new build": 0, "newer version": 1, "major upgrade": 2, "unpinned tag": 3, "unknown": 4, "current": 5, "skip": 6}
        for r in sorted(results, key=lambda r: (order.get(r["state"], 9), r["stack"])):
            print(f"{r['state']:<15} {r['stack']:<{w}}  {r['image'][:70]:<70} {r['note']}")
        n = lambda s: sum(1 for r in results if r["state"] == s)
        print(f"\napplicable: {n('new build')} new builds + {n('newer version')} newer versions; by hand: {n('major upgrade')} major upgrades, {n('unpinned tag')} digest-only pins; {n('current')} current, {n('unknown')} unknown")
    return results


# ---------------------------------------------------------------- apply through Portainer
def portainer():
    env = {}
    for line in open(os.path.join(HERE, "stack-updates.env")):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line: k, v = line.split("=", 1); env[k] = v.strip()
    base, tok, ep = env["PORTAINER_URL"].rstrip("/") + "/api", env["PORTAINER_TOKEN"], env.get("PORTAINER_ENDPOINT_ID", "1")
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE   # Portainer's own cert on the LAN address

    def api(method, path, body=None):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-API-Key": tok, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=900, context=ctx) as r:
            raw = r.read(); return json.loads(raw) if raw else None
    return api, ep


def rewrite(content, changes):
    """replace `image: OLD` with `image: NEW` (the whole reference, quoted or not); returns new content + what was missed"""
    missed = []
    for old, new in changes:
        pat = re.compile(r"(^\s*image:\s*['\"]?)" + re.escape(old) + r"(['\"]?\s*(#.*)?$)", re.M)
        content, n = pat.subn(lambda m: m.group(1) + new + m.group(2), content)
        if n == 0: missed.append(old)
    return content, missed


def apply(only=None):
    results = check(only)
    todo = {}
    for r in results:
        if r["new"]: todo.setdefault(r["stack"], []).append((r["image"], r["new"]))
    if not todo:
        print("\nnothing to apply"); return 0
    api, ep = portainer()
    byname = {s["Name"]: s for s in api("GET", "/stacks")}
    os.makedirs(OUT, exist_ok=True)
    rc = 0
    for name, changes in sorted(todo.items()):
        s = byname.get(name)
        if not s: print(f"\n{name}: not a Portainer stack, skipped"); continue
        print(f"\n{name}: " + "; ".join(f"{o.split('@')[0]} -> {n.split('@')[0]}" for o, n in changes))
        try:
            content = api("GET", f"/stacks/{s['Id']}/file")["StackFileContent"]
            new_content, missed = rewrite(content, changes)
            if missed: print(f"  not found in the stack file (image set by a variable?): {', '.join(missed)}")
            if new_content == content: print("  nothing to change in the stack file"); continue
            api("PUT", f"/stacks/{s['Id']}?endpointId={ep}", {"stackFileContent": new_content, "env": s.get("Env") or [], "prune": False, "pullImage": True})
            for _ in range(120):   # Status 3 = deployment in progress
                time.sleep(5)
                if api("GET", f"/stacks/{s['Id']}").get("Status") != 3: break
            # record what changed (old ref -> new ref, plus the digest now running) for the laptop's copy of the stack
            # file, which may carry pins and comments Portainer's copy lacks: stack-updates-sync.sh applies these there
            rec = []
            for old, new in changes:
                repo = new.split("@")[0].rsplit(":", 1)[0] if ":" in new.split("@")[0].rsplit("/", 1)[-1] else new.split("@")[0]
                dig = new.split("@")[1] if "@" in new else next((d.split("@")[1] for d in json.loads(sh("docker", "image", "inspect", new.split("@")[0]))[0].get("RepoDigests", []) if d.startswith(repo + "@")), None)
                rec.append({"old": old, "new": new.split("@")[0], "digest": dig})
            with open(os.path.join(OUT, f"{name}.json"), "w") as f: json.dump(rec, f, indent=1)
            print(f"  deployed; changes saved for the laptop: data/stack-updates/{name}.json")
            down = [l for l in sh("docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={name}", "--format", "{{.Names}}\t{{.Status}}").splitlines() if "\tUp" not in l]
            if down: rc = 1; print("  NOT RUNNING after the update: " + "; ".join(down))
        except Exception as e:
            rc = 1; print(f"  FAILED {type(e).__name__}: {str(e)[:200]}")
    print("\nafter:"); check(only)
    pruned = sh("docker", "image", "prune", "-f").strip().splitlines()
    print(f"\n{pruned[-1] if pruned else 'no dangling images'}")
    if os.listdir(OUT): print("sync the laptop copies: ~/heardos-install/migration-20261016/stack-updates-sync.sh")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["check", "apply"])
    ap.add_argument("--stack", help="only this stack")
    ap.add_argument("--at-central-hour", type=int, help="exit at once unless it is this hour in America/Chicago (for a UTC crontab)")
    a = ap.parse_args()
    if a.at_central_hour is not None and datetime.datetime.now(zoneinfo.ZoneInfo("America/Chicago")).hour != a.at_central_hour:
        print(f"not {a.at_central_hour}:xx Central right now; nothing to do"); sys.exit(0)
    sys.exit(apply(a.stack) if a.mode == "apply" else (0 if check(a.stack) is not None else 1))
