#!/usr/bin/env python3
"""ngfw-publish.py: publish Elysium hostnames through the ngfw firewall (ngfw ADR 0104). Runs ON the app host.

  ngfw-publish.py publish HOST...    add or update each HOST (a name under DOMAIN, or @ for the apex) on the firewall
  ngfw-publish.py unpublish HOST...  remove each HOST from the firewall
  ngfw-publish.py status             what the firewall publishes and what its outside checks say
  --dry-run (or HEARDOS_DRY_RUN=1)   print what would change, change nothing

The firewall is the registry of record for public hostnames: it renders the edge's (Beacon) Caddyfile and the routes
this host's Traefik pulls. This script only describes a hostname to it:
  * route: the Traefik router for Host(`<host>.<domain>`) found in the docker labels or in dynamic/*.yml. A host whose
    only routers are the main one (and its Authentik outpost) gets a firewall route to the same backend; anything else
    (extra path routers, a serversTransport, a backend Traefik cannot reach by name) is published edge-only and keeps
    its routes here.
  * auth: forward_auth when the router uses authentik-fwd, otherwise none for a new host; an existing value on the
    firewall is kept (set oidc there for an app that signs users in itself).
  * framing: the same rules as lib/caddyfile.py (NO_FRAME, EXTRA_ANCESTORS).
Fields the firewall holds that this script does not derive (auth, app, max_body_mb, enabled) are kept as they are.

Settings (heardos.env): DOMAIN, HOST_OVERLAY, HEARDOS_ROOT, NGFW_URL (https://<a name in the firewall's management
certificate>), NGFW_ADDR (the firewall's overlay address that name is reached at), NGFW_CA (the pinned certificate),
NGFW_EDGES (Beacon edges, space separated), NGFW_ROUTER (the firewall's name for this host's Traefik). The API key
(scope "publish") is read from NGFW_KEY_FILE, default $HEARDOS_ROOT/secrets/ngfw-publish.key. Prints no secrets.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shlex
import socket
import ssl
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPOST = "/outpost.goauthentik.io/"
FWD = "authentik-fwd"
MANAGED = "managed by Elysium (ngfw-publish)"


def load_env() -> dict:
    """heardos.env (HEARDOS_ENV or ../heardos.env) overlaid with the process environment."""
    env = {}
    path = Path(os.environ.get("HEARDOS_ENV") or HERE.parent / "heardos.env")
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                parts = shlex.split(v) if v.strip() else [""]
                env[k.strip()] = " ".join(parts)
    for k, v in os.environ.items():
        if k.startswith(("NGFW_", "DOMAIN", "HOST_OVERLAY", "HEARDOS_")):
            env[k] = v
    return env


def caddy_rules():
    spec = importlib.util.spec_from_file_location("heardos_caddyfile", HERE / "caddyfile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.NO_FRAME, mod.EXTRA_ANCESTORS


def fqdn(host: str, domain: str) -> str:
    return domain if host in ("@", domain) else f"{host}.{domain}"


def frame_ancestors(host: str, domain: str, no_frame, extra) -> list[str]:
    """The origins allowed to frame host, as caddyfile.py renders them; [] keeps the app's own headers."""
    if host in no_frame:
        return []
    return [f"https://dash.{domain}"] + [f"https://{e}.{domain}" for e in extra.get(host, ())]


# ---- Traefik: which router serves a hostname, from docker labels and the file provider ---------------------------

def _host_of(rule: str) -> str:
    m = re.fullmatch(r"\s*Host\(`([^`]+)`\)\s*", rule or "")
    return m.group(1).lower() if m else ""


def _hosts_in(rule: str) -> set[str]:
    return {h.lower() for h in re.findall(r"Host\(`([^`]+)`\)", rule or "")}


def routers_from_labels(containers: list[dict]) -> list[dict]:
    """Every docker-label router: name, rule, middlewares, and the backend URL Traefik reaches it at by name."""
    out = []
    for c in containers:
        labels = (c.get("Config") or {}).get("Labels") or {}
        if labels.get("traefik.enable") != "true":
            continue
        name = c.get("Name", "").lstrip("/")
        nets = set(((c.get("NetworkSettings") or {}).get("Networks") or {}).keys())
        ports = {m.group(1): v for k, v in labels.items() if (m := re.fullmatch(r"traefik\.http\.services\.([^.]+)\.loadbalancer\.server\.port", k))}
        schemes = {m.group(1): v for k, v in labels.items() if (m := re.fullmatch(r"traefik\.http\.services\.([^.]+)\.loadbalancer\.server\.scheme", k))}
        for k, rule in labels.items():
            m = re.fullmatch(r"traefik\.http\.routers\.([^.]+)\.rule", k)
            if not m:
                continue
            r = m.group(1)
            mws = [x.strip() for x in labels.get(f"traefik.http.routers.{r}.middlewares", "").split(",") if x.strip()]
            svc = labels.get(f"traefik.http.routers.{r}.service") or (next(iter(ports)) if len(ports) == 1 else "")
            backend = ""
            if svc in ports:
                backend = f"{schemes.get(svc, 'http')}://{name}:{ports[svc]}"
            out.append({"name": f"{r}@docker", "rule": rule, "middlewares": mws, "service": svc, "backend": backend,
                        "networks": nets, "transport": ""})
    return out


def routers_from_files(docs: list[dict]) -> list[dict]:
    out = []
    for d in docs:
        http_ = (d or {}).get("http") or {}
        services = http_.get("services") or {}
        for r, spec in (http_.get("routers") or {}).items():
            svc = (spec.get("service") or "").split("@")[0]
            lb = (services.get(svc) or {}).get("loadBalancer") or {}
            servers = lb.get("servers") or []
            out.append({"name": f"{r}@file", "rule": spec.get("rule", ""), "middlewares": [m.split("@")[0] for m in spec.get("middlewares") or []],
                        "service": svc, "backend": servers[0].get("url", "") if len(servers) == 1 else "",
                        "networks": None, "transport": lb.get("serversTransport", "")})
    return out


def route_for(fq: str, routers: list[dict], traefik_nets: set[str]) -> tuple[dict | None, str, str]:
    """(route or None, auth derived from the router, why it is edge-only). A route needs exactly one main router for the
    host (rule Host(`fq`)) plus at most its outpost router, a single backend Traefik reaches by name or address, and no
    custom transport."""
    mine = [r for r in routers if fq in _hosts_in(r["rule"])]
    mains = [r for r in mine if _host_of(r["rule"]) == fq]
    others = [r for r in mine if r not in mains and OUTPOST not in r["rule"]]
    if not mains:
        return None, "", "no Traefik router for it here"
    main = mains[0]
    auth = "forward_auth" if any(m.split("@")[0] == FWD for m in main["middlewares"]) else ""
    if len(mains) > 1 or others:
        return None, auth, "more than one router serves it (path rules stay on this host's Traefik)"
    if not main["backend"]:
        return None, auth, "its router has no single backend"
    if main["transport"]:
        return None, auth, "its backend needs a custom serversTransport"
    if main["networks"] is not None and not (main["networks"] & traefik_nets):
        return None, auth, "Traefik shares no network with its container"
    return {"router": "", "backend": main["backend"], "priority": 50}, auth, ""


def desired(host: str, env: dict, routers: list[dict], traefik_nets: set[str], current: dict | None, no_frame, extra) -> tuple[dict, str]:
    """The service the firewall should hold for host, and a note on how it is routed."""
    domain = env["DOMAIN"]
    fq = fqdn(host, domain)
    route, auth, why = route_for(fq, routers, traefik_nets)
    svc = dict(current or {})
    svc.pop("name", None)
    svc.update({"hostname": fq, "upstream": f"{env['HOST_OVERLAY']}:80", "edges": env["NGFW_EDGES"].split()})
    fa = frame_ancestors(host, domain, no_frame, extra)
    if fa:
        svc["frame_ancestors"] = fa
    else:
        svc.pop("frame_ancestors", None)
    if not svc.get("auth"):
        svc["auth"] = auth or "none"
    elif auth == "forward_auth" and svc["auth"] != "forward_auth":
        svc["auth"] = "forward_auth"      # the router gates it: say so, or the firewall would route around the gate
    if route:
        route["router"] = env["NGFW_ROUTER"]
        svc["route"] = route
        note = f"routed by the firewall to {route['backend']}"
    else:
        svc.pop("route", None)
        note = f"edge only: {why}"
    if not svc.get("comment"):
        svc["comment"] = MANAGED
    return svc, note


# ---- the firewall's API ---------------------------------------------------------------------------------------------

class Firewall:
    def __init__(self, env: dict):
        u = urllib.parse.urlsplit(env["NGFW_URL"])
        self.name, self.port = u.hostname, u.port or 443
        self.addr = env.get("NGFW_ADDR") or self.name
        self.ctx = ssl.create_default_context(cafile=env["NGFW_CA"])
        key_file = env.get("NGFW_KEY_FILE") or f"{env.get('HEARDOS_ROOT', '/opt/heardos')}/secrets/ngfw-publish.key"
        self.key = Path(key_file).read_text().strip()

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        sock = socket.create_connection((self.addr, self.port), timeout=60)
        conn = http.client.HTTPSConnection(self.name, self.port, context=self.ctx, timeout=120)
        conn.sock = self.ctx.wrap_socket(sock, server_hostname=self.name)   # reach the address, verify the name
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw) if raw else {}
        except ValueError:
            return r.status, {"error": raw.decode(errors="replace")[:300]}

    def services(self) -> dict:
        st, d = self.call("GET", "/api/v1/publish")
        if st != 200:
            sys.exit(f"ngfw: GET /api/v1/publish answered {st}: {d.get('error', d)}")
        return {s["hostname"]: s for s in d.get("services", [])}


def _clean(svc: dict) -> dict:
    return {k: v for k, v in svc.items() if v not in (None, "", [], {}) or k == "max_body_mb" and v == 0}


def traefik_view() -> tuple[list[dict], set[str]]:
    ids = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, check=True).stdout.split()
    cs = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True, check=True).stdout) if ids else []
    routers = routers_from_labels(cs)
    traefik_nets: set[str] = set()
    for c in cs:
        if c.get("Name", "").lstrip("/") == "traefik":
            traefik_nets = set(((c.get("NetworkSettings") or {}).get("Networks") or {}).keys())
    try:
        import yaml  # PyYAML is on the host; without it only docker labels are read
        docs = [yaml.safe_load(p.read_text()) for p in sorted(Path(load_env().get("HEARDOS_ROOT", "/opt/heardos"), "dynamic").glob("*.yml"))]
        routers += routers_from_files(docs)
    except ImportError:
        pass
    return routers, traefik_nets


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv or os.environ.get("HEARDOS_DRY_RUN") == "1"
    args = [a for a in argv if a != "--dry-run"]
    if not args or args[0] not in ("publish", "unpublish", "status"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    env = load_env()
    missing = [k for k in ("DOMAIN", "HOST_OVERLAY", "NGFW_URL", "NGFW_CA", "NGFW_EDGES", "NGFW_ROUTER") if not env.get(k)]
    if missing:
        sys.exit("ngfw: heardos.env lacks " + ", ".join(missing) + " (the edge is not published by a firewall here)")
    fw = Firewall(env)
    cmd, hosts = args[0], args[1:]
    if cmd == "status":
        st, d = fw.call("GET", "/api/v1/publish/status")
        for s in d.get("services", []):
            print(f"{s['hostname']:32} dns={s['dns']['status']:4} sign-in={s['gate']['status']:4} cert={s['cert']['status']:4}  "
                  + "; ".join(c["detail"] for c in (s["gate"], s["dns"], s["cert"]) if c["status"] in ("fail", "warn")))
        for e in d.get("edges", []) + d.get("routers", []):
            print(f"{e['name']:12} {'current' if e.get('current') else 'NOT current'}  last pull {e.get('last_pull', '-')}")
        return 0
    if not hosts:
        sys.exit(f"usage: ngfw-publish.py {cmd} HOST...")
    current = fw.services()
    by_host = {fqdn(h, env["DOMAIN"]): h for h in hosts}
    rc = 0
    if cmd == "unpublish":
        for fq in by_host:
            cur = current.get(fq)
            if not cur:
                print(f"{fq}: not published")
                continue
            if dry:
                print(f"{fq}: would remove service {cur['name']}")
                continue
            st, d = fw.call("DELETE", f"/api/v1/publish/services/{urllib.parse.quote(cur['name'])}")
            print(f"{fq}: removed (commit {d.get('commit', {}).get('id')})" if st == 200 else f"{fq}: {st} {d.get('error')}")
            rc |= st != 200
        return rc
    no_frame, extra = caddy_rules()
    routers, traefik_nets = traefik_view()
    for fq, host in by_host.items():
        cur = current.get(fq)
        name = cur["name"] if cur else ("apex" if host in ("@", env["DOMAIN"]) else host)
        want, note = desired(host, env, routers, traefik_nets, cur, no_frame, extra)
        have = {k: v for k, v in (cur or {}).items() if k != "name"}
        if _clean(want) == _clean(have):
            print(f"{fq}: unchanged ({note})")
            continue
        if dry:
            print(f"{fq}: would {'update' if cur else 'add'} service {name} ({note})")
            for k in sorted(set(want) | set(have)):
                if _clean({k: want.get(k)}) != _clean({k: have.get(k)}):
                    print(f"    {k}: {json.dumps(have.get(k))} -> {json.dumps(want.get(k))}")
            continue
        st, d = fw.call("PUT", f"/api/v1/publish/services/{urllib.parse.quote(name)}", _clean(want))
        if st == 200:
            print(f"{fq}: {'updated' if cur else 'published'} as {name}, {note} (commit {d.get('commit', {}).get('id')})")
        else:
            rc = 1
            probs = "; ".join(f"{p.get('path')}: {p.get('message')}" for p in d.get("problems", []) if p.get("severity") == "error")
            print(f"{fq}: {st} {d.get('error')} {probs}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
