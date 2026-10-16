"""Find the kit (generic/, authentik/, security/, mail/, apps/, edge/) wherever this package happens to run from.

Resolution order: $HEARDOS_KIT, a PyInstaller bundle (sys._MEIPASS/kit), the repository checkout this file lives in,
then package data copied in at build time. Only this module knows about these locations.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

KIT_DIRS = ("generic", "authentik", "security", "mail", "apps", "edge", "nebula-lan", "brand")


def _looks_like_kit(p: Path) -> bool:
    return (p / "generic" / "lib" / "common.sh").is_file()


def kit_root() -> Path:
    env = os.environ.get("HEARDOS_KIT")
    if env and _looks_like_kit(Path(env)):
        return Path(env).resolve()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and _looks_like_kit(Path(meipass) / "kit"):
        return Path(meipass) / "kit"
    here = Path(__file__).resolve()
    for parent in here.parents:  # installer/heardos_installer/kit/locate.py -> repo root
        if _looks_like_kit(parent):
            return parent
    data = here.parent / "data"
    if _looks_like_kit(data):
        return data
    raise FileNotFoundError(
        "HeardOS kit not found: set HEARDOS_KIT to a checkout of heardos-stack (the directory holding generic/)"
    )


def kit_path(*parts: str) -> Path:
    return kit_root().joinpath(*parts)
