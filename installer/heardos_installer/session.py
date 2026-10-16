"""An Installation bundles everything one install needs: state, vault, logs, SSH sessions and a runner over its DAG.

Both the CLI and the ImGui app drive installs through this class, so behaviour is identical in both.
"""
from __future__ import annotations

import re
import secrets
import threading
from pathlib import Path
from typing import Callable, Optional

from .engine import InstallState, Runner, StepStatus
from .engine.log import LogHub
from .engine.state import config_dir
from .engine.step import Ctx, Step
from .keys import DEFAULT_KEY
from .kit.locate import kit_root
from .remote.ssh import SessionPool
from .steps import apply_defaults, build_steps

Prompt = Callable[[str, str, Callable[[], bool]], bool]


def new_install_id(domain: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-") or "install"
    return f"{base}-{secrets.token_hex(2)}"


class Installation:
    def __init__(self, install_id: str, vault, plan: Optional[dict] = None, prompt: Optional[Prompt] = None):
        self.install_id = install_id
        self.vault = vault
        self.state = InstallState(install_id, plan)
        apply_defaults(self.state.plan)
        self.state.save()
        self.logs = LogHub(install_id, vault.values)
        self.kit_root = kit_root()
        self.workdir = config_dir() / "installs" / install_id
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.sessions = SessionPool(self.state.plan, vault, DEFAULT_KEY)
        self.prompt = prompt
        self.runner: Optional[Runner] = None
        self._thread: Optional[threading.Thread] = None
        self.listeners: list[Callable[[str, StepStatus, str], None]] = []

    @property
    def plan(self) -> dict:
        return self.state.plan

    def steps(self) -> list[Step]:
        return build_steps(self.plan)

    def _ctx(self, dry_run: bool):
        def make(step: Step) -> Ctx:
            return Ctx(self.plan, self.vault, self.sessions, self.logs.logger(step.id), self.state, self.kit_root,
                       self.workdir, dry_run=dry_run, prompt=self.prompt)
        return make

    def run(self, only: Optional[set[str]] = None, dry_run: bool = False) -> bool:
        apply_defaults(self.plan)
        self.state.save()
        self.runner = Runner(self.steps(), self._ctx(dry_run), self.state, self.vault, listeners=list(self.listeners))
        try:
            return self.runner.run(only)
        finally:
            self.sessions.close()

    def run_async(self, only: Optional[set[str]] = None, dry_run: bool = False, done: Optional[Callable[[bool], None]] = None) -> None:
        def go():
            ok = False
            try:
                ok = self.run(only, dry_run)
            finally:
                if done:
                    done(ok)
        self._thread = threading.Thread(target=go, name=f"install-{self.install_id}", daemon=True)
        self._thread.start()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def cancel(self) -> None:
        if self.runner:
            self.runner.cancel()

    def retry(self, step_id: str) -> None:
        """Forget a step's result (and everything after it that was skipped) so the next run re-executes it."""
        self.state.reset_step(step_id)
        for sid, st in list(self.state.data["steps"].items()):
            if st.get("status") in ("skipped", "failed"):
                self.state.reset_step(sid)

    def destroy_vps(self) -> str:
        from .providers import VpsHandle
        from .steps.common import provider_for
        from .engine.step import Ctx as _Ctx
        h = self.state.handle("vps")
        if not h:
            return "no VPS recorded for this install"
        ctx = _Ctx(self.plan, self.vault, None, self.logs.logger("destroy"), self.state, self.kit_root, self.workdir)
        provider_for(ctx).destroy(VpsHandle.from_json(h))
        self.state.set_handle("vps", None)
        for sid in ("vps.create", "vps.harden", "vps.nebula", "vps.caddy", "host.nebula", "dns.publish", "verify"):
            self.state.reset_step(sid)
        return f"destroyed {h['provider']} server {h.get('ip') or h['id']}"

    def log_path(self, step_id: str) -> Path:
        return self.logs.path(step_id)
