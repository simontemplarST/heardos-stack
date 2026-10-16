"""Copy the kit to a remote machine as a tarball streamed over the session (no rsync/scp dependency)."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from .ssh import Session

EXCLUDE = {"__pycache__", ".git", "node_modules", ".venv"}


def _tar_bytes(root: Path, subdirs: list[str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for sub in subdirs:
            base = root / sub
            if not base.exists():
                continue
            for p in sorted(base.rglob("*")):
                if any(part in EXCLUDE for part in p.relative_to(root).parts) or p.suffix == ".pyc":
                    continue
                tar.add(p, arcname=str(p.relative_to(root)), recursive=False)
    return buf.getvalue()


def push_kit(session: Session, kit_root: Path, dest: str = "/opt/heardos-kit", subdirs: list[str] | None = None,
             sudo: bool = False) -> None:
    """Replace <dest> with the selected kit directories. Idempotent; the host's plan file lives outside dest."""
    subdirs = subdirs or ["generic", "authentik", "security", "mail", "apps", "edge", "brand"]
    data = _tar_bytes(kit_root, subdirs)
    session.put_text("", "/tmp/.heardos-kit-marker")  # ensures sftp works before the larger upload
    with session._c().open_sftp() as sftp:  # noqa: SLF001 — same client, binary upload
        with sftp.file("/tmp/heardos-kit.tgz", "wb") as f:
            f.write(data)
    session.run(
        f"mkdir -p {dest} && rm -rf {dest}/generic {dest}/authentik {dest}/security {dest}/mail {dest}/apps {dest}/edge {dest}/brand && "
        f"tar -xzf /tmp/heardos-kit.tgz -C {dest} && rm -f /tmp/heardos-kit.tgz /tmp/.heardos-kit-marker && "
        f"chmod +x {dest}/generic/*.sh {dest}/generic/*.py {dest}/generic/lib/*.py 2>/dev/null; true",
        sudo=sudo, check=True, timeout=300,
    )


def put_dir(session: Session, local: Path, remote: str, *, sudo: bool = False, owner: str = "", clean: bool = False) -> None:
    """Upload the contents of a local directory to <remote> (created; emptied first when clean=True)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(Path(local).rglob("*")):
            tar.add(p, arcname=str(p.relative_to(local)), recursive=False)
    tmp = f"/tmp/heardos-dir-{abs(hash(remote))}.tgz"
    with session._c().open_sftp() as sftp:  # noqa: SLF001
        with sftp.file(tmp, "wb") as f:
            f.write(buf.getvalue())
    cmd = (f"mkdir -p {remote} && " + (f"find {remote} -mindepth 1 -delete && " if clean else "") +
           f"tar -xzf {tmp} -C {remote} --no-same-owner && rm -f {tmp}" + (f" && chown -R {owner} {remote}" if owner else ""))
    session.run(cmd, sudo=sudo, check=True, timeout=300)
