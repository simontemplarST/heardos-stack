"""Move an adopted stack into the kit layout, one stack at a time:  heardos-cli migrate ID [STACK] [--apply|--rollback]

An adopted install (adopt.py) still runs its stacks the way they were set up before: other data paths, other compose
files, hand-written reverse-proxy routes. This moves one stack at a time under the installer's management, so the
family loses each app for a few minutes instead of everything for an afternoon:

  1. quiesce: the old stack's containers stop (compose finds them by project label)
  2. database dump when the kit uses another engine version than the old stack (dump -> restore, never a binary move)
  3. data moves: each old folder is renamed to its place under $HEARDOS_ROOT and a symlink is left behind, so an old
     stack that still mounts the same folder (shared media, Paperless' inbox in Nextcloud) keeps working after a restart
  4. the install step for the stack runs for real (50-app.sh: sign-in registration reused, .env from the seeded secrets,
     deployed through Portainer under the same stack name, which replaces the old containers)
  5. restore the dump, if any; run the stack's after-hooks (e.g. drop the hand-written proxy routes); verify
  6. the stack leaves plan["adopted_pending"]: every tool of the console and the harness works on it from now on

Everything comes from a migration spec (JSON), kept next to the adopt spec:
  {"stacks": {"mealie": {"moves": [["/old/path", "data/mealie"]],          # destination relative to HEARDOS_ROOT
                         "db": {"dump": {"container": "nextcloud-db", "user": "tipi", "db": "nextcloud"},
                                "restore": {"service": "db", "user": "nextcloud", "db": "nextcloud"}},   # optional
                         "step": "app.mealie",                               # default app.<stack>; core: host.edge / host.authentik
                         "before": [...], "pre_step": [...], "post_deploy": [...], "after": [...],   # root shell on the home server
                         "hold": "why this one cannot move yet",                      # optional: preflight refuses
                         "verify": ["mealie"]}}}                             # public host names to check (default: catalogue)
Without --apply the plan and the remote preconditions are printed and nothing changes. --rollback undoes the moves of a
migrated stack (symlinks removed, folders renamed back) and redeploys the old stack from spec "old": {"compose": file,
"env": file} on the laptop. State: handle migrated.<stack> in the install's state file.
"""
from __future__ import annotations

import json
import shlex
import sys
import time
from pathlib import Path
from typing import Optional

from .engine.state import config_dir
from .session import Installation

Q = shlex.quote


def spec_path(install_id: str, explicit: Optional[str] = None) -> Path:
    return Path(explicit) if explicit else config_dir() / "migrate" / f"{install_id}.json"


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text())
    if "stacks" not in spec:
        raise ValueError("spec needs 'stacks'")
    return spec


class Migrator:
    def __init__(self, inst: Installation, spec: dict, log=print):
        self.inst, self.spec, self.log = inst, spec, log
        self.root = inst.plan.get("HEARDOS_ROOT", "/opt/heardos")

    # ------------------------------------------------------------- helpers
    def _host(self):
        return self.inst.sessions.host()

    def _sh(self, cmd: str, timeout: int = 600, check: bool = True):
        r = self._host().run(cmd, sudo=True, timeout=timeout)
        if check and not r.ok:
            raise RuntimeError(f"on the home server: {cmd[:80]}... -> {(r.stderr or r.stdout).strip()[-300:]}")
        return r

    def entry(self, stack: str) -> dict:
        e = self.spec["stacks"].get(stack)
        if e is None:
            raise ValueError(f"no migration spec for {stack}; stacks in the spec: {', '.join(sorted(self.spec['stacks']))}")
        e = dict(e)
        e.setdefault("step", f"app.{stack}")
        e.setdefault("moves", [])
        e.setdefault("before", [])
        e.setdefault("pre_step", [])
        e.setdefault("post_deploy", [])
        e.setdefault("after", [])
        return e

    def pending(self) -> list[str]:
        return list(self.inst.plan.get("adopted_pending") or [])

    def dst(self, rel: str) -> str:
        return rel if rel.startswith("/") else f"{self.root}/{rel}"

    # ------------------------------------------------------------- preconditions
    def preflight(self, stack: str, release_hold: bool = False) -> list[str]:
        """Problems that stop a migration; empty when it can go ahead."""
        e = self.entry(stack)
        probs = []
        if stack not in self.pending():
            probs.append(f"{stack} is not pending (already migrated, or not part of this install)")
        if e.get("hold") and not release_hold:
            probs.append(f"on hold: {e['hold']}")
        checks = []
        resuming = bool(self.inst.state.handle(f"migrated.{stack}"))      # an earlier try moved folders, then its step failed
        for src, rel in e["moves"]:
            checks.append(f"test -e {Q(src)} || echo MISSING {Q(src)}; test -L {Q(src)} && echo LINK {Q(src)}; "
                          f"test -e {Q(self.dst(rel))} && echo EXISTS {Q(self.dst(rel))}")
        if e.get("db"):
            d = e["db"]["dump"]
            checks.append(f"docker inspect {Q(d['container'])} >/dev/null 2>&1 || echo NODB {Q(d['container'])}")
        checks.append(f"docker ps -a --filter label=com.docker.compose.project={Q(stack)} --format '{{{{.Names}}}}' | grep -q . || echo NOPROJECT")
        checks.append(f"test -s {Q(self.root)}/secrets/secrets.env || echo NOSECRETS")
        r = self._sh("; ".join(checks), check=False)
        for line in r.stdout.splitlines():
            w = line.split(" ", 1)
            if w[0] == "MISSING":
                probs.append(f"old folder missing: {w[1]}")
            elif w[0] == "LINK":
                if not resuming:
                    probs.append(f"old path is already a symlink (migrated before?): {w[1]}")
            elif w[0] == "EXISTS":
                if not resuming:
                    probs.append(f"destination already exists: {w[1]}")
            elif w[0] == "NODB":
                probs.append(f"database container not found: {w[1]}")
            elif w[0] == "NOPROJECT":
                self.log(f"note: no containers of compose project {stack} on the host (stopped through Portainer?); nothing to quiesce")
            elif w[0] == "NOSECRETS":
                probs.append("secrets.env on the host is empty: run adopt with --secrets first, or the kit will generate new passwords")
        return probs

    # ------------------------------------------------------------- the move
    def plan_text(self, stack: str) -> str:
        e = self.entry(stack)
        lines = [f"migrate {stack} (step {e['step']})"]
        lines += [f"  before: {c}" for c in e["before"]]
        lines.append(f"  stop containers of project {stack}")
        if e.get("db"):
            d = e["db"]
            lines.append(f"  dump {d['dump']['db']} from {d['dump']['container']} (user {d['dump']['user']}) -> restore into service "
                         f"{d['restore']['service']} as {d['restore']['user']}")
        for src, rel in e["moves"]:
            lines.append(f"  move {src} -> {self.dst(rel)} (symlink left at the old path)")
        lines += [f"  pre-step: {c}" for c in e["pre_step"]]
        lines.append(f"  run install step {e['step']} (deploys through Portainer under the same name)")
        lines += [f"  post-deploy: {c}" for c in e["post_deploy"]]
        lines += [f"  after: {c}" for c in e["after"]]
        lines.append(f"  verify https://<host>.{self.inst.plan['DOMAIN']} for: {', '.join(self._verify_hosts(stack, e)) or 'nothing'}")
        return "\n".join(lines)

    def _verify_hosts(self, stack: str, e: dict) -> list[str]:
        if "verify" in e:
            return list(e["verify"])
        from .kit import catalog
        app = catalog.apps().get(stack)
        return list(app.hosts) if app else []

    def migrate(self, stack: str, release_hold: bool = False) -> dict:
        e = self.entry(stack)
        probs = self.preflight(stack, release_hold)
        if probs:
            raise RuntimeError("cannot migrate: " + "; ".join(probs))
        rec: dict = self.inst.state.handle(f"migrated.{stack}") or {"stack": stack, "started": time.time(), "moves": [], "dump": None}
        done_moves = {tuple(m) for m in rec.get("moves", [])}
        if done_moves:
            self.log(f"resuming: {len(done_moves)} folder(s) already moved by an earlier try")
        self.inst.state.set_handle(f"migrated.{stack}", rec)
        for c in e["before"]:
            self.log(f"before: {c}")
            self._sh(c, timeout=1800)
        self.log(f"stopping {stack}")
        self._sh(f"docker compose -p {Q(stack)} stop", timeout=600)
        if e.get("db") and not rec.get("dump"):
            d = e["db"]["dump"]
            dump = f"{self.root}/migrate/{stack}.dump"
            self._sh(f"install -d -m 700 {Q(self.root)}/migrate && docker start {Q(d['container'])} >/dev/null && "
                     f"for i in $(seq 60); do docker exec {Q(d['container'])} pg_isready -U {Q(d['user'])} -d {Q(d['db'])} >/dev/null 2>&1 && break; sleep 2; done && "
                     f"docker exec {Q(d['container'])} pg_dump -U {Q(d['user'])} -Fc {Q(d['db'])} > {Q(dump)} && docker stop {Q(d['container'])} >/dev/null && "
                     f"ls -l {Q(dump)}", timeout=3600)
            rec["dump"] = dump
            self.log(f"database dumped to {dump}")
        for src, rel in e["moves"]:
            dst = self.dst(rel)
            if (src, dst) in done_moves:
                continue
            self._sh(f"install -d -m 755 {Q(str(Path(dst).parent))} && mv {Q(src)} {Q(dst)} && ln -s {Q(dst)} {Q(src)}")
            rec["moves"].append([src, dst])
            self.inst.state.set_handle(f"migrated.{stack}", rec)
            self.log(f"moved {src} -> {dst}")
        for c in e["pre_step"]:              # after the moves, before the install step (a config file that must name the kit's hosts)
            self.log(f"pre-step: {c}")
            self._sh(c, timeout=1800)
        self.log(f"running {e['step']}")
        self.inst.state.reset_step(e["step"])
        ok = self.inst.run(only={e["step"]})
        if not ok:
            msg = self.inst.state.step(e["step"]).get("message", "")
            raise RuntimeError(f"{e['step']} failed: {msg}. The old folders are at their new places (symlinked from the old ones); "
                               f"fix and re-run, or --rollback")
        for c in e["post_deploy"]:          # fix-ups the restore needs (e.g. an app config that names the new database host)
            self.log(f"post-deploy: {c}")
            self._sh(c, timeout=1800)
        if e.get("db"):
            self._restore(stack, e["db"]["restore"], rec["dump"])
        for c in e["after"]:
            self.log(f"after: {c}")
            self._sh(c, timeout=1800)
        pend = [s for s in self.pending() if s != stack]
        self.inst.plan["adopted_pending"] = pend
        rec["finished"] = time.time()
        self.inst.state.set_handle(f"migrated.{stack}", rec)
        self.inst.state.save()
        self.log(f"{stack} is now managed by the installer; {len(pend)} stack(s) still pending")
        return rec

    def _restore(self, stack: str, r: dict, dump: str) -> None:
        find = (f"docker ps --filter label=com.docker.compose.project={Q(stack)} --filter label=com.docker.compose.service={Q(r['service'])} "
                f"--format '{{{{.Names}}}}' | head -1")
        self._sh(f"c=$({find}); [ -n \"$c\" ] || {{ echo no db container; exit 1; }}; "
                 f"for i in $(seq 90); do docker exec \"$c\" pg_isready -U {Q(r['user'])} -d {Q(r['db'])} >/dev/null 2>&1 && break; sleep 2; done; "
                 f"docker exec -i \"$c\" pg_restore -U {Q(r['user'])} -d {Q(r['db'])} --no-owner --role={Q(r['user'])} --clean --if-exists < {Q(dump)} "
                 f"2> {Q(dump)}.restore.log || true; tail -3 {Q(dump)}.restore.log; "
                 f"docker compose -p {Q(stack)} restart >/dev/null 2>&1 || true", timeout=3600)
        self.log("database restored; stack restarted")

    def verify(self, stack: str) -> list[str]:
        """HTTPS answers from every public host of the stack (anything under 500 counts: most sit behind sign-in)."""
        import ssl
        import urllib.request
        bad = []
        for h in self._verify_hosts(stack, self.entry(stack)):
            url = f"https://{h}.{self.inst.plan['DOMAIN']}/"
            code, err = 0, ""
            for attempt in range(8):            # the container is seconds old: give the router and the app half a minute
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "heardos-installer"})
                    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as resp:
                        code = resp.status
                except urllib.error.HTTPError as ex:
                    code = ex.code
                except Exception as ex:  # noqa: BLE001
                    code, err = 0, type(ex).__name__
                if code and code < 400:
                    break
                if code in (401, 403):          # behind sign-in: that is an answer
                    break
                time.sleep(5)
            if not code:
                bad.append(f"{h}: {err}")
            elif code >= 500 or code == 404:
                bad.append(f"{h}: HTTP {code}")
            self.log(f"  {url} -> {code or err}")
        return bad

    # ------------------------------------------------------------- rollback
    def rollback(self, stack: str) -> None:
        rec = self.inst.state.handle(f"migrated.{stack}")
        if not rec:
            raise RuntimeError(f"{stack} has no migration record")
        e = self.entry(stack)
        self.log(f"stopping the new {stack}")
        self._sh(f"docker compose -p {Q(stack)} stop", timeout=600, check=False)
        for src, dst in reversed(rec.get("moves", [])):
            self._sh(f"[ -L {Q(src)} ] && rm {Q(src)}; [ -e {Q(src)} ] && {{ echo {Q(src)} exists; exit 1; }}; mv {Q(dst)} {Q(src)}")
            self.log(f"moved back {dst} -> {src}")
        old = e.get("old") or {}
        if old.get("compose"):
            from .steps.common import KIT_DEST
            s = self._host()
            d = s.tempdir()
            s.put_text(Path(old["compose"]).read_text(), f"{d}/compose.yml", 0o600)
            env = Path(old["env"]).read_text() if old.get("env") and Path(old["env"]).exists() else ""
            s.put_text(env, f"{d}/stack.env", 0o600)
            self._sh(f"set -a; . {KIT_DEST}/generic/heardos.env; set +a; python3 {KIT_DEST}/generic/lib/portainer-stack.py {Q(stack)} {Q(d)}/compose.yml {Q(d)}/stack.env; rm -rf {Q(d)}",
                     timeout=1800)
            self.log("old stack redeployed through Portainer")
        else:
            self.log("no old compose in the spec: start the old stack from Portainer by hand")
        self.inst.state.set_status(e["step"], __import__("heardos_installer.engine.step", fromlist=["StepStatus"]).StepStatus.done, "adopted: rolled back to the old layout")
        pend = self.pending()
        if stack not in pend:
            pend.append(stack)
        self.inst.plan["adopted_pending"] = pend
        self.inst.state.set_handle(f"migrated.{stack}", None)
        self.inst.state.save()
        self.log(f"{stack} is back in its old layout and pending again")


def main(a) -> None:
    from .__main__ import _prompt, _vault
    inst = Installation(a.id, _vault(), prompt=_prompt)
    spec = load_spec(spec_path(a.id, a.spec))
    m = Migrator(inst, spec)
    if not a.stack:
        pend = m.pending()
        for s in ["traefik", "authentik"] + list(inst.plan.get("apps") or []):
            state = "pending" if s in pend else "migrated"
            print(f"  {state:9} {s:16} {'(in spec)' if s in spec['stacks'] else '(no spec entry)'}")
        return
    if getattr(a, "rollback", False):
        m.rollback(a.stack)
        return
    print(m.plan_text(a.stack))
    probs = m.preflight(a.stack, getattr(a, "release_hold", False))
    if probs:
        print("blocked:\n  " + "\n  ".join(probs))
        sys.exit(1)
    print("preconditions ok")
    if not a.apply:
        print("(dry run; add --apply)")
        return
    m.migrate(a.stack, getattr(a, "release_hold", False))
    bad = m.verify(a.stack)
    if bad:
        sys.exit("migrated, but the web check found: " + "; ".join(bad))
    print(f"{a.stack}: done")
