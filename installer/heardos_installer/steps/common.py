"""Helpers shared by step modules: plan defaults, running kit scripts on the host, provider/DNS construction."""
from __future__ import annotations

import ipaddress
import shlex
from typing import Iterable, Optional

from ..engine.step import Ctx, StepResult
from ..remote.plan import render_plan, SECRET_KEYS_BY_SIDE

KIT_DEST = "/opt/heardos-kit"
PROVIDER_TOKEN_NAMES = {"cloudzy": "CLOUDZY_API_TOKEN", "linode": "LINODE_TOKEN", "vultr": "VULTR_API_KEY", "ovh": "OVH_CREDENTIALS", "byo": ""}


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
    tmp = f"/tmp/heardos-plan-{ctx.state.install_id}.env"
    s.put_text(host_plan_text(ctx), tmp, 0o600)
    s.run(f"install -m 600 {tmp} {plan_path} && rm -f {tmp}", sudo=True, check=True)
    interp = "python3" if script.endswith(".py") else "bash"
    cmd = f"cd {KIT_DEST}/{cwd_dir} && HEARDOS_ENV={plan_path} {interp} ./{script} " + " ".join(shlex.quote(a) for a in args)
    full_env = {"HEARDOS_ENV": plan_path, **(env or {})}
    if ctx.dry_run:
        full_env["HEARDOS_DRY_RUN"] = "1"
    r = s.run(cmd, sudo=True, env=full_env, timeout=timeout, on_line=ctx.log)
    if not r.ok:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        return StepResult(False, message=f"{script} failed: " + " / ".join(tail)[:300], stdout=r.stdout)
    return StepResult(True, outputs=r.outputs(), message=f"{script} ok", stdout=r.stdout)


def provider_for(ctx: Ctx):
    from .. import providers
    from ..keys import DEFAULT_KEY
    name = ctx.plan["provider"]
    if name == "byo":
        return providers.make("byo", ip=ctx.plan.get("byo_ip", ""), key_path=DEFAULT_KEY)
    return providers.make(name, ctx.vault.get(PROVIDER_TOKEN_NAMES[name]) or "")


def dns_for(ctx: Ctx):
    from ..dns import CloudflareDNS, ManualDNS
    if ctx.plan.get("dns_mode") == "manual":
        return ManualDNS(checklist=ctx.checklist)
    return CloudflareDNS(ctx.vault.get("CF_API_TOKEN") or "", ctx.plan.get("CF_ZONE_ID", ""), ctx.plan["DOMAIN"])
