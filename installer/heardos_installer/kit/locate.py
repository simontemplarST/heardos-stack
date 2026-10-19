"""Find the kit (generic/, authentik/, security/, mail/, apps/, edge/) wherever this package happens to run from.

Resolution order: $HEARDOS_KIT, a PyInstaller bundle (sys._MEIPASS/kit), the repository checkout this file lives in,
then package data copied in at build time. Only this module knows about these locations.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

KIT_DIRS = ("generic", "authentik", "security", "mail", "apps", "edge", "nebula-lan", "brand")


def _looks_like_kit(p: Path) -> bool:
    return (p / "generic" / "lib" / "common.sh").is_file()


_warned: set[str] = set()


def kit_root() -> Path:
    env = os.environ.get("HEARDOS_KIT")
    if env and _looks_like_kit(Path(env)):
        p = Path(env).resolve()
        try:
            default = _default_root().resolve()
        except FileNotFoundError:
            default = None
        if p != default and str(p) not in _warned:
            # these scripts run as root on both servers: say loudly that they are not the ones shipped with this app
            _warned.add(str(p))
            msg = f"HEARDOS_KIT points at {p}, not the kit shipped with this installer; those scripts will run as root on your servers"
            logging.getLogger("heardos").warning(msg)
            print(f"warning: {msg}", file=sys.stderr)
        return p
    return _default_root()


def _default_root() -> Path:
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
