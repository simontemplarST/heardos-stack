"""Vultr API v2 (Bearer token). Plans carry the regions they are sold in; OS ids are numeric."""
from __future__ import annotations

from typing import Callable, Optional

from .base import Account, HttpProvider, Plan, ProviderError, Region, VpsHandle, VpsSpec


class Vultr(HttpProvider):
    name = "vultr"
    base_url = "https://api.vultr.com/v2"

    def __init__(self, token: str, client=None):
        super().__init__(token, client)
        self.root_pass: Optional[str] = None   # Vultr's generated root password; the caller stores it in the vault

    def _all(self, path: str, key: str):
        cursor, out = "", []
        while True:
            d = self._json("GET", path, params={"per_page": 500, **({"cursor": cursor} if cursor else {})})
            out.extend(d.get(key) or [])
            cursor = ((d.get("meta") or {}).get("links") or {}).get("next") or ""
            if not cursor:
                return out

    def validate_token(self) -> Account:
        d = self._json("GET", "/account").get("account") or {}
        return Account(label=f"Vultr: {d.get('email') or d.get('name')}", extra={"balance": d.get("balance")})

    def regions(self) -> list[Region]:
        return [Region(id=r["id"], name=f"{r.get('city', r['id'])}, {r.get('country', '')}".strip(", "), country=(r.get("country") or "").upper())
                for r in self._all("/regions", "regions")]

    def plans(self, region_id: str) -> list[Plan]:
        out = []
        for p in self._all("/plans", "plans"):
            if not p.get("id", "").startswith("vc2-") and p.get("type") not in ("vc2", "vhf", "vhp"):
                continue
            locs = p.get("locations") or []
            if locs and region_id not in locs:
                continue
            out.append(Plan(id=p["id"], name=p["id"], vcpu=int(p.get("vcpu_count") or 0), ram_mb=int(p.get("ram") or 0),
                            disk_gb=int(p.get("disk") or 0), monthly_usd=float(p.get("monthly_cost") or 0),
                            hourly_usd=float(p.get("hourly_cost") or 0), region_ids=locs))
        return out

    def ubuntu_image(self) -> str:
        best = None
        for o in self._all("/os", "os"):
            name = (o.get("name") or "")
            if name.startswith("Ubuntu") and "LTS" in name and "x64" in name:
                if best is None or name > best["name"]:
                    best = o
        if not best:
            raise ProviderError("vultr: no Ubuntu LTS x64 image found")
        return str(best["id"])

    def ensure_ssh_key(self, name: str, pubkey: str) -> str:
        want = pubkey.split()[1] if " " in pubkey else pubkey
        for k in self._all("/ssh-keys", "ssh_keys"):
            if want in (k.get("ssh_key") or ""):
                return k["id"]
        d = self._json("POST", "/ssh-keys", json={"name": name, "ssh_key": pubkey.strip()})
        return d["ssh_key"]["id"]

    def create(self, spec: VpsSpec) -> VpsHandle:
        d = self._json("POST", "/instances", json={"region": spec.region_id, "plan": spec.plan_id, "os_id": int(spec.image_id),
                                                   "sshkey_id": [spec.ssh_key_id], "label": spec.label, "hostname": spec.hostname,
                                                   "tags": spec.tags, "enable_ipv6": False, "backups": "disabled"})
        inst = d.get("instance") or {}
        # never in the handle: handles are written to the plain state file
        self.root_pass = inst.get("default_password") or None
        return VpsHandle(self.name, inst["id"], extra={"label": inst.get("label")})

    def wait_ip(self, handle: VpsHandle, timeout: int = 600, on_line: Optional[Callable[[str], None]] = None) -> str:
        def probe():
            i = self._json("GET", f"/instances/{handle.id}").get("instance") or {}
            ip = i.get("main_ip")
            return ip if i.get("status") == "active" and ip and ip != "0.0.0.0" else None
        handle.ip = self._poll(probe, timeout, 5, on_line, what="to boot")
        return handle.ip

    def destroy(self, handle: VpsHandle) -> None:
        self._json("DELETE", f"/instances/{handle.id}")
