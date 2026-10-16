"""Linode API v4 (Bearer token). Images are stable ids, plans are global, instances need a root password too."""
from __future__ import annotations

import secrets
import string
from typing import Callable, Optional

from .base import Account, HttpProvider, Plan, ProviderError, Region, VpsHandle, VpsSpec


class Linode(HttpProvider):
    name = "linode"
    base_url = "https://api.linode.com/v4"

    def __init__(self, token: str, client=None):
        super().__init__(token, client)
        self.root_pass: Optional[str] = None   # generated at create(); the caller stores it in the vault

    def _pages(self, path: str, key: str = "data"):
        page, out = 1, []
        while True:
            d = self._json("GET", path, params={"page": page, "page_size": 200})
            out.extend(d.get(key) or [])
            if page >= int(d.get("pages") or 1):
                return out
            page += 1

    def validate_token(self) -> Account:
        d = self._json("GET", "/profile")
        return Account(label=f"Linode: {d.get('email') or d.get('username')}", extra={"username": d.get("username")})

    def regions(self) -> list[Region]:
        return [Region(id=r["id"], name=r.get("label") or r["id"], country=(r.get("country") or "").upper(),
                       available=r.get("status", "ok") == "ok") for r in self._pages("/regions")]

    def plans(self, region_id: str) -> list[Plan]:
        out = []
        for t in self._pages("/linode/types"):
            if t.get("class") not in ("nanode", "standard"):
                continue
            price = t.get("price") or {}
            out.append(Plan(id=t["id"], name=t.get("label") or t["id"], vcpu=int(t.get("vcpus") or 0), ram_mb=int(t.get("memory") or 0),
                            disk_gb=int((t.get("disk") or 0) / 1024), monthly_usd=float(price.get("monthly") or 0),
                            hourly_usd=float(price.get("hourly") or 0)))
        return out

    def ubuntu_image(self) -> str:
        ids = {i["id"] for i in self._pages("/images") if i.get("is_public")}
        for cand in ("linode/ubuntu24.04", "linode/ubuntu22.04"):
            if cand in ids:
                return cand
        raise ProviderError("linode: no Ubuntu LTS image found")

    def ensure_ssh_key(self, name: str, pubkey: str) -> str:
        want = pubkey.split()[1] if " " in pubkey else pubkey
        for k in self._pages("/profile/sshkeys"):
            if want in (k.get("ssh_key") or ""):
                return str(k["id"])
        d = self._json("POST", "/profile/sshkeys", json={"label": name, "ssh_key": pubkey.strip()})
        return str(d["id"])

    def create(self, spec: VpsSpec) -> VpsHandle:
        alphabet = string.ascii_letters + string.digits + "!@#%^*-_"
        self.root_pass = "".join(secrets.choice(alphabet) for _ in range(32))
        key = self._json("GET", f"/profile/sshkeys/{spec.ssh_key_id}")["ssh_key"]
        d = self._json("POST", "/linode/instances", json={"type": spec.plan_id, "region": spec.region_id, "image": spec.image_id,
                                                          "root_pass": self.root_pass, "authorized_keys": [key], "label": spec.label,
                                                          "tags": spec.tags, "booted": True})
        return VpsHandle(self.name, str(d["id"]), extra={"label": d.get("label")})

    def wait_ip(self, handle: VpsHandle, timeout: int = 600, on_line: Optional[Callable[[str], None]] = None) -> str:
        def probe():
            d = self._json("GET", f"/linode/instances/{handle.id}")
            return (d.get("ipv4") or [None])[0] if d.get("status") == "running" else None
        handle.ip = self._poll(probe, timeout, 5, on_line, what="to boot")
        return handle.ip

    def destroy(self, handle: VpsHandle) -> None:
        self._json("DELETE", f"/linode/instances/{handle.id}")
