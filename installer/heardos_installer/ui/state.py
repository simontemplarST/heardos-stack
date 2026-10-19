"""Everything the UI remembers between frames. Non-secret draft answers autosave to ~/.config/heardos/draft.json."""
from __future__ import annotations

import json
import re
from typing import Optional

from ..engine.state import config_dir
from ..engine.step import StepStatus
from .bus import LogBuffer, PromptBridge, Tasks

WIZARD = [
    ("welcome", "Welcome"),
    ("family", "Your family"),
    ("vps", "Public server"),
    ("home", "Home server"),
    ("dns", "Domain"),
    ("apps", "Apps"),
    ("mail", "Email"),
    ("review", "Review"),
    ("install", "Install"),
    ("done", "Finished"),
]
DAY2 = [("dashboard", "Health"), ("people", "People"), ("manage", "Apps"), ("backups", "Backups"), ("logs", "Logs"), ("settings", "Settings")]

DRAFT_KEYS = ["BRAND_TITLE", "BRAND_PRIMARY", "BRAND_LOGO", "DOMAIN", "ADMIN_USER", "ADMIN_EMAIL", "ADMIN_NAME", "TZ", "provider", "byo_ip",
              "vps_region", "vps_plan", "vps_monthly", "vps_region_name", "vps_plan_name", "HOST_LAN_IP", "HOST_SSH_USER",
              "HOST_SSH_PORT", "dns_mode", "CF_ZONE_ID", "profile", "apps", "CADDY_FRONT_DOOR", "CROWDSEC", "mail_mode", "SMTP_HOST",
              "SMTP_PORT", "SMTP_USER", "MAIL_FROM", "ovh_autopay"]


class UIState:
    def __init__(self, vault):
        self.vault = vault
        self.tasks = Tasks()
        self.prompt = PromptBridge()
        self.logbuf = LogBuffer()
        self.screen = "welcome"
        self.mode = "wizard"            # wizard | day2
        self.draft: dict = {"BRAND_TITLE": "HeardOS", "BRAND_PRIMARY": "#f2a35e", "provider": "cloudzy", "dns_mode": "cloudflare",
                            "profile": "family", "HOST_SSH_USER": "root", "CADDY_FRONT_DOOR": True, "CROWDSEC": True, "mail_mode": "resend"}
        self.inputs: dict[str, str] = {}   # secrets typed but not yet saved, and transient form text
        self.inst = None                   # session.Installation once Install is pressed (or an install is resumed)
        self.step_status: dict[str, tuple[StepStatus, str]] = {}
        self.selected_step: Optional[str] = None
        self.install_done: Optional[bool] = None
        self.cost_ack = False
        self.messages: dict[str, tuple[bool, str]] = {}   # per-screen validation results
        self.load_draft()

    # --- draft persistence (never secrets) ---
    @property
    def draft_path(self):
        return config_dir() / "draft.json"

    def load_draft(self) -> None:
        try:
            self.draft.update(json.loads(self.draft_path.read_text()))
        except (OSError, ValueError):
            pass

    def save_draft(self) -> None:
        try:
            self.draft_path.write_text(json.dumps({k: self.draft.get(k) for k in DRAFT_KEYS if k in self.draft}, indent=1))
        except OSError:
            pass

    # --- navigation ---
    def go(self, screen: str) -> None:
        self.save_draft()
        self.screen = screen

    def next(self) -> None:
        ids = [s for s, _ in WIZARD]
        i = ids.index(self.screen)
        if i + 1 < len(ids):
            self.go(ids[i + 1])

    def back(self) -> None:
        ids = [s for s, _ in WIZARD]
        i = ids.index(self.screen)
        if i > 0:
            self.go(ids[i - 1])

    # --- engine hookup ---
    def on_step(self, sid: str, status: StepStatus, msg: str) -> None:
        self.step_status[sid] = (status, msg)


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
USERNAME = re.compile(r"^[a-z][a-z0-9._-]{1,30}$")
IPV4 = re.compile(r"^((25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(25[0-5]|2[0-4]\d|1?\d?\d)$")
