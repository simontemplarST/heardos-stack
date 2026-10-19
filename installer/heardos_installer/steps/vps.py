"""Steps on the lighthouse VPS: wait for boot, harden, Nebula lighthouse, Caddy edge."""
from __future__ import annotations

import importlib.util
import shlex

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
    d = s.tempdir()
    s.put_text((ctx.kit_root / "generic" / "vps" / "harden.sh").read_text(), f"{d}/harden.sh", 0o700)
    r = s.run(f"bash {shlex.quote(d)}/harden.sh; rc=$?; rm -rf {shlex.quote(d)}; exit $rc", timeout=1200, on_line=ctx.log)
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
    d = s.tempdir()
    s.put_text(_caddyfile(ctx), f"{d}/Caddyfile", 0o644)
    s.put_text((ctx.kit_root / "generic" / "vps" / "caddy-install.sh").read_text(), f"{d}/caddy-install.sh", 0o700)
    # caddy-install.sh takes the Caddyfile path as $1 (it copies it into its own private work dir before validating)
    qd = shlex.quote(d)
    r = s.run(f"bash {qd}/caddy-install.sh {qd}/Caddyfile; rc=$?; rm -rf {qd}; exit $rc", timeout=900, on_line=ctx.log)
    if not r.ok:
        return StepResult(False, message="Caddy install or config validation failed")
    return StepResult(True, message="public web entrance (Caddy) running with automatic HTTPS")


def vps_crowdsec(ctx: Ctx) -> StepResult:
    """CrowdSec + nftables bouncer on the VPS (kit: generic/vps/crowdsec-install.sh). Reads Caddy's JSON access log."""
    if not ctx.plan.get("CROWDSEC", True):
        return StepResult(True, message="attack blocking (CrowdSec) skipped")
    s = ctx.sessions.vps()
    d = s.tempdir()
    s.put_text((ctx.kit_root / "generic" / "vps" / "crowdsec-install.sh").read_text(), f"{d}/crowdsec-install.sh", 0o700)
    env = {"OVERLAY_CIDR": ctx.plan.get("OVERLAY_CIDR", "10.42.0.0/24"), "CS_ENROLL_KEY": ctx.vault.get("CROWDSEC_ENROLL_KEY") or "",
           "DOMAIN": ctx.plan["DOMAIN"], "HOME_PUBLIC_IP": ctx.plan.get("HOME_PUBLIC_IP", ""),
           "CS_NAME": f"{(ctx.plan.get('BRAND_TITLE') or 'heardos').lower().replace(' ', '-')}-edge"}
    r = s.run(f"bash {shlex.quote(d)}/crowdsec-install.sh; rc=$?; rm -rf {shlex.quote(d)}; exit $rc", env=env, timeout=900, on_line=ctx.log)
    if not r.ok:
        return StepResult(False, message="CrowdSec install failed (the site still works; retry or skip)")
    enrolled = " and enrolled in the CrowdSec console (accept it at app.crowdsec.net)" if env["CS_ENROLL_KEY"] else ""
    return StepResult(True, message=f"known attackers are blocked before they reach the sign-in page{enrolled}")


def vps_ssh_lock(ctx: Ctx) -> StepResult:
    """Close public SSH: sshd listens on the lighthouse's overlay address only (kit: generic/vps/ssh-overlay-only.sh).
    Only when this computer is on the overlay itself (it can reach the lighthouse's overlay SSH), otherwise the installer
    would lock itself out; then later steps and day-two jobs connect over the overlay (plan NEBULA_LH_SSH)."""
    import socket
    ov = ctx.plan["NEBULA_LH_OVERLAY"]
    try:
        socket.create_connection((ov, 22), timeout=5).close()
    except OSError:
        return StepResult(True, message=f"public SSH left open: this computer isn't on the private network yet. Once it is, "
                                        f"run generic/vps/ssh-overlay-only.sh {ov} on the VPS (or retry this step)")
    from ..remote.ssh import ensure_host_key, fingerprint, pinned_host_key
    pub = pinned_host_key(ctx.plan["NEBULA_LH_IP"], 22)
    if pub is None:
        return StepResult(False, message="the VPS host key isn't pinned yet; run the earlier VPS steps first")
    # same machine, so the overlay address must present the key already pinned for the public IP (no new trust)
    ensure_host_key(ov, 22, lambda _h, fp: fp == fingerprint(pub))
    s = ctx.sessions.vps()
    d = s.tempdir()
    qd = shlex.quote(d)
    s.put_text((ctx.kit_root / "generic" / "vps" / "ssh-overlay-only.sh").read_text(), f"{d}/ssh-overlay-only.sh", 0o700)
    r = s.run(f"bash {qd}/ssh-overlay-only.sh {shlex.quote(ov)}; rc=$?; rm -rf {qd}; exit $rc", timeout=300, on_line=ctx.log)
    if not r.ok:
        return StepResult(False, message="could not move SSH to the private network (public SSH unchanged)")
    from ..remote.ssh import Session
    try:   # prove a fresh login over the overlay before anything relies on it
        Session(ov, "root", key_path=ctx.sessions.key_path).connect().close()
    except Exception as e:  # noqa: BLE001 - surface any connect failure
        return StepResult(False, message=f"SSH over the private network failed after the switch ({type(e).__name__}); "
                                         f"undo on the VPS console with: bash ssh-overlay-only.sh --undo")
    return StepResult(True, outputs={"NEBULA_LH_SSH": ov}, message="SSH to the VPS now works only over the private network")


def steps() -> list[Step]:
    return [
        Step("vps.harden", "vps", vps_harden, deps=["vps.create"], title="Secure the VPS", timeout=1800, retries=2),
        Step("vps.nebula", "vps", vps_nebula, deps=["vps.harden", "nebula.ca"], title="Start the private-network hub on the VPS", retries=2),
        Step("vps.caddy", "vps", vps_caddy, deps=["vps.harden", "dns.publish"], title="Set up HTTPS on the VPS", timeout=1800, retries=2),
        Step("vps.ssh_lock", "vps", vps_ssh_lock, deps=["vps.nebula", "vps.caddy"], title="Close public SSH on the VPS",
             optional=True, retries=1),
        Step("vps.crowdsec", "vps", vps_crowdsec, deps=["vps.caddy"], title="Block known attackers (CrowdSec)",
             inputs=["CROWDSEC_ENROLL_KEY"], timeout=1200, retries=2, optional=True),
    ]
