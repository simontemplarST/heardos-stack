"""Day-two operations on a finished install. Every function is blocking (run them through ui.bus.Tasks or the CLI) and
returns plain data. Remote work goes through the same SSH sessions and kit scripts as the install."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import shlex
import socket
import ssl
import string
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from .engine.step import Ctx, StepResult
from .kit import catalog
from .steps.common import apply_defaults, overlay_ip, run_kit

HOST_STEPS_FOR_NEW_APP = ("dns.publish", "vps.caddy", "verify")


@dataclass
class Check:
    area: str
    name: str
    ok: Optional[bool]          # None = information only
    detail: str


def ctx(inst, name: str) -> Ctx:
    return Ctx(inst.plan, inst.vault, inst.sessions, inst.logs.logger(f"ops.{name}"), inst.state, inst.kit_root, inst.workdir,
               prompt=inst.prompt)


def _kit(inst, name: str, script: str, args: Iterable[str] = (), env: Optional[dict] = None, timeout: int = 900, cwd: str = "generic") -> StepResult:
    return run_kit(ctx(inst, name), script, list(args), env=env, timeout=timeout, cwd_dir=cwd)


def _json_line(text: str):
    for line in text.splitlines():
        if line.startswith("HEARDOS_JSON "):
            return json.loads(line[len("HEARDOS_JSON "):])
    raise RuntimeError("no data returned")


# ------------------------------------------------------------------ health
def health(inst) -> list[Check]:
    out: list[Check] = []
    p = inst.plan
    try:
        v = inst.sessions.vps()
        r = v.run("sshd -T 2>/dev/null | grep -E '^passwordauthentication ' ; ufw status | head -1; "
                  "systemctl is-active caddy nebula fail2ban unattended-upgrades | tr '\\n' ' '; echo; "
                  "apt list --upgradable 2>/dev/null | grep -c upgradable; test -f /var/run/reboot-required && echo reboot || echo noreboot; "
                  "df -P / | awk 'NR==2{print $5}'", sudo=True, timeout=60)
        L = r.stdout.splitlines() + [""] * 6
        out += [Check("Public server", "Password logins off", "no" in L[0], L[0].strip() or "unknown"),
                Check("Public server", "Firewall on", "active" in L[1], L[1].strip()),
                Check("Public server", "Web, tunnel, ban and update services running", L[2].split() == ["active"] * 4, L[2].strip()),
                Check("Public server", "Security updates installed", L[3].strip() == "0", f"{L[3].strip()} pending"),
                Check("Public server", "No restart needed", L[4].strip() == "noreboot", "restart pending" if L[4].strip() == "reboot" else "ok"),
                Check("Public server", "Disk space", int(L[5].strip("%") or 0) < 85, f"{L[5].strip()} used")]
    except Exception as e:  # noqa: BLE001
        out.append(Check("Public server", "Reachable", False, str(e)[:160]))
    try:
        h = inst.sessions.host()
        r = h.run("docker ps -a --format '{{.Names}}\\t{{.Status}}'; echo ---; df -P " + shlex.quote(p.get("HEARDOS_ROOT", "/opt/heardos")) +
                  " | awk 'NR==2{print $5}'; ping -c 2 -W 3 " + overlay_ip(p, "NEBULA_LH_OVERLAY") + " >/dev/null && echo tunnel-ok || echo tunnel-down; "
                  "systemctl show heardos-backup.service -p Result -p ExecMainExitTimestamp --value 2>/dev/null | tr '\\n' ' '; echo; "
                  "test -f /var/run/reboot-required && echo reboot || echo noreboot", sudo=True, timeout=90)
        rows, rest = r.stdout.split("---", 1) if "---" in r.stdout else (r.stdout, "")
        bad = [l.split("\t")[0] for l in rows.strip().splitlines() if re.search(r"unhealthy|Restarting|Exited \((?!0\))", l)]
        n = len(rows.strip().splitlines())
        R = rest.strip().splitlines() + [""] * 4
        out += [Check("Home server", "Apps running", not bad, f"{n - len(bad)} of {n} containers fine" + (f"; trouble: {', '.join(bad[:5])}" if bad else "")),
                Check("Home server", "Disk space", int((R[0].strip("%") or "0")) < 85, f"{R[0]} used"),
                Check("Home server", "Private tunnel to the public server", R[1] == "tunnel-ok", R[1]),
                Check("Home server", "Last backup", (None if not R[2].strip() else R[2].split()[0] == "success"),
                      R[2].strip() or "backups not set up"),
                Check("Home server", "No restart needed", R[3].strip() == "noreboot", "restart pending" if R[3].strip() == "reboot" else "ok")]
    except Exception as e:  # noqa: BLE001
        out.append(Check("Home server", "Reachable", False, str(e)[:160]))
    try:
        ld = host_load(inst)
        if ld["busy"] is not None:
            out.append(Check("Home server", "Not overloaded", not ld["busy"],
                             ld["summary"] + ("; everything on it will be slow until that drops" if ld["busy"] else "")))
    except Exception:  # noqa: BLE001 — reachability is already reported above
        pass
    for hname in p.get("PUBLIC_HOSTS", []):
        out.append(_https_check(f"{hname}.{p['DOMAIN']}"))
    return out


# A busy disk makes every app on the server look broken (2026-10-28: OpenCloud's first scan kept the disk 94% busy, and
# sign-ins timed out in an unrelated service). These thresholds mark "the server itself is the bottleneck".
BUSY_IO_PCT, BUSY_CPU_PCT, BUSY_LOAD_PER_CORE = 40.0, 60.0, 2.0
LOAD_CMD = "cat /proc/loadavg; nproc; grep -h '^some' /proc/pressure/io /proc/pressure/cpu 2>/dev/null"


def parse_load(text: str) -> dict:
    """/proc/loadavg + nproc + PSI 'some' lines -> {load1, cores, io_pct, cpu_pct, busy, summary}. PSI avg60 = % of the last
    minute in which some task was stalled waiting for the disk (io) or a CPU (cpu); missing PSI (old kernels) reads as 0."""
    L = text.splitlines()
    try:
        load1, cores = float(L[0].split()[0]), max(1, int(L[1].strip()))
    except (IndexError, ValueError):
        return {"busy": None, "summary": "server load unknown"}
    psi = [float(m.group(1)) for l in L[2:4] for m in [re.search(r"avg60=([0-9.]+)", l)] if m] + [0.0, 0.0]
    io, cpu = psi[0], psi[1]
    busy = io >= BUSY_IO_PCT or cpu >= BUSY_CPU_PCT or load1 >= BUSY_LOAD_PER_CORE * cores
    return {"load1": load1, "cores": cores, "io_pct": io, "cpu_pct": cpu, "busy": busy,
            "summary": f"load {load1:.1f} on {cores} cores; disk-waits {io:.0f}% and cpu-waits {cpu:.0f}% of the last minute"}


def host_load(inst) -> dict:
    return parse_load(inst.sessions.host().run(LOAD_CMD, timeout=30).stdout)


def _https_check(host: str) -> Check:
    try:
        c = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as s, c.wrap_socket(s, server_hostname=host) as t:
            cert = t.getpeercert()
            days = int((ssl.cert_time_to_seconds(cert["notAfter"]) - time.time()) // 86400)
            t.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: heardos\r\nConnection: close\r\n\r\n".encode())
            code = int(t.recv(64).split()[1])
        return Check("Web addresses", host, code < 500 and days > 10, f"HTTP {code}, certificate valid {days} more days")
    except Exception as e:  # noqa: BLE001
        return Check("Web addresses", host, False, type(e).__name__ + (f": {e}"[:100] if str(e) else ""))


# ------------------------------------------------------------------ port scan
EXPECTED_OPEN = {22, 80, 443}     # ssh is key-only; Nebula is UDP and not visible to a TCP scan


def portscan(ip: str, ports: Iterable[int] = range(1, 65536), conc: int = 500, timeout: float = 2.0) -> dict:
    async def run():
        sem = asyncio.Semaphore(conc)
        found: list[int] = []

        async def probe(port):
            async with sem:
                try:
                    _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
                    found.append(port); w.close()
                except Exception:  # noqa: BLE001
                    pass
        await asyncio.gather(*(probe(p) for p in ports))
        return sorted(found)
    t0 = time.time()
    found = asyncio.run(run())
    return {"open": found, "unexpected": [p for p in found if p not in EXPECTED_OPEN], "seconds": round(time.time() - t0)}


# ------------------------------------------------------------------ people
def people(inst) -> list[dict]:
    r = _kit(inst, "people", "42-ak-app.py", ["user", "list"], timeout=120)
    if not r.ok:
        raise RuntimeError(r.message)
    return _json_line(r.stdout)


def _gen_password() -> str:
    a = string.ascii_letters + string.digits
    return "-".join("".join(secrets.choice(a) for _ in range(5)) for _ in range(4))


def add_person(inst, username: str, name: str, email: str, apps: list[str], groups: list[str], send_welcome: bool) -> str:
    """Create the account, give app access and rule groups; returns the first password (shown once by the UI)."""
    pw = _gen_password()
    inst.vault.set(f"PERSON_{username.upper()}_FIRST_PASSWORD", pw)   # in the vault => redacted from every log
    # the password goes to 42-ak-app.py in a 0600 file it reads and deletes (HEARDOS_PASSWORD_FILE), never on argv
    h = inst.sessions.host()
    d = h.tempdir()
    h.put_text(pw, f"{d}/password", 0o600)
    try:
        r = _kit(inst, "people", "42-ak-app.py", ["user", "add", username, "--email", email, "--name", name],
                 env={"HEARDOS_PASSWORD_FILE": f"{d}/password"})
    finally:
        h.run(f"rm -rf {shlex.quote(d)}", sudo=True, timeout=30)
    if not r.ok:
        raise RuntimeError(r.message)
    slugs = [catalog.apps()[a].slug for a in apps if catalog.apps()[a].slug]
    if slugs:
        r = _kit(inst, "people", "42-ak-app.py", ["user", "access", username, ",".join(slugs)])
        if not r.ok:
            raise RuntimeError(r.message)
    if groups:
        _kit(inst, "people", "42-ak-app.py", ["user", "join", username, ",".join(groups)])
    if "calibre-web" in apps:
        h = inst.sessions.host()
        h.run(f"HEARDOS_ROOT={shlex.quote(inst.plan.get('HEARDOS_ROOT', '/opt/heardos'))} python3 /opt/heardos-kit/generic/lib/calibre-web-setup.py "
              f"add {shlex.quote(username)} {shlex.quote(email)}", sudo=True, timeout=60)
    if send_welcome and inst.plan.get("mail_mode") not in (None, "none"):
        _kit(inst, "people", "42-ak-app.py", ["user", "welcome", username])
    return pw


def set_access(inst, username: str, add: list[str], remove: list[str]) -> None:
    cat = catalog.apps()
    for action, items in (("access", add), ("revoke", remove)):
        slugs = [cat[a].slug for a in items if cat[a].slug]
        if slugs:
            r = _kit(inst, "people", "42-ak-app.py", ["user", action, username, ",".join(slugs)])
            if not r.ok:
                raise RuntimeError(r.message)


def set_groups(inst, username: str, join: list[str], leave: list[str]) -> None:
    for action, items in (("join", join), ("leave", leave)):
        if items:
            r = _kit(inst, "people", "42-ak-app.py", ["user", action, username, ",".join(items)])
            if not r.ok:
                raise RuntimeError(r.message)


def person_action(inst, username: str, action: str) -> str:
    assert action in ("welcome", "suspend", "resume")
    r = _kit(inst, "people", "42-ak-app.py", ["user", action, username])
    if not r.ok:
        raise RuntimeError(r.message)
    return {"welcome": "set-password email sent", "suspend": "sign-in blocked", "resume": "sign-in allowed again"}[action]


# ------------------------------------------------------------------ apps
def app_status(inst) -> dict[str, str]:
    """compose project -> worst container state ('running', 'unhealthy', 'exited', 'restarting')."""
    r = inst.sessions.host().run("docker ps -a --format '{{.Label \"com.docker.compose.project\"}}\\t{{.State}}\\t{{.Status}}'", sudo=True, timeout=60)
    rank = {"running": 0, "created": 1, "exited": 2, "restarting": 3, "unhealthy": 4}
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        proj, state, status = (line.split("\t") + ["", "", ""])[:3]
        if not proj:
            continue
        s = "unhealthy" if "unhealthy" in status else state
        if rank.get(s, 1) >= rank.get(out.get(proj, "running"), 0):
            out[proj] = s
    return out


def app_compose(inst, stack: str, verb: str) -> str:
    assert verb in ("update", "restart", "stop", "start")
    d = shlex.quote(f"{inst.plan.get('HEARDOS_ROOT', '/opt/heardos')}/stacks/{stack}")
    cmd = {"update": "docker compose -p {s} pull -q && docker compose -p {s} up -d --remove-orphans",
           "restart": "docker compose -p {s} restart", "stop": "docker compose -p {s} stop", "start": "docker compose -p {s} up -d"}[verb]
    r = inst.sessions.host().run(f"cd {d} && " + cmd.format(s=shlex.quote(stack)), sudo=True, timeout=1800,
                                 on_line=inst.logs.logger(f"ops.app.{stack}").info)
    if not r.ok:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1:][0][:200] if (r.stderr or r.stdout).strip() else f"{verb} failed")
    return f"{catalog.apps()[stack].name}: {verb} done"


def add_apps(inst, stacks: list[str]) -> set[str]:
    """Add apps to the plan and return the step ids to run (DNS and HTTPS for new addresses, the apps, their setup)."""
    plan = inst.plan
    plan["apps"] = catalog.closure(list(dict.fromkeys((plan.get("apps") or []) + stacks)))
    plan.pop("PUBLIC_HOSTS", None)
    apply_defaults(plan)
    inst.state.save()
    for sid in HOST_STEPS_FOR_NEW_APP:
        inst.state.reset_step(sid)
    ids = set(HOST_STEPS_FOR_NEW_APP)
    for s in catalog.closure(stacks):
        ids.add(f"app.{s}")
        ids.update(f"post.{t}" for t in catalog.apps()[s].post_install)
    return ids


# ------------------------------------------------------------------ backups
def backup(inst, action: str, repo: str = "") -> str:
    assert action in ("install", "run", "list", "check")
    if repo:
        inst.vault.set("RESTIC_REPOSITORY", repo)
    if not inst.vault.get("RESTIC_REPOSITORY"):
        raise RuntimeError("choose where backups go first")
    r = _kit(inst, f"backup.{action}", "70-backup.sh", [action], timeout=6 * 3600)
    if not r.ok:
        raise RuntimeError(r.message)
    if action == "install":
        inst.state.set_plan(backups_installed=True)
    pw = (r.outputs or {}).pop("RESTIC_PASSWORD", None)
    if pw:   # the only copy must not live solely inside the backups it encrypts: keep it in the laptop's vault too
        inst.vault.set("RESTIC_PASSWORD", pw)
    from .remote.ssh import mask_output_line
    return "\n".join(mask_output_line(l) for l in r.stdout[-4000:].splitlines())


# ------------------------------------------------------------------ settings
def reapply_brand(inst, **changes) -> set[str]:
    if changes:
        inst.state.set_plan(**changes)
        inst.plan.pop("BRAND_COLOR", None)
        apply_defaults(inst.plan); inst.state.save()
    inst.state.reset_step("brand.apply")
    return {"brand.apply"}
