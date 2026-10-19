"""Helpers shared by step modules: plan defaults, running kit scripts on the host, provider/DNS construction."""
from __future__ import annotations

import ipaddress
import re
import shlex
from typing import Callable, Iterable, Optional

from ..engine.step import Ctx, StepResult
from ..remote.plan import render_plan, SECRET_KEYS_BY_SIDE
from ..remote.ssh import redacted_tail

KIT_DEST = "/opt/heardos-kit"
PROVIDER_TOKEN_NAMES = {"cloudzy": "CLOUDZY_API_TOKEN", "linode": "LINODE_TOKEN", "vultr": "VULTR_API_KEY", "ovh": "OVH_CREDENTIALS", "byo": ""}


SSH_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")   # what useradd accepts by default; nothing a shell would expand


def ssh_user(plan: dict) -> str:
    u = str(plan.get("HOST_SSH_USER") or "root")
    if not SSH_USER.match(u):
        raise ValueError(f"login name {u!r} is not a plain Linux user name")
    return u


def overlay_ip(plan: dict, key: str) -> str:
    """An overlay address from the plan, checked to be a bare IP inside OVERLAY_CIDR before it goes near a shell."""
    ip = ipaddress.ip_address(str(plan[key]))
    if ip not in ipaddress.ip_network(plan.get("OVERLAY_CIDR", "10.42.0.0/24"), strict=False):
        raise ValueError(f"{key}={ip} is outside the private network {plan.get('OVERLAY_CIDR')}")
    return str(ip)


def host_key_confirm(ctx: Ctx, what: str, plan_key: str) -> Callable[[str, str], bool]:
    """Confirmation for the SSH host key of a machine the installer did not create (home server, your own VPS, OVH).
    A fingerprint in the plan (plan_key, e.g. HOST_SSH_FINGERPRINT) is compared silently; otherwise the operator is
    shown the fingerprint and must confirm it, before any password or command is sent."""
    def confirm(host: str, fp: str) -> bool:
        want = str(ctx.plan.get(plan_key) or "").strip()
        if want:
            if want != fp:
                ctx.log(f"host key of {host} is {fp}, but the plan expects {want}")
            return want == fp
        ctx.log(f"{what} {host} presents SSH host key {fp}")
        never = lambda: False   # noqa: E731 — only the operator can answer this one
        never.manual_only = True   # type: ignore[attr-defined]  (headless CLI: refuse instead of waiting forever)
        return ctx.checklist(
            f"Check the {what}'s identity",
            f"The {what} at {host} identifies itself with this SSH host key fingerprint:\n\n    {fp}\n\n"
            f"On the {what} itself (its console, or a session you already trust) run:\n\n"
            f"    ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub\n\n"
            f"Continue only if the SHA256 value matches exactly; otherwise cancel: something between you and the server "
            f"may be impersonating it. (Headless installs: put the value in the plan as {plan_key}.)",
            never)
    return confirm


def lan_domain(ip: str) -> str:
    return ip.replace(".", "-") + ".sslip.io"


def apply_defaults(plan: dict) -> dict:
    """Fill every value the kit needs that the user should not have to think about. Never overwrites what is set."""
    from ..kit import catalog
    from ..providers.select import local_timezone

    p = plan
    p.setdefault("TZ", local_timezone())
    p.setdefault("HEARDOS_ROOT", "/opt/heardos")
    p.setdefault("OVERLAY_CIDR", "10.42.0.0/24")
    net = ipaddress.ip_network(p["OVERLAY_CIDR"], strict=False)
    hosts = list(net.hosts())
    p.setdefault("NEBULA_LH_OVERLAY", str(hosts[0]))
    p.setdefault("HOST_OVERLAY", str(hosts[10]))
    p.setdefault("LAPTOP_OVERLAY", str(hosts[19]))
    p.setdefault("ACME_EMAIL", p.get("ADMIN_EMAIL", ""))
    p.setdefault("BRAND_TITLE", "HeardOS")
    p.setdefault("BRAND_PRIMARY", "#f2a35e")
    try:
        from ..brand.theme import derive
        p.setdefault("BRAND_COLOR", derive(p["BRAND_PRIMARY"]).homepage_color)
    except ValueError:
        p["BRAND_PRIMARY"], p["BRAND_COLOR"] = "#f2a35e", "orange"
    p.setdefault("CADDY_FRONT_DOOR", True)
    p.setdefault("CROWDSEC", True)
    p.setdefault("PROXY_SUBNET", "172.30.0.0/24")      # user-defined subnet: Traefik gets a fixed address on it
    p.setdefault("PROXY_IP_RANGE", "172.30.0.128/25")
    p.setdefault("TRAEFIK_PROXY_IP", "172.30.0.2")     # the only source apps trust identity / forwarded headers from
    p.setdefault("MEDIA_GID", "1500")
    p.setdefault("LAN_HTTP", "0")                      # plain-http OIDC redirects on LAN names: off (no LAN TLS in the kit)
    p.setdefault("HOST_SSH_USER", "root")
    p.setdefault("HOST_SSH_PORT", 22)
    p.setdefault("dns_mode", "cloudflare")
    if p.get("HOST_LAN_IP"):
        p.setdefault("LAN_DOMAIN", lan_domain(p["HOST_LAN_IP"]))
        p.setdefault("AUTHENTIK_URL", f"http://{p['HOST_LAN_IP']}:9000")
        p.setdefault("PORTAINER_URL", f"https://{p['HOST_LAN_IP']}:9443")
    apps = p.get("apps") or (catalog.profile_apps(p["profile"]) if p.get("profile") else [])
    p["apps"] = catalog.closure(apps)
    hosts_ = catalog.public_hosts(p["apps"])
    if p.get("CADDY_FRONT_DOOR"):
        hosts_.append(p.get("FRONT_DOOR_HOST", "cloud"))
    p["PUBLIC_HOSTS"] = hosts_
    return p


def missing_inputs(plan: dict, vault) -> list[str]:
    """Plain-word list of what the user still has to provide before an install can start."""
    need = []
    for k, label in (("DOMAIN", "your domain name"), ("ADMIN_USER", "an admin username"), ("ADMIN_EMAIL", "an admin email address"),
                     ("HOST_LAN_IP", "the home server's address"), ("provider", "a VPS provider")):
        if not plan.get(k):
            need.append(label)
    prov = plan.get("provider")
    if prov == "byo" and not plan.get("byo_ip"):
        need.append("your VPS's address")
    elif prov and PROVIDER_TOKEN_NAMES.get(prov) and not vault.get(PROVIDER_TOKEN_NAMES[prov]):
        need.append(f"a {prov.capitalize()} API token")
    if plan.get("dns_mode") == "cloudflare" and not vault.get("CF_API_TOKEN"):
        need.append("a Cloudflare API token (or choose manual DNS)")
    if not plan.get("apps"):
        need.append("at least one app")
    return need


def host_plan_text(ctx: Ctx) -> str:
    secrets = {k: ctx.vault.get(k) for k in SECRET_KEYS_BY_SIDE["host"] if ctx.vault.get(k)}
    return render_plan(ctx.plan, secrets, "host")


def run_kit(ctx: Ctx, script: str, args: Iterable[str] = (), *, env: Optional[dict] = None, timeout: int = 1800,
            cwd_dir: str = "generic") -> StepResult:
    """Write the host plan file, then run <kit>/<cwd_dir>/<script> on the host as root, streaming output into the step log."""
    s = ctx.sessions.host()
    plan_path = f"{KIT_DEST}/generic/heardos.env"
    s.run(f"install -d -m 755 {KIT_DEST}/generic", sudo=True, check=True)
    d = s.tempdir()
    tmp = f"{d}/heardos.env"
    s.put_text(host_plan_text(ctx), tmp, 0o600)
    s.run(f"install -m 600 {shlex.quote(tmp)} {plan_path} && rm -rf {shlex.quote(d)}", sudo=True, check=True)
    interp = "python3" if script.endswith(".py") else "bash"
    cmd = f"cd {KIT_DEST}/{cwd_dir} && HEARDOS_ENV={plan_path} {interp} ./{script} " + " ".join(shlex.quote(a) for a in args)
    full_env = {"HEARDOS_ENV": plan_path, **(env or {})}
    if ctx.dry_run:
        full_env["HEARDOS_DRY_RUN"] = "1"
    r = s.run(cmd, sudo=True, env=full_env, timeout=timeout, on_line=ctx.log)
    if not r.ok:
        # HEARDOS_OUTPUT values (tokens the vault has not seen yet) and env values masked; the runner redacts the rest
        tail = redacted_tail(r.stderr or r.stdout, tuple((env or {}).values()))
        return StepResult(False, message=f"{script} failed: {tail}", stdout=r.stdout)
    return StepResult(True, outputs=r.outputs(), message=f"{script} ok", stdout=r.stdout)


def provider_for(ctx: Ctx):
    from .. import providers
    from ..keys import DEFAULT_KEY
    name = ctx.plan["provider"]
    if name == "byo":
        return providers.make("byo", ip=ctx.plan.get("byo_ip", ""), key_path=DEFAULT_KEY,
                              confirm=host_key_confirm(ctx, "server", "VPS_SSH_FINGERPRINT"))
    prov = providers.make(name, ctx.vault.get(PROVIDER_TOKEN_NAMES[name]) or "")
    if name == "ovh":
        prov.autopay = bool(ctx.plan.get("ovh_autopay"))   # a real charge: only when the operator opted in
        prov.confirm = host_key_confirm(ctx, "new server", "VPS_SSH_FINGERPRINT")
    return prov


def dns_for(ctx: Ctx):
    from ..dns import CloudflareDNS, ManualDNS
    if ctx.plan.get("dns_mode") == "manual":
        return ManualDNS(checklist=ctx.checklist)
    return CloudflareDNS(ctx.vault.get("CF_API_TOKEN") or "", ctx.plan.get("CF_ZONE_ID", ""), ctx.plan["DOMAIN"])
