"""Steps on the lighthouse VPS: wait for boot, harden, Nebula lighthouse, Caddy edge."""
from __future__ import annotations

import importlib.util

from ..dns import Record, wait_resolved
from ..engine.step import Ctx, Step, StepResult
from ..keys import DEFAULT_KEY
from ..remote.ssh import wait_cloud_init, wait_ssh


def _session(ctx: Ctx):
    return wait_ssh(ctx.plan["NEBULA_LH_IP"], "root", key_path=DEFAULT_KEY, timeout=900, on_line=ctx.log)


def vps_harden(ctx: Ctx) -> StepResult:
    s = _session(ctx)
    ctx.sessions._vps = s  # noqa: SLF001 — reuse the warm connection
    wait_cloud_init(s, on_line=ctx.log)
    s.put_text((ctx.kit_root / "generic" / "vps" / "harden.sh").read_text(), "/tmp/heardos-harden.sh", 0o700)
    r = s.run("bash /tmp/heardos-harden.sh && rm -f /tmp/heardos-harden.sh", timeout=1200, on_line=ctx.log)
    if not r.ok:
        return StepResult(False, message="hardening the VPS failed")
    return StepResult(True, outputs=r.outputs(), message="VPS hardened (key-only SSH, firewall, auto-updates)")


def vps_nebula(ctx: Ctx) -> StepResult:
    from ..nebula import CA, install_node, render_config
    s = ctx.sessions.vps()
    arch = s.run("uname -m", check=True).stdout.strip()
    ca = CA.open(ctx.state.install_id, log=ctx.log)
    cfg = render_config(ctx.kit_root, "lighthouse", ctx.plan, lighthouse=True, ingress=False)
    install_node(s, ca, "lighthouse", cfg, arch, ctx.kit_root, ctx.log)
    return StepResult(True, message="private network hub running on the VPS")


def _caddyfile(ctx: Ctx) -> str:
    spec = importlib.util.spec_from_file_location("heardos_caddyfile", ctx.kit_root / "generic" / "lib" / "caddyfile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    env = {k: ctx.plan[k] for k in ("DOMAIN", "ACME_EMAIL", "HOST_OVERLAY", "PUBLIC_HOSTS") if k in ctx.plan}
    env["CADDY_FRONT_DOOR"] = "1" if ctx.plan.get("CADDY_FRONT_DOOR") else ""
    env["FRONT_DOOR_HOST"] = ctx.plan.get("FRONT_DOOR_HOST", "cloud")
    return mod.render(env)


def vps_caddy(ctx: Ctx) -> StepResult:
    # wait until public resolvers see auth.<domain> -> VPS, so the first ACME attempt succeeds (rate limits are unforgiving)
    rec = Record("A", f"auth.{ctx.plan['DOMAIN']}", ctx.plan["NEBULA_LH_IP"])
    if not wait_resolved([rec], timeout=900, on_line=ctx.log):
        return StepResult(False, message=f"{rec.name} does not resolve to the VPS yet; certificates would fail")
    s = ctx.sessions.vps()
    s.put_text(_caddyfile(ctx), "/tmp/Caddyfile.heardos", 0o644)
    s.put_text((ctx.kit_root / "generic" / "vps" / "caddy-install.sh").read_text(), "/tmp/heardos-caddy.sh", 0o700)
    r = s.run("bash /tmp/heardos-caddy.sh && rm -f /tmp/heardos-caddy.sh", timeout=900, on_line=ctx.log)
    if not r.ok:
        return StepResult(False, message="Caddy install or config validation failed")
    return StepResult(True, message="public web entrance (Caddy) running with automatic HTTPS")


def steps() -> list[Step]:
    return [
        Step("vps.harden", "vps", vps_harden, deps=["vps.create"], title="Secure the VPS", timeout=1800, retries=2),
        Step("vps.nebula", "vps", vps_nebula, deps=["vps.harden", "nebula.ca"], title="Start the private-network hub on the VPS", retries=2),
        Step("vps.caddy", "vps", vps_caddy, deps=["vps.harden", "dns.publish"], title="Set up HTTPS on the VPS", timeout=1800, retries=2),
    ]
