#!/usr/bin/env python3
"""caddyfile.py — render the lighthouse Caddyfile (stdlib only; used by 30-vps-caddy.sh and imported by the installer).

  caddyfile.py            reads the plan file ($HEARDOS_ENV or ../heardos.env) and prints the Caddyfile
Inputs: DOMAIN, ACME_EMAIL, HOST_OVERLAY, PUBLIC_HOSTS, optional CADDY_FRONT_DOOR=1 (adds cloud.<domain>: a 302 redirector
keyed by Authentik slug and host alias, plus /brand/* static assets from /srv/heardos-brand), optional FRONT_DOOR_HOST.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

HSTS = "max-age=31536000; includeSubDomains"

SITE_HEADERS = """\theader {{
\t\tStrict-Transport-Security "{hsts}"
\t\tX-Content-Type-Options nosniff
\t\tReferrer-Policy strict-origin-when-cross-origin
\t\t-Server
\t}}
\tencode zstd gzip
\trequest_body {{
\t\tmax_size 0
\t}}
"""

# HeardOS desktop: apps open as windows in https://dash.<domain> (docs/web-desktop.md). For those hosts only, drop
# X-Frame-Options and let the app itself plus the desktop (and, for OnlyOffice, Nextcloud) frame it. Keep the quotes:
# unquoted self/none would be read as host names.
FRAMING = """\t\t\theader_down -X-Frame-Options
\t\t\theader_down Content-Security-Policy "frame-ancestors 'self'" "frame-ancestors 'self' {anc}"
\t\t\theader_down Content-Security-Policy "frame-ancestors 'none'" "frame-ancestors {anc}"
\t\t\theader_down +Content-Security-Policy "frame-ancestors 'self' {anc}"
"""

PROXY = """\t\treverse_proxy {upstream}:80 {{
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Forwarded-Port 443
{framing}\t\t\tlb_try_duration 15s
\t\t\tlb_try_interval 500ms
\t\t\ttransport http {{
\t\t\t\tdial_timeout 10s
\t\t\t\tresponse_header_timeout 120s
\t\t\t\tkeepalive 2m
\t\t\t\tkeepalive_idle_conns 32
\t\t\t}}
\t\t}}
"""

# Hosts the desktop never frames keep the app's own X-Frame-Options / frame-ancestors: a sign-in or vault page inside
# someone else's frame is what clickjacking needs. dash and hub are top-level pages, not windows.
NO_FRAME = ("auth", "vault", "dash", "hub")
# extra framers per host (Nextcloud opens documents in an OnlyOffice frame)
EXTRA_ANCESTORS = {"office": ("docs",)}

# JSON access log for CrowdSec (vps/crowdsec-install.sh reads it); rolled by Caddy itself. Query values that carry
# secrets (OIDC code/state, Subsonic p/t/s, share tokens) are redacted; CrowdSec only needs remote_ip, status, method,
# host, user agent and the uri path, which stay. Caddy already redacts Cookie/Authorization; deleting them is belt+braces.
ACCESSLOG = """# JSON access log for CrowdSec
(accesslog) {
\tlog {
\t\toutput file /var/log/caddy/access.log {
\t\t\troll_size 50MiB
\t\t\troll_keep 5
\t\t}
\t\tformat filter {
\t\t\twrap json
\t\t\tfields {
\t\t\t\trequest>uri query {
\t\t\t\t\treplace code REDACTED
\t\t\t\t\treplace state REDACTED
\t\t\t\t\treplace p REDACTED
\t\t\t\t\treplace t REDACTED
\t\t\t\t\treplace s REDACTED
\t\t\t\t\treplace token REDACTED
\t\t\t\t}
\t\t\t\trequest>headers>Authorization delete
\t\t\t\trequest>headers>Cookie delete
\t\t\t}
\t\t}
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


def _proxy(upstream: str, ancestors: list[str] | None) -> str:
    framing = FRAMING.format(anc=" ".join(ancestors)) if ancestors else ""
    return PROXY.format(upstream=upstream, framing=framing)


def render(env: dict) -> str:
    domain, email, upstream = env["DOMAIN"], env["ACME_EMAIL"], env["HOST_OVERLAY"]
    hosts = env["PUBLIC_HOSTS"].split() if isinstance(env["PUBLIC_HOSTS"], str) else list(env["PUBLIC_HOSTS"])
    site = ",\n".join(f"{h}.{domain}" for h in hosts)
    dash = f"https://dash.{domain}"
    body: list[str] = []
    # reverse_proxy header_down takes no matcher, so each framing policy gets its own handle (first match wins)
    noframe = [f"{h}.{domain}" for h in hosts if h in NO_FRAME]
    if noframe:
        body += [f"\t@noframe host {' '.join(noframe)}", "\thandle @noframe {", _proxy(upstream, None).rstrip("\n"), "\t}"]
    for h, extra in EXTRA_ANCESTORS.items():
        if h in hosts:
            anc = [dash] + [f"https://{x}.{domain}" for x in extra]
            body += [f"\t@frame_{h} host {h}.{domain}", f"\thandle @frame_{h} {{", _proxy(upstream, anc).rstrip("\n"), "\t}"]
    body += ["\thandle {", _proxy(upstream, [dash]).rstrip("\n"), "\t}"]
    parts = [f"# {domain} public edge: TLS here, then over the Nebula overlay to Traefik on the home host ({upstream}:80).",
             "{", f"\temail {email}", "\tadmin localhost:2019", "}", "", ACCESSLOG,
             site + " {", "\timport accesslog", SITE_HEADERS.format(hsts=HSTS).rstrip("\n"), *body, "}"]
    if str(env.get("CADDY_FRONT_DOOR", "")) in ("1", "true", "yes"):
        fd = env.get("FRONT_DOOR_HOST") or "cloud"
        # only public app hosts are mapped: never add LAN-only targets (Proxmox, router UIs) here, this host answers
        # anyone without signing in and would disclose internal names/addresses
        lines = [f"\t\t~^/(?:{alt})(/[^?]*)?(\\?.*)?$ \"https://{target}${{1}}${{2}}\"" for alt, target in front_door_map(domain, hosts)]
        parts += ["", f"# {fd}.{domain}: one address for everything; /<app> redirects to the app's own host.", f"{fd}.{domain} {{", "\timport accesslog",
                  f"\theader Strict-Transport-Security \"{HSTS}\"",
                  "\tmap {uri} {dest} {", *lines, f"\t\tdefault \"https://auth.{domain}/if/user/\"", "\t}",
                  "\thandle_path /brand/* {", "\t\troot * /srv/heardos-brand", "\t\theader Cache-Control \"public, max-age=86400\"",
                  "\t\tfile_server", "\t}", "\thandle {", "\t\tredir {dest} 302", "\t}", "}"]
    return "\n".join(parts) + "\n"


def _env_value(v: str) -> str:
    """Shell-ish value: a quoted value is taken up to its closing quote (a " #" inside stays); an unquoted one loses a
    trailing " # comment"."""
    v = v.strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        if end > 0:
            return v[1:end]
    v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
    try:
        return " ".join(shlex.split(v)) if v else ""
    except ValueError:
        return v.strip("'\"")


def _load_env_file(path: Path) -> dict:
    env: dict = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = _env_value(v)
    return env


if __name__ == "__main__":
    envf = Path(os.environ.get("HEARDOS_ENV", HERE / "heardos.env"))
    env = _load_env_file(envf)
    env.update({k: v for k, v in os.environ.items() if k in ("CADDY_FRONT_DOOR", "FRONT_DOOR_HOST")})
    sys.stdout.write(render(env))
