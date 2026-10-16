"""Nebula CA and node bundles, done natively on the operator's machine (Linux, macOS or Windows).

The kit's 20-nebula-ca.sh does the same with bash; this module exists so the installer needs no bash locally. It reuses
the kit's config templates (generic/lib/nebula-*.yml.tmpl) and remote installer (generic/lib/nebula-install.sh).
"""
from __future__ import annotations

import io
import json
import os
import platform
import stat
import string
import subprocess
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .engine.state import config_dir

NEBULA_SERVICE = """[Unit]
Description=Nebula overlay network (heardos)
Wants=basic.target network-online.target
After=basic.target network-online.target
[Service]
Type=notify
NotifyAccess=main
SyslogIdentifier=nebula
ExecReload=/bin/kill -HUP $MAINPID
ExecStart=/usr/local/bin/nebula -config /etc/nebula/config.yml
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
"""


PINNED_VERSION = "v1.11.2"   # tested with the kit; override with HEARDOS_NEBULA_VERSION (or "latest")


def _latest_version() -> str:
    want = os.environ.get("HEARDOS_NEBULA_VERSION", PINNED_VERSION)
    if want != "latest":
        return want
    with urllib.request.urlopen("https://api.github.com/repos/slackhq/nebula/releases/latest", timeout=30) as r:
        return json.load(r)["tag_name"]


def _asset(os_name: str, arch: str) -> str:
    if os_name == "darwin":
        return "nebula-darwin.zip"
    if os_name == "windows":
        return f"nebula-windows-{arch}.zip"
    return f"nebula-linux-{arch}.tar.gz"


def norm_arch(machine: str) -> str:
    m = machine.lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, "arm-7" if m.startswith("armv7") else m)


def bin_dir(version: str) -> Path:
    d = config_dir() / "bin" / version
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch(os_name: str, arch: str, version: Optional[str] = None, log: Callable[[str], None] = print) -> Path:
    """Download and unpack nebula + nebula-cert for a platform into the cache; returns that directory."""
    version = version or _latest_version()
    dest = bin_dir(version) / f"{os_name}-{arch}"
    exe = ".exe" if os_name == "windows" else ""
    if (dest / f"nebula{exe}").exists() and (dest / f"nebula-cert{exe}").exists():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/slackhq/nebula/releases/download/{version}/{_asset(os_name, arch)}"
    log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                base = Path(n).name
                if base in (f"nebula{exe}", f"nebula-cert{exe}"):
                    (dest / base).write_bytes(z.read(n))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            for m in t.getmembers():
                if Path(m.name).name in ("nebula", "nebula-cert"):
                    (dest / Path(m.name).name).write_bytes(t.extractfile(m).read())
    for f in dest.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return dest


def local_platform() -> tuple[str, str]:
    s = platform.system().lower()
    return ("windows" if s.startswith("win") else s), norm_arch(platform.machine())


@dataclass
class CA:
    dir: Path
    cert_tool: Path

    @staticmethod
    def open(install_id: str, version: Optional[str] = None, log: Callable[[str], None] = print) -> "CA":
        d = config_dir() / "nebula" / install_id
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        osn, arch = local_platform()
        tools = fetch(osn, arch, version, log)
        return CA(d, tools / ("nebula-cert.exe" if osn == "windows" else "nebula-cert"))

    def _run(self, *args: str) -> None:
        r = subprocess.run([str(self.cert_tool), *args], cwd=self.dir, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"nebula-cert {' '.join(args[:2])} failed: {r.stderr.strip() or r.stdout.strip()}")

    def ensure_ca(self, name: str = "heardos") -> None:
        if not (self.dir / "ca.crt").exists():
            self._run("ca", "-name", name, "-duration", "87600h")
            os.chmod(self.dir / "ca.key", 0o600)

    def sign(self, name: str, ip: str, prefix: int, groups: str, subnets: str = "") -> None:
        if (self.dir / f"{name}.crt").exists():
            return
        args = ["sign", "-ca-crt", "ca.crt", "-ca-key", "ca.key", "-name", name, "-ip", f"{ip}/{prefix}", "-groups", groups, "-duration", "43800h"]
        if subnets:
            args += ["-subnets", subnets]
        self._run(*args)
        os.chmod(self.dir / f"{name}.key", 0o600)

    def files(self, name: str) -> dict[str, bytes]:
        return {"ca.crt": (self.dir / "ca.crt").read_bytes(), "host.crt": (self.dir / f"{name}.crt").read_bytes(),
                "host.key": (self.dir / f"{name}.key").read_bytes()}


def render_config(kit_root: Path, node: str, plan: dict, lighthouse: bool, ingress: bool) -> str:
    tmpl = kit_root / "generic" / "lib" / ("nebula-lighthouse.yml.tmpl" if lighthouse else "nebula-node.yml.tmpl")
    vals = {"NODE_NAME": node, "OVERLAY_CIDR": plan["OVERLAY_CIDR"], "NEBULA_LH_IP": plan.get("NEBULA_LH_IP", ""),
            "NEBULA_LH_OVERLAY": plan["NEBULA_LH_OVERLAY"], "NEBULA_ROUTINES": "1",
            "INGRESS_RULE": "    - {port: 80, proto: tcp, group: lighthouse}" if ingress else ""}
    return string.Template(tmpl.read_text()).safe_substitute(vals)


def install_node(session, ca: CA, node: str, config: str, remote_arch: str, kit_root: Path, log: Callable[[str], None]) -> None:
    """Stage bundle + binary on the target via sftp, then run the kit's nebula-install.sh there."""
    tools = fetch("linux", norm_arch(remote_arch), None, log)
    stage = "/tmp/nebula-stage"
    session.run(f"rm -rf {stage} && mkdir -p {stage} && chmod 700 {stage}", sudo=True, check=True)
    if session.user != "root":
        session.run(f"chown {session.user} {stage}", sudo=True, check=True)
    for fname, data in ca.files(node).items():
        session.put_text(data.decode(), f"{stage}/{fname}", 0o600)
    session.put_text(config, f"{stage}/config.yml", 0o600)
    session.put_text(NEBULA_SERVICE, f"{stage}/nebula.service", 0o644)
    session.put_text((kit_root / "generic" / "lib" / "nebula-install.sh").read_text(), f"{stage}/install.sh", 0o700)
    session.put(tools / "nebula", f"{stage}/nebula", 0o755)
    r = session.run(f"bash {stage}/install.sh && rm -rf {stage}", sudo=True, on_line=log, timeout=180)
    if not r.ok:
        raise RuntimeError(f"nebula install on {session.host} failed: {r.stderr[-400:]}")
