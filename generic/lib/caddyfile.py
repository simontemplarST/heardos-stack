#!/usr/bin/env python3
"""caddyfile.py — render the lighthouse Caddyfile (stdlib only; used by 30-vps-caddy.sh and imported by the installer).

  caddyfile.py            reads the plan file ($HEARDOS_ENV or ../heardos.env) and prints the Caddyfile
Inputs: DOMAIN, ACME_EMAIL, HOST_OVERLAY, PUBLIC_HOSTS, optional CADDY_FRONT_DOOR=1 (adds cloud.<domain>: a 302 redirector
keyed by Authentik slug and host alias, plus /brand/* static assets from /srv/heardos-brand), optional FRONT_DOOR_HOST.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

PROXY_BLOCK = """\theader {{
\t\tStrict-Transport-Security "max-age=31536000"
\t\tX-Content-Type-Options nosniff
\t\tReferrer-Policy strict-origin-when-cross-origin
\t\t-Server
\t}}
\tencode zstd gzip
\treverse_proxy {upstream}:80 {{
\t\theader_up X-Forwarded-Proto https
\t\theader_up X-Forwarded-Port 443
\t\tlb_try_duration 15s
\t\tlb_try_interval 500ms
\t\ttransport http {{
\t\t\tdial_timeout 10s
\t\t\tresponse_header_timeout 120s
\t\t\tkeepalive 2m
\t\t\tkeepalive_idle_conns 32
\t\t}}
\t}}
\trequest_body {{
\t\tmax_size 0
\t}}
"""

# JSON access log for CrowdSec (vps/crowdsec-install.sh reads it); rolled by Caddy itself
ACCESSLOG = """# JSON access log for CrowdSec
(accesslog) {
\tlog {
\t\toutput file /var/log/caddy/access.log {
\t\t\troll_size 50MiB
\t\t\troll_keep 5
\t\t}
\t\tformat json
\t}
}
"""


def load_catalog() -> dict:
    return json.loads((HERE / "catalog.json").read_text())


def front_door_map(domain: str, hosts: list[str]) -> list[tuple[str, str]]:
    """(path-regex-alternation, target-host) for every installed app that has a slug or a public host."""
    out: list[tuple[str, str]] = []
    for app in load_catalog()["apps"]:
        for h in app.get("hosts", []):
            if h not in hosts:
                continue
            names = [n for n in {app.get("slug"), h} if n]
            out.append(("|".join(sorted(names, key=len, reverse=True)), f"{h}.{domain}"))
    return out


def render(env: dict) -> str:
    domain, email, upstream = env["DOMAIN"], env["ACME_EMAIL"], env["HOST_OVERLAY"]
    hosts = env["PUBLIC_HOSTS"].split() if isinstance(env["PUBLIC_HOSTS"], str) else list(env["PUBLIC_HOSTS"])
    site = ",\n".join(f"{h}.{domain}" for h in hosts)
    parts = [f"# {domain} public edge: TLS here, then over the Nebula overlay to Traefik on the home host ({upstream}:80).",
             "{", f"\temail {email}", "}", "", ACCESSLOG,
             site + " {", "\timport accesslog", PROXY_BLOCK.format(upstream=upstream).rstrip("\n"), "}"]
    if str(env.get("CADDY_FRONT_DOOR", "")) in ("1", "true", "yes"):
        fd = env.get("FRONT_DOOR_HOST") or "cloud"
        lines = [f"\t\t~^/(?:{alt})(/[^?]*)?(\\?.*)?$ \"https://{target}${{1}}${{2}}\"" for alt, target in front_door_map(domain, hosts)]
        parts += ["", f"# {fd}.{domain}: one address for everything; /<app> redirects to the app's own host.", f"{fd}.{domain} {{", "\timport accesslog",
                  "\theader Strict-Transport-Security \"max-age=31536000\"",
                  "\tmap {uri} {dest} {", *lines, f"\t\tdefault \"https://auth.{domain}/if/user/\"", "\t}",
                  "\thandle_path /brand/* {", "\t\troot * /srv/heardos-brand", "\t\theader Cache-Control \"public, max-age=86400\"",
                  "\t\tfile_server", "\t}", "\thandle {", "\t\tredir {dest} 302", "\t}", "}"]
    return "\n".join(parts) + "\n"


def _load_env_file(path: Path) -> dict:
    env: dict = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.split(" #")[0].strip()
            try:
                v = " ".join(shlex.split(v)) if v else ""
            except ValueError:
                v = v.strip("'\"")
            env[k.strip()] = v
    return env


if __name__ == "__main__":
    envf = Path(os.environ.get("HEARDOS_ENV", HERE / "heardos.env"))
    env = _load_env_file(envf)
    env.update({k: v for k, v in os.environ.items() if k in ("CADDY_FRONT_DOOR", "FRONT_DOOR_HOST")})
    sys.stdout.write(render(env))
