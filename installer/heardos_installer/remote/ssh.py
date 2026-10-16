"""SSH to the VPS and the home host with paramiko: streaming output, env injection, password-then-key bootstrap.

Why paramiko: one implementation on Linux, macOS and Windows, no dependency on an OpenSSH client, agent or sshpass,
and we can stream stdout line by line into the step log while the command runs.
"""
from __future__ import annotations

import io
import shlex
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import paramiko

from ..engine.state import config_dir


class SshError(RuntimeError):
    pass


@dataclass
class Result:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def outputs(self) -> dict[str, str]:
        """Collect `HEARDOS_OUTPUT KEY=VALUE` lines the kit scripts print for the installer."""
        out: dict[str, str] = {}
        for line in self.stdout.splitlines():
            if line.startswith("HEARDOS_OUTPUT "):
                kv = line[len("HEARDOS_OUTPUT "):].strip()
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    out[k.strip()] = v.strip()
        return out


def known_hosts_file() -> Path:
    p = config_dir() / "known_hosts"
    p.touch(exist_ok=True)
    return p


class _Pin(paramiko.MissingHostKeyPolicy):
    """Trust on first use, pinned in our own known_hosts; a changed key is refused."""

    def __init__(self, client: paramiko.SSHClient):
        self.client = client

    def missing_host_key(self, client, hostname, key):
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(known_hosts_file()))


class Session:
    def __init__(self, host: str, user: str = "root", port: int = 22, key_path: Optional[Path] = None,
                 password: Optional[str] = None, connect_timeout: int = 20):
        self.host, self.user, self.port = host, user, port
        self.key_path = Path(key_path) if key_path else None
        self.password = password
        self.connect_timeout = connect_timeout
        self._client: Optional[paramiko.SSHClient] = None
        self._lock = threading.Lock()

    # --- connection ---
    def connect(self) -> "Session":
        c = paramiko.SSHClient()
        c.load_host_keys(str(known_hosts_file()))
        c.set_missing_host_key_policy(_Pin(c))
        kw = dict(hostname=self.host, port=self.port, username=self.user, timeout=self.connect_timeout,
                  banner_timeout=30, auth_timeout=30, allow_agent=False, look_for_keys=False)
        if self.key_path:
            kw["key_filename"] = str(self.key_path)
        if self.password:
            kw["password"] = self.password
        try:
            c.connect(**kw)
        except paramiko.BadHostKeyException as e:
            raise SshError(f"host key for {self.host} changed; remove it from {known_hosts_file()} if this is expected") from e
        except (paramiko.AuthenticationException,) as e:
            raise SshError(f"authentication to {self.user}@{self.host} failed") from e
        except (paramiko.SSHException, socket.error, OSError) as e:
            raise SshError(f"cannot reach {self.user}@{self.host}:{self.port}: {e}") from e
        c.get_transport().set_keepalive(15)
        self._client = c
        return self

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def _c(self) -> paramiko.SSHClient:
        if self._client is None or self._client.get_transport() is None or not self._client.get_transport().is_active():
            self.connect()
        return self._client  # type: ignore[return-value]

    # --- commands ---
    def run(self, cmd: str, *, sudo: bool = False, env: Optional[dict[str, str]] = None, timeout: int = 900,
            on_line: Optional[Callable[[str], None]] = None, cwd: Optional[str] = None, check: bool = False) -> Result:
        """Run a shell command. env values are shell-quoted on the command line, never written to the remote disk."""
        parts = []
        if cwd:
            parts.append(f"cd {shlex.quote(cwd)} &&")
        if env:
            parts.append("env " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()))
        inner = f"bash -lc {shlex.quote(cmd)}"
        full = " ".join(parts + [inner])
        if sudo and self.user != "root":
            full = "sudo -n -H " + full
        with self._lock:
            chan = self._c().get_transport().open_session()
            chan.settimeout(timeout)
            chan.exec_command(full)
            out, err = io.StringIO(), io.StringIO()
            buf = ""
            deadline = time.time() + timeout
            while True:
                if chan.recv_ready():
                    chunk = chan.recv(65536).decode("utf-8", "replace")
                    out.write(chunk)
                    if on_line:
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            on_line(line)
                if chan.recv_stderr_ready():
                    chunk = chan.recv_stderr(65536).decode("utf-8", "replace")
                    err.write(chunk)
                    if on_line:
                        for line in chunk.splitlines():
                            on_line(line)
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                if time.time() > deadline:
                    chan.close()
                    raise SshError(f"command timed out after {timeout}s: {cmd[:80]}")
                time.sleep(0.05)
            if on_line and buf:
                on_line(buf)
            rc = chan.recv_exit_status()
            chan.close()
        res = Result(rc, out.getvalue(), err.getvalue())
        if check and not res.ok:
            raise SshError(f"command failed ({rc}): {cmd[:120]}\n{res.stderr[-800:]}")
        return res

    def put(self, local: Path, remote: str, mode: int = 0o600) -> None:
        with self._lock:
            sftp = self._c().open_sftp()
            try:
                sftp.put(str(local), remote)
                sftp.chmod(remote, mode)
            finally:
                sftp.close()

    def put_text(self, text: str, remote: str, mode: int = 0o600) -> None:
        with self._lock:
            sftp = self._c().open_sftp()
            try:
                with sftp.file(remote, "w") as f:
                    f.write(text)
                sftp.chmod(remote, mode)
            finally:
                sftp.close()

    def get(self, remote: str) -> bytes:
        with self._lock:
            sftp = self._c().open_sftp()
            try:
                with sftp.file(remote, "rb") as f:
                    return f.read()
            finally:
                sftp.close()

    # --- bootstrap helpers ---
    def install_pubkey(self, pubkey: str) -> None:
        """Append our public key to the remote user's authorized_keys (idempotent)."""
        key = pubkey.strip()
        self.run(
            "install -d -m 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
            f"grep -qF {shlex.quote(key)} ~/.ssh/authorized_keys || echo {shlex.quote(key)} >> ~/.ssh/authorized_keys",
            check=True,
        )

    def can_sudo(self) -> bool:
        if self.user == "root":
            return True
        return self.run("sudo -n true", timeout=30).ok


def wait_ssh(host: str, user: str = "root", key_path: Optional[Path] = None, password: Optional[str] = None,
             timeout: int = 600, interval: int = 5, on_line: Optional[Callable[[str], None]] = None) -> Session:
    """Retry until sshd answers and we can authenticate (new VPSes take a minute or two)."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            s = Session(host, user, key_path=key_path, password=password, connect_timeout=10).connect()
            s.run("true", timeout=20, check=True)
            return s
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if on_line:
                on_line(f"waiting for ssh on {host}: {last[:80]}")
            time.sleep(interval)
    raise SshError(f"ssh to {user}@{host} not reachable after {timeout}s: {last}")


def wait_cloud_init(s: Session, timeout: int = 900, retries: int = 5, on_line: Optional[Callable[[str], None]] = None) -> None:
    """Providers' first-boot cloud-init fights our apt runs; wait for it (it can drop the ssh connection mid-way)."""
    for i in range(retries):
        try:
            r = s.run("command -v cloud-init >/dev/null && cloud-init status --wait || echo no-cloud-init", timeout=timeout, on_line=on_line)
            if r.ok:
                return
        except SshError as e:
            if on_line:
                on_line(f"cloud-init wait interrupted ({e}); retrying")
            time.sleep(10)
    raise SshError("cloud-init did not finish")


class SessionPool:
    """Lazily opened, reused sessions to the two remote machines, built from the plan + vault."""

    def __init__(self, plan: dict, vault, key_path: Path):
        self.plan, self.vault, self.key_path = plan, vault, key_path
        self._vps: Optional[Session] = None
        self._host: Optional[Session] = None

    def vps(self) -> Session:
        if self._vps is None:
            self._vps = Session(self.plan["NEBULA_LH_IP"], "root", key_path=self.key_path).connect()
        return self._vps

    def host(self) -> Session:
        if self._host is None:
            self._host = Session(self.plan["HOST_LAN_IP"], self.plan.get("HOST_SSH_USER", "root"),
                                 port=int(self.plan.get("HOST_SSH_PORT", 22)), key_path=self.key_path).connect()
        return self._host

    def close(self) -> None:
        for s in (self._vps, self._host):
            if s:
                s.close()
        self._vps = self._host = None
