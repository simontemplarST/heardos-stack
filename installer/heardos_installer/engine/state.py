"""Persisted install state: ~/.config/heardos/state/<install-id>.json. Never holds secrets."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .step import StepStatus


def config_dir() -> Path:
    base = os.environ.get("HEARDOS_HOME") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    d = Path(base) / "heardos" if not os.environ.get("HEARDOS_HOME") else Path(base)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


class InstallState:
    """One JSON document per install id; every mutation is written through immediately."""

    def __init__(self, install_id: str, plan: dict[str, Any] | None = None):
        self.install_id = install_id
        self.path = config_dir() / "state" / f"{install_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if plan:
                self.data["plan"].update(plan)
        else:
            self.data = {"install_id": install_id, "created": time.time(), "plan": dict(plan or {}), "steps": {}, "handles": {}}
        self.save()

    # --- plan (non-secret config) ---
    @property
    def plan(self) -> dict[str, Any]:
        return self.data["plan"]

    def set_plan(self, **kv: Any) -> None:
        with self._lock:
            self.plan.update(kv)
            self.save()

    # --- step bookkeeping ---
    def step(self, sid: str) -> dict[str, Any]:
        return self.data["steps"].setdefault(sid, {"status": StepStatus.pending.value, "attempts": 0, "outputs": {}})

    def set_status(self, sid: str, status: StepStatus, message: str = "") -> None:
        with self._lock:
            s = self.step(sid)
            s["status"] = status.value
            s["message"] = message
            now = time.time()
            if status == StepStatus.running:
                s["started"] = now
                s["attempts"] = s.get("attempts", 0) + 1
            if status in (StepStatus.done, StepStatus.failed, StepStatus.skipped):
                s["ended"] = now
            self.save()

    def status(self, sid: str) -> StepStatus:
        return StepStatus(self.step(sid)["status"])

    def set_outputs(self, sid: str, outputs: dict[str, Any]) -> None:
        with self._lock:
            self.step(sid)["outputs"].update(outputs)
            self.plan.update(outputs)
            self.save()

    # --- provider handles etc. (ids, not secrets) ---
    def set_handle(self, name: str, value: Any) -> None:
        with self._lock:
            self.data["handles"][name] = value
            self.save()

    def handle(self, name: str, default: Any = None) -> Any:
        return self.data["handles"].get(name, default)

    def reset_step(self, sid: str) -> None:
        with self._lock:
            self.data["steps"].pop(sid, None)
            self.save()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @staticmethod
    def list_installs() -> list[str]:
        d = config_dir() / "state"
        return sorted(p.stem for p in d.glob("*.json")) if d.exists() else []
