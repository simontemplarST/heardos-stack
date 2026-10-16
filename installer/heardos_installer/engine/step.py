"""A Step is one node of the install DAG: where it runs, what it needs, what it produces, how to tell it is done."""
from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

RunsOn = Literal["local", "vps", "host"]


class StepStatus(str, enum.Enum):
    pending = "pending"
    ready = "ready"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


@dataclass
class StepResult:
    ok: bool
    outputs: dict[str, Any] = field(default_factory=dict)   # plan/state keys this step produced
    secrets: dict[str, str] = field(default_factory=dict)   # values that go into the vault, never into state
    message: str = ""                                       # one line for the UI
    stdout: str = field(default="", repr=False)             # raw output of a kit script (day-two ops parse it; never persisted)


class Ctx:
    """What a step gets: the plan (non-secret), the vault (secrets), sessions to the remote machines, a logger.

    Steps call ctx.log(line) for streaming output and ctx.secret(name) for credentials. Everything is injected by
    the runner so steps stay testable with fakes.
    """

    def __init__(self, plan: dict[str, Any], vault, sessions, logger, state, kit_root, workdir, dry_run: bool = False,
                 prompt: Optional[Callable[[str, str, Callable[[], bool]], bool]] = None):
        self.plan = plan
        self.vault = vault
        self.sessions = sessions          # remote.SessionPool: .vps(), .host()
        self.logger = logger              # logging.Logger for this step
        self.state = state                # InstallState
        self.kit_root = kit_root
        self.workdir = workdir            # per-install working dir (plan files, kit copy)
        self.dry_run = dry_run
        self._prompt = prompt             # checklist hook: prompt(title, instructions, poll) -> True when done
        self.cancel = threading.Event()

    def log(self, line: str) -> None:
        self.logger.info(line.rstrip("\n"))

    def secret(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.vault.get(name, default)

    def checklist(self, title: str, instructions: str, poll: Callable[[], bool]) -> bool:
        """Hand a manual step to the UI/CLI; returns True once poll() succeeds or the operator confirms."""
        if self._prompt is None:
            raise RuntimeError(f"manual step required but no prompt handler: {title}")
        return self._prompt(title, instructions, poll)


@dataclass
class Step:
    id: str
    runs_on: RunsOn
    run: Callable[[Ctx], StepResult]
    deps: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)     # vault keys this step may read (least privilege)
    outputs: list[str] = field(default_factory=list)    # keys it promises to produce
    check: Optional[Callable[[Ctx], bool]] = None       # "already done?" for idempotency / resume
    timeout: int = 900
    retries: int = 1
    title: str = ""                                     # plain-word label for the UI
    optional: bool = False                              # failure does not stop dependants
    serial_group: Optional[str] = None                  # steps sharing a group never run concurrently (e.g. "host-apt")

    def __post_init__(self):
        if not self.title:
            self.title = self.id.replace(".", " ").replace("_", " ")
