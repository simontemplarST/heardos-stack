"""Nebula CA and node bundles, done natively on the operator's machine (Linux, macOS or Windows).

The kit's 20-nebula-ca.sh does the same with bash; this module exists so the installer needs no bash locally. It reuses
the kit's config templates (generic/lib/nebula-*.yml.tmpl) and remote installer (generic/lib/nebula-install.sh).
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import shlex
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


PINNED_VERSION = "v1.11.2"   # tested with the kit; override with HEARDOS_NEBULA_VERSION (then SHASUM256.txt is checked)

# SHA-256 of the release assets of PINNED_VERSION, copied from its SHASUM256.txt on GitHub. A download that does not
# match is refused. Other versions are checked against that release's SHASUM256.txt, fetched over HTTPS.
KNOWN_SHA256 = {
    "v1.11.2": {
        "nebula-darwin.zip": "ae23ebd29e570d72f28d71e608261d454fb87b1a19b4ca21c2aa26bb4698da88",
        "nebula-linux-amd64.tar.gz": "6140d33f2ec21ce7f6b655b5bc820e93a684d97e51d0ddcf907324b5b28aac1e",
        "nebula-linux-arm64.tar.gz": "85d10e7bc2d121193c1392a1a919172ded7c413f46e602138281cfa9fa1b0231",
        "nebula-linux-arm-7.tar.gz": "dc9c890d7ec014ab709e0444592b33f35db4f396f28245c95887844b1565ff41",
        "nebula-windows-amd64.zip": "adbb2201c86fcce7bf68a1d4ebbeb7570b32fdb73ce9044296ea47a17ad596d7",
        "nebula-windows-arm64.zip": "faf943e46e4267398012bf119804671d919b00609f6701e86e034b05ec187984",
    },
}


def _latest_version() -> str:
    want = os.environ.get("HEARDOS_NEBULA_VERSION", PINNED_VERSION)
    if want == "latest":
        # "latest" used to resolve through the GitHub API with no integrity check at all; a pin is required now
        raise RuntimeError("HEARDOS_NEBULA_VERSION=latest is no longer supported; set a release tag such as v1.11.2")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", want):
        raise RuntimeError(f"HEARDOS_NEBULA_VERSION={want!r} is not a release tag like v1.11.2")
    return want


def _expected_sha256(version: str, asset: str) -> str:
    known = KNOWN_SHA256.get(version)
    if known is not None:
        if asset not in known:
            raise RuntimeError(f"no known checksum for {asset} in nebula {version}")
        return known[asset]
    url = f"https://github.com/slackhq/nebula/releases/download/{version}/SHASUM256.txt"
    with urllib.request.urlopen(url, timeout=60) as r:
        for line in r.read().decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == asset:
                return parts[0].lower()
    raise RuntimeError(f"{asset} is not listed in nebula {version}'s SHASUM256.txt")


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
    asset = _asset(os_name, arch)
    url = f"https://github.com/slackhq/nebula/releases/download/{version}/{asset}"
    log(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    want, got = _expected_sha256(version, asset), hashlib.sha256(data).hexdigest()
    if got != want:
        raise RuntimeError(f"checksum mismatch for {asset} ({version}): got {got}, expected {want}; refusing to use it")
    log(f"{asset}: sha256 verified")
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
        args = ["sign", "-ca-crt", "ca.crt", "-ca-key", "ca.key", "-name", name, "-ip", f"{ip}/{prefix}", "-groups", groups, "-duration", "8760h"]   # 1 year, as 20-nebula-ca.sh; re-sign to renew
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
            # per-role inbound rules (as 20-nebula-ca.sh): the home host takes admins on any port + the lighthouse on 80/tcp;
            # every other node (laptops, phones) only answers ICMP, so a compromised server can't reach them
            "INGRESS_RULE": ("    - {port: any, proto: any, group: admins}\n    - {port: 80, proto: tcp, group: lighthouse}"
                             if ingress else "")}
    out = string.Template(tmpl.read_text()).safe_substitute(vals)
    blocked = [l.strip() for l in (plan.get("NEBULA_BLOCKLIST") or "").replace(",", " ").split() if l.strip()]
    if blocked:   # pki.blocklist: revoked certificate fingerprints, right after the key: line
        lines = out.split("\n"); i = next(n for n, l in enumerate(lines) if l.startswith("  key:"))
        lines[i + 1:i + 1] = ["  blocklist:"] + [f"    - {f}" for f in blocked]
        out = "\n".join(lines)
    return out


def install_node(session, ca: CA, node: str, config: str, remote_arch: str, kit_root: Path, log: Callable[[str], None]) -> None:
    """Stage bundle + binary on the target via sftp, then run the kit's nebula-install.sh there."""
    tools = fetch("linux", norm_arch(remote_arch), None, log)
    stage = session.tempdir()   # fresh 0700 dir, owned by the login user so sftp can write into it
    for fname, data in ca.files(node).items():
        session.put_text(data.decode(), f"{stage}/{fname}", 0o600)
    session.put_text(config, f"{stage}/config.yml", 0o600)
    session.put_text(NEBULA_SERVICE, f"{stage}/nebula.service", 0o644)
    session.put_text((kit_root / "generic" / "lib" / "nebula-install.sh").read_text(), f"{stage}/install.sh", 0o700)
    session.put(tools / "nebula", f"{stage}/nebula", 0o755)
    q = shlex.quote(stage)
    r = session.run(f"bash {q}/install.sh; rc=$?; rm -rf {q}; exit $rc", sudo=True, on_line=log, timeout=180)
    if not r.ok:
        raise RuntimeError(f"nebula install on {session.host} failed: {r.stderr[-400:]}")
