"""Executes the DAG: layer by layer, steps in a layer in parallel, serial groups honoured, state written through.

Resume comes for free: a step recorded as done whose check() still passes is skipped. A failed step stops its
dependants (unless optional) but unrelated branches keep going. The runner never touches the UI; it reports through
callbacks that the UI/CLI subscribe to.
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Iterable, Optional

from ..vault.redact import redact_text
from .dag import topo_layers, validate_dag
from .step import Ctx, Step, StepResult, StepStatus

Listener = Callable[[str, StepStatus, str], None]


class Runner:
    def __init__(self, steps: Iterable[Step], make_ctx: Callable[[Step], Ctx], state, vault, listeners: Optional[list[Listener]] = None,
                 max_workers: int = 4):
        self.steps = validate_dag(steps)
        self.make_ctx = make_ctx
        self.state = state
        self.vault = vault
        self.listeners = listeners or []
        self.max_workers = max_workers
        self._group_locks: dict[str, threading.Lock] = {}
        self._cancel = threading.Event()
        self.failed: set[str] = set()

    def cancel(self) -> None:
        self._cancel.set()

    def _notify(self, sid: str, status: StepStatus, msg: str = "", extra_secrets: Iterable[str] = ()) -> None:
        # the message lands in the state file (which never holds secrets) and the UI: mask vault values, the failing
        # step's own not-yet-stored secrets, and HEARDOS_OUTPUT values
        try:
            vals = list(self.vault.values()) if self.vault is not None else []
        except Exception:  # a locked vault must not stop status updates
            vals = []
        msg = redact_text(msg or "", [*vals, *extra_secrets])
        self.state.set_status(sid, status, msg)
        for fn in self.listeners:
            try:
                fn(sid, status, msg)
            except Exception:
                pass

    def _blocked(self, step: Step) -> bool:
        for d in step.deps:
            st = self.state.status(d)
            dep = self.steps[d]
            if st == StepStatus.failed and not dep.optional:
                return True
            if st not in (StepStatus.done, StepStatus.skipped, StepStatus.failed):
                return True
        return False

    def _run_one(self, step: Step) -> None:
        if self._cancel.is_set():
            return
        if self._blocked(step):
            self._notify(step.id, StepStatus.skipped, "a required earlier step failed")
            return
        ctx = self.make_ctx(step)
        if self.state.status(step.id) == StepStatus.done:
            try:
                if step.check is None or step.check(ctx):
                    self._notify(step.id, StepStatus.done, "already done")
                    return
            except Exception as e:  # check must never crash the run
                ctx.log(f"check() raised {e!r}; re-running step")
        lock = None
        if step.serial_group:
            lock = self._group_locks.setdefault(step.serial_group, threading.Lock())
            lock.acquire()
        try:
            for attempt in range(1, step.retries + 1):
                self._notify(step.id, StepStatus.running, f"attempt {attempt}")
                t0 = time.time()
                try:
                    if ctx.dry_run:
                        ctx.log(f"[dry-run] would run {step.id} on {step.runs_on}")
                        res = StepResult(True, message="dry-run")
                    else:
                        res = step.run(ctx)
                except Exception as e:
                    ctx.log("".join(traceback.format_exception(e)).rstrip())
                    res = StepResult(False, message=f"{type(e).__name__}: {e}")
                dt = time.time() - t0
                if res.ok:
                    if res.secrets:
                        self.vault.update(res.secrets)
                    if res.outputs:
                        self.state.set_outputs(step.id, res.outputs)
                    self._notify(step.id, StepStatus.done, res.message or f"ok in {dt:.0f}s")
                    return
                ctx.log(f"step failed: {res.message}")
                if attempt < step.retries and not self._cancel.is_set():
                    time.sleep(min(30, 5 * attempt))
            self.failed.add(step.id)
            self._notify(step.id, StepStatus.failed, res.message, (res.secrets or {}).values())
        finally:
            if lock:
                lock.release()

    def run(self, only: Optional[set[str]] = None) -> bool:
        """Run the whole DAG (or `only` these ids plus whatever they depend on). Returns True if nothing failed."""
        layers = topo_layers(self.steps.values())
        wanted = None
        if only:
            from .dag import ancestors
            wanted = set(only)
            for sid in only:
                wanted.update(ancestors(self.steps.values(), sid))
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for layer in layers:
                if self._cancel.is_set():
                    break
                futs: list[Future] = []
                for step in layer:
                    if wanted is not None and step.id not in wanted:
                        continue
                    futs.append(pool.submit(self._run_one, step))
                for f in futs:
                    f.result()
        return not self.failed and not self._cancel.is_set()
