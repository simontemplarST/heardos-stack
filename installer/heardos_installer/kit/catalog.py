"""The app catalogue (generic/catalog.json): profiles, dependency closure, public host list."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .locate import kit_path


@dataclass
class App:
    stack: str
    name: str
    blurb: str
    slug: str = ""
    hosts: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    post_install: list[str] = field(default_factory=list)
    core: bool = False

    @property
    def available(self) -> bool:
        return (kit_path("generic", "stacks", self.stack) / "compose.yml").is_file()


@lru_cache(maxsize=1)
def load() -> tuple[dict, dict[str, App]]:
    raw = json.loads(Path(kit_path("generic", "catalog.json")).read_text())
    apps = {a["stack"]: App(**{k: v for k, v in a.items() if k in App.__dataclass_fields__}) for a in raw["apps"]}
    return raw["profiles"], apps


def apps() -> dict[str, App]:
    return load()[1]


def profiles() -> dict:
    return load()[0]


def profile_apps(profile: str) -> list[str]:
    return [a.stack for a in apps().values() if profile in a.profiles and not a.core]


def closure(selected: list[str]) -> list[str]:
    """Selected apps plus everything they depend on, dependencies first, unknown names rejected."""
    cat = apps()
    out: list[str] = []

    def visit(s: str, chain: tuple = ()):
        if s not in cat:
            raise ValueError(f"unknown app {s!r}")
        if s in chain:
            raise ValueError(f"dependency cycle: {' -> '.join(chain + (s,))}")
        for d in cat[s].depends_on:
            visit(d, chain + (s,))
        if s not in out:
            out.append(s)

    for s in selected:
        visit(s)
    return [s for s in out if not cat[s].core]


def public_hosts(selected: list[str]) -> list[str]:
    cat = apps()
    hosts = ["auth"]
    for s in closure(selected):
        if not cat[s].available:   # not installable yet: no DNS record, no Caddy site
            continue
        for h in cat[s].hosts:
            if h not in hosts:
                hosts.append(h)
    return hosts


def added_by_dependency(selected: list[str]) -> list[str]:
    return [s for s in closure(selected) if s not in selected]
