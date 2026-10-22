# PyInstaller spec: one-file HeardOS app for Windows, macOS and Linux.  Build from installer/:
#   pip install -e .[dev] && pyinstaller heardos.spec      ->  dist/HeardOS(.exe) / dist/HeardOS.app
# The kit (generic/, authentik/, ...) is bundled under kit/ and found via sys._MEIPASS (kit/locate.py).
import re
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = Path(SPECPATH)
REPO = HERE.parent
KIT_DIRS = [d.strip().strip('"') for d in re.search(r"KIT_DIRS = \((.*?)\)", (HERE / "heardos_installer/kit/locate.py").read_text()).group(1).split(",") if d.strip()]
SKIP = ("__pycache__", ".venv", "node_modules", ".pyc", ".bak")
SKIP_SUFFIX = (".env",)    # real env files hold credentials; *.env.example templates are still bundled

datas = collect_data_files("imgui_bundle")          # fonts and assets hello_imgui loads at start-up
for d in KIT_DIRS:
    for p in (REPO / d).rglob("*"):
        if p.is_file() and not any(s in str(p) for s in SKIP) and not p.name.endswith(SKIP_SUFFIX):
            datas.append((str(p), str(Path("kit") / p.parent.relative_to(REPO))))

hidden = collect_submodules("keyring.backends") + collect_submodules("dns") + ["nacl.bindings", "paramiko"]
hidden += collect_submodules("heardos_installer.manage") + collect_submodules("heardos_installer.harness")   # imported lazily by the Stacks/Assistant screens

a = Analysis([str(HERE / "heardos_installer" / "gui_entry.py")], pathex=[str(HERE)], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "matplotlib", "IPython", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
icon = str(REPO / "brand/heardos/app-icon-512.png")
if sys.platform == "darwin":          # macOS: a .app folder (one-file inside a .app clashes with Gatekeeper)
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="HeardOS", console=False, upx=False, icon=icon)
    coll = COLLECT(exe, a.binaries, a.datas, name="HeardOS", upx=False)
    app = BUNDLE(coll, name="HeardOS.app", icon=icon, bundle_identifier="org.heardos.installer",
                 info_plist={"NSHighResolutionCapable": True, "LSMinimumSystemVersion": "12.0"})
else:                                 # Windows / Linux: one self-contained file
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="HeardOS", console=False, upx=False,
              icon=icon if sys.platform == "win32" else None)
