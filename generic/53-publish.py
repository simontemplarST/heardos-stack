#!/usr/bin/env python3
"""53-publish.py — make an app reachable at https://HOST.DOMAIN in one go, or take it off again. Run ON THE HOST as root.
  53-publish.py HOST --app SLUG [--name "Name"] [--auth forward_auth|oidc|none] [--users a,b|all] [--launch URL]
                [--backend http://<container>:<port>] [--dry-run]
  53-publish.py --remove HOST [--dry-run]
Publishing, in order (each step is idempotent, so a failed run can simply be repeated):
  1. sign-in  forward_auth (default): Authentik proxy app SLUG for https://HOST.DOMAIN with group app-SLUG (42-ak-app.py
              proxy; --users as there, default admin only; later changes: 42-ak-app.py user access). oidc: the app signs
              users in itself (its client comes from 42-ak-app.py oidc). none: public, anyone on the internet.
  2. DNS      HOST.DOMAIN A -> the edge (32-dns.py HOST --apply); skipped without CF_API_TOKEN (create it by hand).
  3. edge     behind an ngfw firewall (NGFW_URL) the firewall publishes and routes it (lib/ngfw-publish.py publish): the
              route comes from the stack's Traefik labels, or --backend for a stack without public routers. Without
              NGFW_URL: add HOST to PUBLIC_HOSTS and run 30-vps-caddy.sh from the laptop.
  4. check    Traefik answers for the host (a gate sends you to auth.DOMAIN), then the firewall's outside checks.
--remove takes HOST off the firewall and deletes its DNS record; the Authentik app and the stack stay (removing an app
is the installer's job). Catalogue apps need none of this: 50-app.sh publishes their hosts itself (publish_stack).
"""
import argparse, os, re, subprocess, sys, time, urllib.request, urllib.error
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
from ak import load_env, env_path

RESERVED = {"auth", "www"}   # Authentik and the apex redirect; the front door (FRONT_DOOR_HOST) is added below


def step(title): print(f"\n== {title}")


def run(cmd, dry=False, check=True):
    """Run a kit command with the plan file; in a dry run only say what would run (callers pass read-only commands
    with dry=False themselves)."""
    print("  $ " + " ".join(cmd[1:] if cmd[0] == sys.executable else cmd))
    if dry: return 0
    rc = subprocess.run(cmd, env={**os.environ, "HEARDOS_ENV": env_path()}).returncode
    if rc and check: sys.exit(f"  failed (exit {rc}); fix the cause and run 53-publish.py again (every step is idempotent)")
    return rc


def kit(script, *args): return [sys.executable, os.path.join(HERE, script), *args]


def answer(url, host, tries=12):
    """(status, Location) from this host's Traefik for Host: host, retrying while the route is being pulled (15 s)."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    op = urllib.request.build_opener(NoRedirect)
    last = (0, "")
    for _ in range(tries):
        try:
            r = op.open(urllib.request.Request(url, headers={"Host": host}), timeout=10); last = (r.status, "")
        except urllib.error.HTTPError as e: last = (e.code, e.headers.get("Location", ""))
        except OSError as e: last = (0, str(e))
        if last[0] and last[0] not in (404, 502, 503): return last
        time.sleep(5)
    return last


def publish(a, env):
    domain, fq = env["DOMAIN"], f"{a.host}.{env['DOMAIN']}"
    step(f"1/4 sign-in: {a.auth}")
    if a.auth == "forward_auth":
        cmd = kit("42-ak-app.py", "proxy", a.app, "--name", a.name or a.app, "--host", f"https://{fq}")
        if a.users: cmd += ["--users", a.users]
        if a.launch: cmd += ["--launch", a.launch]
        run(cmd, a.dry_run)
    elif a.auth == "oidc":
        print(f"  the app signs users in itself; its Authentik client: 42-ak-app.py oidc {a.app} --redirect https://{fq}/...")
    else:
        print(f"  no sign-in gate: anyone on the internet can open https://{fq}")
    step("2/4 DNS")
    if env.get("CF_API_TOKEN") and env.get("CF_ZONE_ID"):
        run(kit("32-dns.py", a.host) + ([] if a.dry_run else ["--apply"]))   # without --apply it only reads and plans
    else:
        print(f"  no CF_API_TOKEN/CF_ZONE_ID in the plan file: create {fq} A -> {env.get('NEBULA_LH_IP') or 'the edge'} by hand")
    step("3/4 edge")
    if env.get("NGFW_URL"):
        cmd = kit("lib/ngfw-publish.py", "publish", a.host, "--auth", a.auth)
        if a.backend: cmd += ["--backend", a.backend]
        run(cmd + (["--dry-run"] if a.dry_run else []))
    else:
        print(f"  no ngfw firewall (NGFW_URL): add {a.host} to PUBLIC_HOSTS, then run 30-vps-caddy.sh from the laptop")
    step("4/4 check")
    if a.dry_run:
        print("  dry run: nothing to check"); return 0
    status, loc = answer(f"http://{env['HOST_LAN_IP']}/", fq)
    gated = 300 <= status < 400 and loc.startswith(f"https://auth.{domain}/")
    ok = gated if a.auth == "forward_auth" else 0 < status < 500
    print(f"  Traefik: {status} {loc[:60]}" + ("  (gate in place)" if gated else ""))
    if env.get("NGFW_URL"):
        out = subprocess.run(kit("lib/ngfw-publish.py", "status"), capture_output=True, text=True,
                             env={**os.environ, "HEARDOS_ENV": env_path()}).stdout
        print("".join(f"  firewall: {l}\n" for l in out.splitlines() if l.startswith(fq + " ")) or
              "  firewall: no status yet (its outside checks run every few minutes: ngfw-publish.py status)", end="")
    if not ok:
        sys.exit(f"  {fq} does not answer as expected yet" + (" (no redirect to the sign-in page)" if a.auth == "forward_auth" else ""))
    print(f"\nhttps://{fq} is published ({a.auth}).")
    return 0


def remove(a, env):
    fq = f"{a.host}.{env['DOMAIN']}"
    step("1/2 edge")
    if env.get("NGFW_URL"):
        run(kit("lib/ngfw-publish.py", "unpublish", a.host) + (["--dry-run"] if a.dry_run else []))
    else:
        print(f"  remove {a.host} from PUBLIC_HOSTS and run 30-vps-caddy.sh from the laptop")
    step("2/2 DNS")
    if env.get("CF_API_TOKEN") and env.get("CF_ZONE_ID"):
        run(kit("32-dns.py", "--delete", a.host, "A") + ([] if a.dry_run else ["--apply"]))
    else:
        print(f"  delete the {fq} A record by hand")
    print(f"\n{fq} is no longer published; its Authentik app and stack are untouched.")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("host", help="the name under DOMAIN, e.g. tv for tv.DOMAIN")
    p.add_argument("--app", help="Authentik application slug (group app-SLUG)")
    p.add_argument("--name"); p.add_argument("--users"); p.add_argument("--launch")
    p.add_argument("--auth", choices=["forward_auth", "oidc", "none"], default="forward_auth")
    p.add_argument("--backend", help="http://<container>:<port> for a stack without public routers in its labels")
    p.add_argument("--remove", action="store_true"); p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # keep our lines in order with the sub-commands' output
    env = load_env()
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", a.host): sys.exit(f"{a.host!r}: give a plain name under {env['DOMAIN']}")
    if a.host in RESERVED | {env.get("FRONT_DOOR_HOST") or "cloud"}: sys.exit(f"{a.host}.{env['DOMAIN']} is kept by the kit itself")
    if a.remove: return remove(a, env)
    if not a.app or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", a.app): sys.exit("--app SLUG (lowercase letters, digits, dashes) is required")
    if (a.users or a.launch) and a.auth != "forward_auth": sys.exit("--users/--launch belong to --auth forward_auth")
    if a.dry_run: print("dry run: nothing is changed")
    return publish(a, env)


if __name__ == "__main__":
    sys.exit(main())
