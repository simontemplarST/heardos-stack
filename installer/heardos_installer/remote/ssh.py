"""SSH to the VPS and the home host with paramiko: streaming output, env injection, password-then-key bootstrap.

Why paramiko: one implementation on Linux, macOS and Windows, no dependency on an OpenSSH client, agent or sshpass,
and we can stream stdout line by line into the step log while the command runs.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
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


class HostKeyError(SshError):
    """The server's host key changed, or the operator refused it. Never retried."""


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OUTPUT_PREFIX = "HEARDOS_OUTPUT "


def mask_output_line(line: str) -> str:
    """`HEARDOS_OUTPUT KEY=VALUE` lines carry tokens the vault has not seen yet: keep the key, hide the value."""
    if line.startswith(OUTPUT_PREFIX):
        return OUTPUT_PREFIX + line[len(OUTPUT_PREFIX):].split("=", 1)[0] + "=***"
    return line


def redacted_tail(text: str, hide: tuple[str, ...] = (), lines: int = 3, limit: int = 300) -> str:
    """Last few lines of command output for an error message, with HEARDOS_OUTPUT values and `hide` values masked."""
    tail = [mask_output_line(l) for l in (text or "").strip().splitlines()[-lines:]]
    out = " / ".join(tail)
    for v in sorted((v for v in hide if v and len(v) >= 6), key=len, reverse=True):
        out = out.replace(v, "***")
    return out[:limit]


def env_file(env: dict[str, str]) -> str:
    """A file `set -a; . FILE` turns back into environment variables (values single-quoted by shlex)."""
    for k in env:
        if not ENV_NAME.match(k):
            raise ValueError(f"bad environment variable name: {k!r}")
    return "".join(f"{k}={shlex.quote(str(v))}\n" for k, v in env.items())


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH-style SHA256 fingerprint, as `ssh-keygen -lf` prints it."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


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


def _kh_name(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


class _Pin(paramiko.MissingHostKeyPolicy):
    """Trust on first use, pinned in our own known_hosts; a changed key is refused. Used for VPSes the installer just
    created through a provider API; machines it did not create go through ensure_host_key() first."""

    def __init__(self, client: paramiko.SSHClient):
        self.client = client

    def missing_host_key(self, client, hostname, key):
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(known_hosts_file()))


def fetch_host_key(host: str, port: int = 22, timeout: int = 10) -> paramiko.PKey:
    """The server's host key, read from the key exchange alone (no authentication, nothing sent)."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        raise SshError(f"cannot reach {host}:{port}: {e}") from e
    t = paramiko.Transport(sock)
    try:
        t.start_client(timeout=timeout)
        return t.get_remote_server_key()
    except paramiko.SSHException as e:
        raise SshError(f"no SSH server answering on {host}:{port}: {e}") from e
    finally:
        t.close()


def pinned_host_key(host: str, port: int = 22) -> Optional[paramiko.PKey]:
    keys = paramiko.HostKeys(str(known_hosts_file())).lookup(_kh_name(host, port)) or {}
    return next(iter(keys.values()), None)


def ensure_host_key(host: str, port: int, confirm: Callable[[str, str], bool]) -> None:
    """Pin the host key of a machine the installer did not create, but only once the operator (or a fingerprint in the
    plan) has confirmed it: confirm(host, "SHA256:...") -> bool. Already pinned keys are left alone; a changed key
    then fails in Session.connect()."""
    if pinned_host_key(host, port) is not None:
        return
    key = fetch_host_key(host, port)
    if not confirm(host, fingerprint(key)):
        raise HostKeyError(f"host key of {host} ({fingerprint(key)}) was not confirmed; nothing was sent to it")
    kh = paramiko.HostKeys()
    path = known_hosts_file()
    kh.load(str(path))
    kh.add(_kh_name(host, port), key.get_name(), key)
    kh.save(str(path))


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
            raise HostKeyError(f"host key for {self.host} changed; remove it from {known_hosts_file()} if this is expected") from e
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
        """Run a shell command. env values never appear on a command line (visible in /proc/*/cmdline and sudo logs): they
        go into a 0600 file in a fresh private directory, which the command sources and deletes before doing anything else.
        Output lines are streamed to on_line with HEARDOS_OUTPUT values masked; Result keeps them for outputs()."""
        parts = []
        if cwd:
            parts.append(f"cd {shlex.quote(cwd)} &&")
        pre = ""
        if env:
            d = self.tempdir()
            f = f"{d}/env"
            self.put_text(env_file(env), f, 0o600)
            pre = f"set -a; . {shlex.quote(f)}; set +a; rm -rf {shlex.quote(d)}; "
        inner = f"bash -lc {shlex.quote(pre + cmd)}"
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
                            on_line(mask_output_line(line))
                if chan.recv_stderr_ready():
                    chunk = chan.recv_stderr(65536).decode("utf-8", "replace")
                    err.write(chunk)
                    if on_line:
                        for line in chunk.splitlines():
                            on_line(mask_output_line(line))
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                if time.time() > deadline:
                    chan.close()
                    raise SshError(f"command timed out after {timeout}s: {cmd[:80]}")
                time.sleep(0.05)
            if on_line and buf:
                on_line(mask_output_line(buf))
            rc = chan.recv_exit_status()
            chan.close()
        res = Result(rc, out.getvalue(), err.getvalue())
        if check and not res.ok:
            # never raw stderr: it lands in the state file and the UI (redacted tail, env values masked)
            raise SshError(f"command failed ({rc}): {cmd[:120]}\n{redacted_tail(res.stderr or res.stdout, tuple((env or {}).values()))}")
        return res

    def tempdir(self) -> str:
        """A fresh 0700 directory for staging files: under /root for root, else mktemp's default (owned by the login
        user, who must write into it over sftp). Replaces fixed /tmp names another local user could pre-create."""
        cmd = "mktemp -d /root/.heardos-XXXXXXXX" if self.user == "root" else "mktemp -d"
        d = self.run(cmd, timeout=30, check=True).stdout.strip()
        if not d.startswith("/"):
            raise SshError(f"mktemp gave an unexpected path: {d[:80]!r}")
        return d

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
        except HostKeyError:
            raise   # a changed key is an answer, not a boot delay: retrying for the whole timeout only hides it
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
            # after vps.ssh_lock, sshd only listens on the overlay address (plan NEBULA_LH_SSH)
            self._vps = Session(self.plan.get("NEBULA_LH_SSH") or self.plan["NEBULA_LH_IP"], "root", key_path=self.key_path).connect()
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
