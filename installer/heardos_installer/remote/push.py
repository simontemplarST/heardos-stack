"""Copy the kit to a remote machine as a tarball streamed over the session (no rsync/scp dependency)."""
from __future__ import annotations

import io
import shlex
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
    stage = session.tempdir()   # fresh 0700 dir (also proves sftp works before the larger upload)
    tgz = f"{stage}/heardos-kit.tgz"
    with session._c().open_sftp() as sftp:  # noqa: SLF001 — same client, binary upload
        with sftp.file(tgz, "wb") as f:
            f.write(data)
    q = shlex.quote(dest)
    wipe = " ".join(f"{q}/{d}" for d in ("generic", "authentik", "security", "mail", "apps", "edge", "brand"))
    session.run(
        f"mkdir -p {q} && rm -rf {wipe} && "
        f"tar -xzf {shlex.quote(tgz)} -C {q} && rm -rf {shlex.quote(stage)} && "
        f"chmod +x {q}/generic/*.sh {q}/generic/*.py {q}/generic/lib/*.py 2>/dev/null; true",
        sudo=sudo, check=True, timeout=300,
    )


def put_dir(session: Session, local: Path, remote: str, *, sudo: bool = False, owner: str = "", clean: bool = False) -> None:
    """Upload the contents of a local directory to <remote> (created; emptied first when clean=True)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(Path(local).rglob("*")):
            tar.add(p, arcname=str(p.relative_to(local)), recursive=False)
    stage = session.tempdir()
    tmp = f"{stage}/dir.tgz"
    with session._c().open_sftp() as sftp:  # noqa: SLF001
        with sftp.file(tmp, "wb") as f:
            f.write(buf.getvalue())
    r = shlex.quote(remote)
    cmd = (f"mkdir -p {r} && " + (f"find {r} -mindepth 1 -delete && " if clean else "") +
           f"tar -xzf {shlex.quote(tmp)} -C {r} --no-same-owner && rm -rf {shlex.quote(stage)}" +
           (f" && chown -R {shlex.quote(owner)} {r}" if owner else ""))
    session.run(cmd, sudo=sudo, check=True, timeout=300)
