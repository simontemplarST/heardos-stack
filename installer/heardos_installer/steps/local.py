"""Steps that run on the operator's machine: key, provider/DNS validation, Nebula CA, VPS creation, DNS records."""
from __future__ import annotations

import base64

from ..dns import public_hosts_records
from ..engine.step import Ctx, Step, StepResult
from ..keys import DEFAULT_KEY, ensure_key
from ..providers import VpsHandle, VpsSpec, smart_default
from .common import dns_for, provider_for


def ssh_keygen(ctx: Ctx) -> StepResult:
    path, pub = ensure_key(DEFAULT_KEY)
    ctx.log(f"installer key: {path}")
    return StepResult(True, outputs={"SSH_KEY": str(path)}, message="SSH key ready")


def provider_validate(ctx: Ctx) -> StepResult:
    acct = provider_for(ctx).validate_token()
    ctx.log(acct.label)
    return StepResult(True, message=acct.label)


def dns_validate(ctx: Ctx) -> StepResult:
    d = dns_for(ctx)
    msg = d.validate()
    out = {"CF_ZONE_ID": d.zone_id} if getattr(d, "zone_id", "") else {}
    return StepResult(True, outputs=out, message=msg)


def nebula_ca(ctx: Ctx) -> StepResult:
    from ..nebula import CA
    import ipaddress
    prefix = ipaddress.ip_network(ctx.plan["OVERLAY_CIDR"], strict=False).prefixlen
    ca = CA.open(ctx.state.install_id, log=ctx.log)
    ca.ensure_ca(ctx.plan.get("BRAND_TITLE", "heardos").lower().replace(" ", "-") or "heardos")
    ca.sign("lighthouse", ctx.plan["NEBULA_LH_OVERLAY"], prefix, "lighthouse")
    ca.sign("host", ctx.plan["HOST_OVERLAY"], prefix, "servers")
    ca.sign("laptop", ctx.plan["LAPTOP_OVERLAY"], prefix, "admins")
    key_b64 = base64.b64encode((ca.dir / "ca.key").read_bytes()).decode()
    return StepResult(True, outputs={"NEBULA_CA_DIR": str(ca.dir)}, secrets={"NEBULA_CA_KEY": key_b64},
                      message="overlay certificates issued")


def vps_create(ctx: Ctx) -> StepResult:
    prov = provider_for(ctx)
    existing = ctx.state.handle("vps")
    if existing and existing.get("ip"):
        return StepResult(True, outputs={"NEBULA_LH_IP": existing["ip"]}, message=f"VPS already exists at {existing['ip']}")
    if ctx.plan["provider"] == "byo":
        h = prov.create(VpsSpec("byo", "byo", "byo", "byo", "byo"))
        ctx.state.set_handle("vps", h.to_json())
        return StepResult(True, outputs={"NEBULA_LH_IP": h.ip}, message=f"using your server {h.ip}")
    _, pub = ensure_key(DEFAULT_KEY)
    key_id = prov.ensure_ssh_key("heardos-installer", pub)
    image = prov.ubuntu_image()
    region, plan_id = ctx.plan.get("vps_region"), ctx.plan.get("vps_plan")
    monthly = ctx.plan.get("vps_monthly")
    if not (region and plan_id):
        choice = smart_default(prov.regions(), prov.plans, tz=ctx.plan.get("TZ"))
        if not choice:
            return StepResult(False, message="no region with an eligible plan is available right now")
        region, plan_id, monthly = choice.region.id, choice.plan.id, choice.plan.monthly_usd
        ctx.log(f"chose {choice.region.name} / {choice.plan.name} at ${monthly}/month ({choice.reason})")
    host = f"lh-{ctx.plan['DOMAIN'].replace('.', '-')}"[:60]
    h = prov.create(VpsSpec(hostname=host, region_id=region, plan_id=plan_id, image_id=image, ssh_key_id=key_id))
    ctx.state.set_handle("vps", h.to_json())   # saved before waiting, so a failed boot can still be destroyed
    ip = prov.wait_ip(h, timeout=900, on_line=ctx.log)
    ctx.state.set_handle("vps", h.to_json())
    secrets = {"VPS_ROOT_PASSWORD": prov.root_pass} if getattr(prov, "root_pass", None) else {}
    return StepResult(True, outputs={"NEBULA_LH_IP": ip, "vps_region": region, "vps_plan": plan_id, "vps_monthly": monthly},
                      secrets=secrets, message=f"VPS created at {ip}")


def dns_publish(ctx: Ctx) -> StepResult:
    recs = public_hosts_records(ctx.plan["DOMAIN"], ctx.plan["PUBLIC_HOSTS"], ctx.plan["NEBULA_LH_IP"])
    dns_for(ctx).publish(recs, on_line=ctx.log)
    return StepResult(True, message=f"{len(recs)} DNS records point at {ctx.plan['NEBULA_LH_IP']}")


def steps() -> list[Step]:
    return [
        Step("ssh.keygen", "local", ssh_keygen, title="Create the installer's SSH key"),
        Step("provider.validate", "local", provider_validate, deps=["ssh.keygen"], title="Check the VPS provider account",
             inputs=["CLOUDZY_API_TOKEN", "LINODE_TOKEN", "VULTR_API_KEY", "OVH_CREDENTIALS"]),
        Step("dns.validate", "local", dns_validate, title="Check DNS access", inputs=["CF_API_TOKEN"]),
        Step("nebula.ca", "local", nebula_ca, title="Issue private-network certificates", timeout=300),
        Step("vps.create", "local", vps_create, deps=["provider.validate"], title="Create the public server (VPS)",
             inputs=["CLOUDZY_API_TOKEN", "LINODE_TOKEN", "VULTR_API_KEY", "OVH_CREDENTIALS"], timeout=1200,
             check=lambda c: bool(c.state.handle("vps") and c.plan.get("NEBULA_LH_IP"))),
        Step("dns.publish", "local", dns_publish, deps=["vps.create", "dns.validate"], title="Point your domain at the VPS",
             inputs=["CF_API_TOKEN"], timeout=2400, retries=2),
    ]
