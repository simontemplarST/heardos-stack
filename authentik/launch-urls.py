#!/usr/bin/env python3
"""authentik-launch-urls.py — point every Authentik application's launch URL at the cloud.example.com front door.

  authentik-launch-urls.py [--dry-run] [--base https://cloud.example.com]   set meta_launch_url = <base>/<slug>
  authentik-launch-urls.py --restore BEFORE.json                             put the saved launch URLs back

Idempotent. Skips applications whose launch URL is blank:// (hidden gates). Saves the previous values to
authentik-launch-urls.before-<date>.json next to this script before changing anything.
Needs AUTHENTIK_TOKEN (admin API token) in the environment, e.g. `set -a; . ~/.config/credentials.env; set +a`.
The front door itself is the cloud.example.com site in the lighthouse Caddyfile (copy in ~/heardos-install/Caddyfile).
"""
import argparse, datetime, json, os, pathlib, sys, urllib.request

AK = os.environ.get("AUTHENTIK_URL", "https://auth.example.com")
TOK = os.environ["AUTHENTIK_TOKEN"]
HERE = pathlib.Path(__file__).resolve().parent


def api(method, path, body=None):
    req = urllib.request.Request(AK + "/api/v3" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + TOK, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def apps():
    return api("GET", "/core/applications/?page_size=200&superuser_full_list=true")["results"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://cloud.example.com")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", metavar="BEFORE.json")
    a = ap.parse_args()

    if a.restore:
        saved = json.load(open(a.restore))
        for slug, url in saved.items():
            print(f"{slug:16} -> {url}")
            if not a.dry_run:
                api("PATCH", f"/core/applications/{slug}/", {"meta_launch_url": url})
        return

    current = apps()
    before = {x["slug"]: x.get("meta_launch_url") or "" for x in current}
    if not a.dry_run:
        out = HERE / f"authentik-launch-urls.before-{datetime.date.today():%Y%m%d}.json"
        if not out.exists():
            out.write_text(json.dumps(before, indent=1))
            print("saved previous launch URLs to", out)

    for x in sorted(current, key=lambda x: x["slug"]):
        slug, old = x["slug"], before[x["slug"]]
        if old.startswith("blank://"):
            print(f"{slug:16} skip (hidden gate: {old})")
            continue
        new = f"{a.base.rstrip('/')}/{slug}"
        if old == new:
            print(f"{slug:16} ok   {new}")
            continue
        print(f"{slug:16} {old or '(none)'} -> {new}")
        if not a.dry_run:
            api("PATCH", f"/core/applications/{slug}/", {"meta_launch_url": new})


if __name__ == "__main__":
    sys.exit(main())
