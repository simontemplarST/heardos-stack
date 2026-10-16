"""Copies the kit (../generic, ../authentik, ...) into the wheel as heardos_installer/kit/data, so `pipx install ./installer`
works without a repository checkout next to it. Editable installs keep using the checkout (see kit/locate.py)."""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

REPO = Path(__file__).resolve().parent.parent
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "node_modules", "*.bak*")


class BuildWithKit(build_py):
    def run(self):
        super().run()
        import re
        KIT_DIRS = re.search(r"KIT_DIRS = \((.*?)\)", (Path(__file__).parent / "heardos_installer/kit/locate.py").read_text()).group(1)
        KIT_DIRS = [d.strip().strip('"') for d in KIT_DIRS.split(",") if d.strip()]
        dest = Path(self.build_lib) / "heardos_installer" / "kit" / "data"
        for d in KIT_DIRS:
            if (REPO / d).is_dir():
                shutil.copytree(REPO / d, dest / d, dirs_exist_ok=True, ignore=SKIP)


setup(cmdclass={"build_py": BuildWithKit})
