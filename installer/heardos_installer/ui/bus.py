"""Never block the frame: anything slow (API calls, SSH, the install itself) runs in a thread and reports here.

Task wraps one background call; the UI polls .running/.result/.error each frame. PromptBridge lets an engine thread ask
the operator to do something by hand (create DNS records, click Verify) and wait while the UI shows the instructions.
"""
from __future__ import annotations

import collections
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


class Task:
    def __init__(self, fn: Callable[[], Any], name: str = ""):
        self.name = name
        self.result: Any = None
        self.error: Optional[str] = None
        self.started = time.time()
        self._done = threading.Event()
        self._t = threading.Thread(target=self._run, args=(fn,), daemon=True, name=f"task-{name}")
        self._t.start()

    def _run(self, fn):
        try:
            self.result = fn()
        except Exception as e:  # noqa: BLE001 — shown to the user as a plain message
            self.error = str(e) or type(e).__name__
            traceback.print_exc()
        finally:
            self._done.set()

    @property
    def running(self) -> bool:
        return not self._done.is_set()

    @property
    def ok(self) -> bool:
        return self._done.is_set() and self.error is None


class Tasks:
    """Named task slots: starting a task with the same name replaces the old one."""

    def __init__(self):
        self._t: dict[str, Task] = {}

    def start(self, name: str, fn: Callable[[], Any]) -> Task:
        t = Task(fn, name)
        self._t[name] = t
        return t

    def get(self, name: str) -> Optional[Task]:
        return self._t.get(name)

    def running(self, name: str) -> bool:
        t = self._t.get(name)
        return bool(t and t.running)

    def clear(self, name: str) -> None:
        self._t.pop(name, None)


@dataclass
class PendingPrompt:
    title: str
    instructions: str
    poll: Callable[[], bool]
    confirmed: threading.Event = field(default_factory=threading.Event)
    rejected: bool = False          # the operator pressed Cancel (e.g. a host key fingerprint did not match)
    manual_only: bool = False       # poll() can never succeed; only the operator can answer
    last_check: float = 0.0
    last_result: bool = False


class PromptBridge:
    """Called from engine threads; blocks until poll() succeeds or the operator confirms (True) or cancels (False) in the UI."""

    def __init__(self, interval: int = 15):
        self.interval = interval
        self.pending: Optional[PendingPrompt] = None
        self._lock = threading.Lock()

    def __call__(self, title: str, instructions: str, poll: Callable[[], bool]) -> bool:
        p = PendingPrompt(title, instructions, poll, manual_only=bool(getattr(poll, "manual_only", False)))
        with self._lock:
            self.pending = p
        try:
            while not p.confirmed.is_set():
                try:
                    p.last_result = bool(poll())
                except Exception:  # noqa: BLE001
                    p.last_result = False
                p.last_check = time.time()
                if p.last_result:
                    return True
                p.confirmed.wait(self.interval)
            return not p.rejected
        finally:
            with self._lock:
                self.pending = None


class LogBuffer:
    """Thread-safe tail of log lines for the log pane (fed by the engine's RingHandler)."""

    def __init__(self, maxlen: int = 4000):
        self.lines: collections.deque[tuple[str, str]] = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.version = 0

    def add(self, logger_name: str, line: str) -> None:
        with self._lock:
            self.lines.append((logger_name.removeprefix("heardos."), line))
            self.version += 1

    def snapshot(self, step: Optional[str] = None) -> list[tuple[str, str]]:
        with self._lock:
            return [x for x in self.lines if step is None or x[0] == step]
