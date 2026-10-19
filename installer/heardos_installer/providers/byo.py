"""Bring your own VPS: the user already has an Ubuntu 24.04 box with our key on root; we only need its IP."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .base import Account, Plan, ProviderError, Region, VpsHandle, VpsSpec


class BringYourOwn:
    name = "byo"

    def __init__(self, ip: str = "", key_path: Optional[Path] = None, confirm: Optional[Callable[[str, str], bool]] = None, **_):
        self.ip = ip
        self.key_path = key_path
        self.confirm = confirm   # host-key fingerprint confirmation: we did not create this server, so no blind trust

    def validate_token(self) -> Account:
        from ..remote.ssh import Session, SshError, ensure_host_key
        if not self.ip:
            raise ProviderError("enter the VPS address first")
        try:
            if self.confirm:
                ensure_host_key(self.ip, 22, self.confirm)
            s = Session(self.ip, "root", key_path=self.key_path, connect_timeout=10).connect()
            r = s.run("lsb_release -ds 2>/dev/null || cat /etc/os-release | head -1", timeout=20)
            s.close()
        except SshError as e:
            raise ProviderError(str(e)) from e
        return Account(label=f"{self.ip}: {r.stdout.strip() or 'reachable'}")

    def regions(self) -> list[Region]:
        return [Region(id="byo", name="Your own server")]

    def plans(self, region_id: str) -> list[Plan]:
        return [Plan(id="byo", name="existing", vcpu=1, ram_mb=1024, disk_gb=0, monthly_usd=0.0)]

    def ubuntu_image(self) -> str:
        return "byo"

    def ensure_ssh_key(self, name: str, pubkey: str) -> str:
        return "byo"

    def create(self, spec: VpsSpec) -> VpsHandle:
        return VpsHandle(self.name, self.ip, ip=self.ip)

    def wait_ip(self, handle: VpsHandle, timeout: int = 600, on_line: Optional[Callable[[str], None]] = None) -> str:
        return handle.ip or self.ip

    def destroy(self, handle: VpsHandle) -> None:
        pass  # not ours to delete
