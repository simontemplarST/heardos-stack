"""Per-step log files, an in-memory ring buffer for the UI, and a filter that masks every secret everywhere."""
from __future__ import annotations

import collections
import logging
import re
import threading
from pathlib import Path
from typing import Callable, Iterable

from ..vault.redact import secret_values
from .state import config_dir


class RedactingFilter(logging.Filter):
    """Replaces every known secret value (8+ chars) with *** in log records. Install on every handler. JSON-object
    secrets (OVH_CREDENTIALS) are also masked field by field."""

    def __init__(self, secrets: Callable[[], Iterable[str]]):
        super().__init__()
        self._secrets = secrets

    def _pattern(self):
        vals = secret_values(self._secrets())
        return re.compile("|".join(re.escape(v) for v in vals)) if vals else None

    def filter(self, record: logging.LogRecord) -> bool:
        pat = self._pattern()
        if pat:
            msg = record.getMessage()
            red = pat.sub("***", msg)
            if red != msg:
                record.msg, record.args = red, ()
        return True


class RingHandler(logging.Handler):
    """Keeps the last N lines per logger for the UI log pane and fans out to subscribers."""

    def __init__(self, capacity: int = 2000):
        super().__init__()
        self.lines: collections.deque[tuple[str, str]] = collections.deque(maxlen=capacity)
        self._subs: list[Callable[[str, str], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[str, str], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        with self._lock:
            self.lines.append((record.name, line))
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(record.name, line)
            except Exception:  # a UI subscriber must never break the engine
                pass


class LogHub:
    """Creates one logger per step under logs/<install-id>/<step>.log plus the shared ring buffer."""

    def __init__(self, install_id: str, secrets: Callable[[], Iterable[str]]):
        self.dir = config_dir() / "logs" / install_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.redactor = RedactingFilter(secrets)
        self.ring = RingHandler()
        self.ring.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        self.ring.addFilter(self.redactor)
        self._file_handlers: dict[str, logging.Handler] = {}

    def logger(self, step_id: str) -> logging.Logger:
        name = f"heardos.{step_id}"
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        if name not in self._file_handlers:
            fh = logging.FileHandler(self.dir / f"{step_id}.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            fh.addFilter(self.redactor)
            lg.addHandler(fh)
            lg.addHandler(self.ring)
            self._file_handlers[name] = fh
        return lg

    def path(self, step_id: str) -> Path:
        return self.dir / f"{step_id}.log"
