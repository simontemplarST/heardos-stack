"""Adopt a HeardOS that was set up before (or without) this installer, so the day-two console, the harness and the CLI
can manage it:  heardos-cli adopt SPEC.json [--vault FILE] [--secrets FILE] [--apply]

The spec is a JSON file: {"install_id": ..., "plan": {...plan keys...}, "ssh": {"host": "root@192.168.10.20", "vps": "lh"}}.
  plan   the same keys an installer-made install has (DOMAIN, HOST_LAN_IP, apps, provider "byo" + byo_ip, NEBULA_LH_IP,
         NEBULA_LH_SSH, HOST_OVERLAY, PORTAINER_URL, ...). HOST_SSH_FINGERPRINT / VPS_SSH_FINGERPRINT pin the servers'
         SSH host keys without a prompt (ssh-keygen -lf on the server shows them).
  ssh    the operator's own ssh targets (as `ssh TARGET` works today), used once to add the installer's key.
--vault    KEY=VALUE lines that go into the encrypted vault (AUTHENTIK_TOKEN, PORTAINER_TOKEN, CF_API_TOKEN, ...).
--secrets  KEY=VALUE lines seeded into $HEARDOS_ROOT/secrets/secrets.env on the host (the kit's names, e.g.
           AUTHENTIK_MEALIE_CLIENT_SECRET, PAPERLESS_DB_PASSWORD), so 50-app.sh reuses the live values instead of
           generating new ones. Existing keys there are never overwritten.

What adopt does: records the install (state + plan) with every install step marked done except the ones it runs for
real (installer SSH key, first contact with the home server, kit upload), writes the host's plan file, makes the
$HEARDOS_ROOT layout (empty), seeds the secrets, and lists the stacks that still run in their old layout as
plan["adopted_pending"]. Reads (status, logs, health, people) and start/stop/restart work right away; installs, settings,
removals and updates of a pending stack are refused until `heardos-cli migrate` has moved it into the kit layout.
Without --apply nothing is written anywhere.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .engine.step import StepStatus
from .keys import DEFAULT_KEY, ensure_key
from .remote.ssh import ensure_host_key, known_hosts_file
from .session import Installation
from .steps.common import KIT_DEST, host_plan_text

REAL_STEPS = {"ssh.keygen", "host.probe", "host.push_kit"}     # everything else is marked done by adoption
ADOPTED_MSG = "adopted: already in place before the installer"


def read_kv(path: Optional[str]) -> dict[str, str]:
    """KEY=VALUE lines (comments and blanks skipped); values keep everything after the first '='."""
    out: dict[str, str] = {}
    if not path:
        return out
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


def load_spec(path: str) -> dict:
    spec = json.loads(Path(path).read_text())
    for k in ("install_id", "plan"):
        if k not in spec:
            raise ValueError(f"spec needs '{k}'")
    plan = spec["plan"]
    for k in ("DOMAIN", "HOST_LAN_IP", "ADMIN_USER", "ADMIN_EMAIL", "apps"):
        if not plan.get(k):
            raise ValueError(f"plan needs {k}")
    plan.setdefault("provider", "byo")
    return spec


def pending_stacks(plan: dict) -> list[str]:
    """Catalogue apps of the plan plus the two core stacks: all of them run in the old layout until migrated."""
    return ["traefik", "authentik"] + list(plan.get("apps") or [])


def install_pubkey_via_ssh(target: str, pub: str, log) -> None:
    """Add the installer's public key with the operator's own ssh (BatchMode: their agent/config must already work)."""
    cmd = ("install -d -m 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
           f"grep -qF {shlex.quote(pub)} ~/.ssh/authorized_keys || echo {shlex.quote(pub)} >> ~/.ssh/authorized_keys")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target, cmd], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"could not add the installer key on {target}: {(r.stderr or r.stdout).strip()[-200:]}")
    log(f"installer key present on {target}")


def pin_host_key(host: str, port: int, fingerprint: str, log) -> None:
    def confirm(h: str, fp: str) -> bool:
        if fp != fingerprint:
            log(f"{h}: host key is {fp}, the spec says {fingerprint}")
        return fp == fingerprint
    ensure_host_key(host, port, confirm)
    log(f"host key of {host} pinned in {known_hosts_file()}")


def mark_adopted(inst: Installation, log) -> list[str]:
    """Every step except the real ones is recorded as done; returns the ids marked."""
    marked = []
    for s in inst.steps():
        if s.id in REAL_STEPS:
            continue
        inst.state.set_status(s.id, StepStatus.done, ADOPTED_MSG)
        marked.append(s.id)
    inst.state.set_handle("adopted", {"pending": pending_stacks(inst.plan)})
    inst.plan["adopted_pending"] = pending_stacks(inst.plan)
    inst.state.save()
    log(f"{len(marked)} install steps recorded as already done")
    return marked


def seed_host(inst: Installation, secrets: dict[str, str], log) -> None:
    """Plan file, empty layout and secrets.env on the host (existing secret keys are kept)."""
    s = inst.sessions.host()
    root = inst.plan.get("HEARDOS_ROOT", "/opt/heardos")
    q = shlex.quote
    d = s.tempdir()
    s.put_text(host_plan_text(_ctx(inst)), f"{d}/heardos.env", 0o600)
    s.run(f"install -d -m 755 {q(KIT_DEST)}/generic && install -m 600 {q(d)}/heardos.env {q(KIT_DEST)}/generic/heardos.env", sudo=True, check=True)
    s.run(f"install -d -m 755 {q(root)}/stacks {q(root)}/data {q(root)}/dynamic {q(root)}/media && install -d -m 700 {q(root)}/secrets "
          f"&& touch {q(root)}/secrets/secrets.env && chmod 600 {q(root)}/secrets/secrets.env", sudo=True, check=True)
    if secrets:
        s.put_text("".join(f"{k}={v}\n" for k, v in secrets.items()), f"{d}/seed.env", 0o600)
        # merge: keys already in secrets.env win (a value the host generated must not be replaced behind its back)
        r = s.run(f"f={q(root)}/secrets/secrets.env; n=0; while IFS= read -r line; do k=${{line%%=*}}; "
                  f"grep -q \"^$k=\" \"$f\" || {{ printf '%s\\n' \"$line\" >> \"$f\"; n=$((n+1)); }}; done < {q(d)}/seed.env; echo added=$n",
                  sudo=True, check=True)
        log(f"secrets.env: {r.stdout.strip()} (of {len(secrets)} offered)")
    s.run(f"rm -rf {q(d)}", sudo=True)
    log(f"host plan file and {root} layout written")


def _ctx(inst: Installation):
    from .engine.step import Ctx
    return Ctx(inst.plan, inst.vault, inst.sessions, inst.logs.logger("adopt"), inst.state, inst.kit_root, inst.workdir)


def verify(inst: Installation, log) -> dict:
    from . import ops
    status = ops.app_status(inst)
    people = ops.people(inst)
    apps = inst.plan.get("apps") or []
    missing = [a for a in apps if a not in status]
    log(f"{len(status)} compose projects on the host; {len(apps)} catalogue apps in the plan"
        + (f"; not running as a project: {', '.join(missing)}" if missing else ""))
    log(f"{len(people)} people in Authentik")
    return {"projects": len(status), "missing": missing, "people": len(people)}


def adopt(spec: dict, vault, vault_values: dict[str, str], secrets: dict[str, str], apply: bool, log=print) -> Installation:
    plan = dict(spec["plan"])
    iid = spec["install_id"]
    ssh_targets = spec.get("ssh") or {}
    if not apply:
        log(f"[dry-run] would record install {iid} for {plan['DOMAIN']} (home server {plan['HOST_LAN_IP']}, {len(plan['apps'])} apps)")
        log(f"[dry-run] vault would get: {', '.join(sorted(vault_values)) or 'nothing'}")
        log(f"[dry-run] host secrets.env would be offered: {len(secrets)} keys")
        log(f"[dry-run] installer key would be added on: {', '.join(ssh_targets.values()) or 'nothing (already present?)'}")
        log(f"[dry-run] steps run for real: {', '.join(sorted(REAL_STEPS))}; pending stacks: {', '.join(pending_stacks(plan))}")
        return None  # type: ignore[return-value]
    if vault_values:
        vault.update(vault_values)
        log(f"vault: {len(vault_values)} value(s) stored")
    _, pub = ensure_key(DEFAULT_KEY)
    for target in ssh_targets.values():
        install_pubkey_via_ssh(target, pub, log)
    if plan.get("HOST_SSH_FINGERPRINT"):
        pin_host_key(plan["HOST_LAN_IP"], int(plan.get("HOST_SSH_PORT", 22)), plan["HOST_SSH_FINGERPRINT"], log)
    vps_addr = plan.get("NEBULA_LH_SSH") or plan.get("NEBULA_LH_IP")
    if vps_addr and plan.get("VPS_SSH_FINGERPRINT"):
        pin_host_key(vps_addr, 22, plan["VPS_SSH_FINGERPRINT"], log)
    inst = Installation(iid, vault, plan)
    if plan.get("NEBULA_LH_IP"):
        inst.state.set_handle("vps", {"provider": "byo", "id": "byo", "ip": plan["NEBULA_LH_IP"]})
    mark_adopted(inst, log)
    ok = inst.run(only={"host.push_kit"})
    if not ok:
        failed = [f"{sid}: {s.get('message', '')}" for sid, s in inst.state.data["steps"].items() if s.get("status") == "failed"]
        raise RuntimeError("first contact with the home server failed: " + "; ".join(failed))
    log("installer key works on the home server; kit uploaded")
    seed_host(inst, secrets, log)
    verify(inst, log)
    return inst


def main(a) -> None:
    from .__main__ import _vault
    spec = load_spec(a.spec)
    v = _vault(create_ok=False) if a.apply else None
    vault_values = read_kv(a.vault)
    secrets = read_kv(a.secrets)
    try:
        adopt(spec, v, vault_values, secrets, apply=a.apply)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"adopt failed: {e}")
    if a.apply:
        print(f"adopted {spec['install_id']}: heardos-cli tools {spec['install_id']}  |  heardos-serve --install {spec['install_id']}")
