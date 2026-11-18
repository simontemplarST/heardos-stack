#!/usr/bin/env python3
"""ngfw-publish.py: publish Elysium hostnames through the ngfw firewall (ngfw ADR 0104). Runs ON the app host.

  ngfw-publish.py publish HOST...    add or update each HOST (a name under DOMAIN, or @ for the apex) on the firewall
      [--backend URL] [--auth MODE]  explicit route (http://<container>:<port>, reached by this host's Traefik) and/or
                                     sign-in (forward_auth, oidc, none) instead of what the labels say: for a stack
                                     without public routers in its labels
  ngfw-publish.py unpublish HOST...  remove each HOST from the firewall
  ngfw-publish.py status             what the firewall publishes and what its outside checks say
  ngfw-publish.py stack DIR...       update the hosts of a stack (DIR/compose.yml, compose.override.yml) the firewall
                                     already publishes and strip from DIR/compose.yml the public routers it routes;
                                     never publishes a new host
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
A host with no public router here keeps the route the firewall already holds: once `stack` has stripped a stack's
public routers, the firewall is where its route lives (the LAN routers, Host(`<host>.<LAN_DOMAIN>`), stay here).

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
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPOST = "/outpost.goauthentik.io/"
FWD = "authentik-fwd"
NO_ROUTER = "no Traefik router for it here"
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


# ---- compose files: routers before the stack runs, and stripping the ones the firewall serves --------------------

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")


def _subst(v: str, env: dict) -> str:
    return _VAR.sub(lambda m: env.get(m.group(1)) or (m.group(2) or ""), v)


def _labels(spec: dict) -> dict:
    raw = spec.get("labels") or {}
    if isinstance(raw, list):
        raw = dict(x.split("=", 1) if "=" in x else (x, "") for x in raw)
    return {str(k): str(v) for k, v in raw.items()}


def compose_containers(docs: list[dict], env: dict) -> list[dict]:
    """The services of compose documents (later ones override earlier, like compose.override.yml) in docker-inspect
    shape, labels substituted from env, so routers_from_labels reads them as if the stack ran."""
    merged: dict[str, dict] = {}
    for d in docs:
        for name, spec in ((d or {}).get("services") or {}).items():
            m = merged.setdefault(name, {"labels": {}, "networks": set(), "container_name": ""})
            m["labels"].update(_labels(spec or {}))
            nets = (spec or {}).get("networks") or []
            m["networks"] |= set(nets if isinstance(nets, list) else nets.keys())
            m["container_name"] = (spec or {}).get("container_name") or m["container_name"]
    out = []
    for name, m in merged.items():
        out.append({"Name": "/" + (m["container_name"] or name),
                    "Config": {"Labels": {k: _subst(v, env) for k, v in m["labels"].items()}},
                    "NetworkSettings": {"Networks": {n: {} for n in (m["networks"] or {"default"})}}})
    return out


def public_hosts(routers: list[dict], domain: str) -> list[str]:
    """The hosts under DOMAIN that routers serve as main routers (not the LAN names)."""
    out = []
    for r in routers:
        h = _host_of(r["rule"])
        if h and (h == domain or h.endswith("." + domain)) and h not in out:
            out.append(h)
    return out


def strip_public_routers(text: str, names: set[str]) -> tuple[str, int]:
    """Remove every label line of the named routers (mapping or list form), keeping the file's layout."""
    pat = re.compile(r"traefik\.http\.routers\.(" + "|".join(re.escape(n) for n in sorted(names)) + r")\.") if names else None
    keep, dropped = [], 0
    for line in text.splitlines(keepends=True):
        if pat and pat.search(line) and re.match(r"\s*(-\s*)?[\"']?traefik\.http\.routers\.", line):
            dropped += 1
            continue
        keep.append(line)
    return "".join(keep), dropped


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
        return None, "", NO_ROUTER
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


def desired(host: str, env: dict, routers: list[dict], traefik_nets: set[str], current: dict | None, no_frame, extra,
            override: dict | None = None) -> tuple[dict, str]:
    """The service the firewall should hold for host, and a note on how it is routed. override: {backend, auth} given
    on the command line win over what the labels say."""
    domain = env["DOMAIN"]
    fq = fqdn(host, domain)
    route, auth, why = route_for(fq, routers, traefik_nets)
    override = override or {}
    if override.get("backend"):
        route, why = {"router": "", "backend": override["backend"], "priority": 50}, ""
    svc = dict(current or {})
    svc.pop("name", None)
    svc.update({"hostname": fq, "upstream": f"{env['HOST_OVERLAY']}:80", "edges": env["NGFW_EDGES"].split()})
    fa = frame_ancestors(host, domain, no_frame, extra)
    if fa:
        svc["frame_ancestors"] = fa
    else:
        svc.pop("frame_ancestors", None)
    if override.get("auth"):
        svc["auth"] = override["auth"]
    elif not svc.get("auth"):
        svc["auth"] = auth or "none"
    elif auth == "forward_auth" and svc["auth"] != "forward_auth":
        svc["auth"] = "forward_auth"      # the router gates it: say so, or the firewall would route around the gate
    if route:
        route["router"] = env["NGFW_ROUTER"]
        svc["route"] = route
        note = f"routed by the firewall to {route['backend']}"
    elif why == NO_ROUTER and (current or {}).get("route"):
        note = f"routed by the firewall to {current['route'].get('backend')} (its route lives there)"
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
    args, override, it = [], {}, iter(a for a in argv if a != "--dry-run")
    for a in it:
        if a in ("--backend", "--auth"):
            override[a[2:]] = next(it, "")
        else:
            args.append(a)
    if override.get("auth") not in (None, "forward_auth", "oidc", "none"):
        sys.exit("--auth must be forward_auth, oidc or none")
    if "backend" in override and not re.fullmatch(r"https?://[A-Za-z0-9._-]+(:\d{1,5})?/?", override["backend"]):
        sys.exit("--backend must look like http://<container>:<port>")
    if not args or args[0] not in ("publish", "unpublish", "status", "stack"):
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    if override and (args[0] != "publish" or len(args) != 2):
        sys.exit("--backend/--auth go with `publish` and exactly one HOST")
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
    if cmd == "stack":
        if not hosts:
            sys.exit("usage: ngfw-publish.py stack DIR...")
        return stack_cmd(fw, env, [Path(h) for h in hosts], dry)
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
    return publish_hosts(fw, env, by_host, current, routers, traefik_nets, no_frame, extra, dry, override)


def publish_hosts(fw, env, by_host, current, routers, traefik_nets, no_frame, extra, dry, override=None) -> int:
    rc = 0
    for fq, host in by_host.items():
        cur = current.get(fq)
        name = cur["name"] if cur else ("apex" if host in ("@", env["DOMAIN"]) else host)
        want, note = desired(host, env, routers, traefik_nets, cur, no_frame, extra, override)
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


def stack_cmd(fw, env: dict, dirs: list[Path], dry: bool) -> int:
    """For each stack: update the hosts the firewall already publishes from the stack's compose files, then strip from
    compose.yml the public routers of the hosts the firewall routes. It never publishes a new host: making an app
    public stays an explicit `publish` (the installer does it on install), and a host the firewall serves edge-only
    keeps its router here."""
    import yaml
    env = {**env, **{k: v for k, v in os.environ.items() if k.startswith("LAN_")}}
    no_frame, extra = caddy_rules()
    _, traefik_nets = traefik_view()
    rc = 0
    for d in dirs:
        files = [d / "compose.yml"] + ([d / "compose.override.yml"] if (d / "compose.override.yml").exists() else [])
        routers = routers_from_labels(compose_containers([yaml.safe_load(f.read_text()) for f in files], env))
        hosts = public_hosts(routers, env["DOMAIN"])
        if not hosts:
            print(f"{d.name}: no public routers")
            continue
        current = fw.services()
        for h in hosts:
            if h not in current:
                print(f"{d.name}: {h} is not published on the firewall; its router stays (`publish` makes it public)")
        known = {h: (h[: -len(env["DOMAIN"]) - 1] if h != env["DOMAIN"] else "@") for h in hosts if h in current}
        if known:
            prc = publish_hosts(fw, env, known, current, routers, traefik_nets, no_frame, extra, dry)
            rc |= prc
            if prc:
                print(f"{d.name}: not every host updated; nothing stripped (its own routers keep serving it)")
                continue
            if not dry:
                current = fw.services()
        strip = set()
        for h in known:
            route, _, why = route_for(h, routers, traefik_nets)
            if not (current.get(h) or {}).get("route") or (route is None and why != NO_ROUTER):
                print(f"{d.name}: {h} is edge-only on the firewall; its router stays")
                continue
            for r in routers:
                if h in _hosts_in(r["rule"]):        # the main router and its outpost router
                    strip.add(r["name"].split("@")[0])
        text = files[0].read_text()
        new, dropped = strip_public_routers(text, strip)
        if not dropped:
            print(f"{d.name}: nothing to strip")
            continue
        if dry:
            print(f"{d.name}: would strip {dropped} label lines of {', '.join(sorted(strip))}")
            continue
        backup = files[0].with_name(f"compose.yml.bak-ngfw-{time.strftime('%Y%m%d%H%M%S')}")
        backup.write_text(text)
        files[0].write_text(new)
        print(f"{d.name}: stripped {dropped} label lines of {', '.join(sorted(strip))} (the firewall routes them)")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
