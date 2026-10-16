"""Cloudzy (api.cloudzy.com/developers). Quirks learned from deploy-dco.sh: `API-Token` header, plain arrays for
regions/products but nested objects for os/ssh-keys/instances, POST ssh-keys returns no id, instance id location varies,
never call resize."""
from __future__ import annotations

import re
from typing import Callable, Optional

from .base import Account, HttpProvider, Plan, ProviderError, Region, VpsHandle, VpsSpec


class Cloudzy(HttpProvider):
    name = "cloudzy"
    base_url = "https://api.cloudzy.com/developers"

    def headers(self):
        return {"API-Token": self.token, "Accept": "application/json", "Content-Type": "application/json",
                "User-Agent": "heardos-installer/0.1"}

    def validate_token(self) -> Account:
        d = self._json("GET", "/v1/instances")
        n = len((d.get("data") or {}).get("instances") or [])
        return Account(label=f"Cloudzy account ({n} instance{'s' if n != 1 else ''})", extra={"instances": n})

    def regions(self) -> list[Region]:
        out = []
        for r in self._json("GET", "/v1/regions").get("data") or []:
            out.append(Region(id=str(r["id"]), name=r.get("name", str(r["id"])), country=(r.get("countryCode") or r.get("country") or "")[:2].upper(),
                              available=bool(r.get("isActive", True)) and not r.get("isOutOfStock", False)))
        return out

    def plans(self, region_id: str) -> list[Plan]:
        out = []
        for p in self._json("GET", "/v1/products", params={"regionId": region_id}).get("data") or []:
            if p.get("isActive") is False or p.get("isOutOfSales") or (p.get("plan") or {}).get("gpuName"):
                continue
            spec = (p.get("plan") or {}).get("specification") or {}
            price = (p.get("priceItems") or [{}])[0]
            hourly = float(price.get("hourlyPrice") or 0)
            monthly = float(price.get("monthlyPrice") or hourly * 730)
            vcpu, ram, disk = int(spec.get("cpu") or 0), int(spec.get("ramInMB") or 0), int(spec.get("storage") or 0)
            ram_label = f"{ram // 1024} GB" if ram >= 1024 else f"{ram} MB"
            out.append(Plan(id=str(p["id"]), name=f"{vcpu} vCPU · {ram_label} RAM · {disk} GB disk", vcpu=vcpu,
                            ram_mb=ram, disk_gb=disk, monthly_usd=round(monthly, 2), hourly_usd=hourly, region_ids=[region_id]))
        return out

    def ubuntu_image(self) -> str:
        best, best_ver = None, (0, 0)
        for o in ((self._json("GET", "/v1/os").get("data") or {}).get("os") or []):
            if not o.get("isActive", True) or "ubuntu" not in (o.get("family") or o.get("name") or "").lower():
                continue
            m = re.search(r"(\d+)\.(\d+)", o.get("name") or "")
            ver = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            if ver[0] % 2 == 0 and ver[1] == 4 and ver > best_ver:   # prefer LTS (xx.04 with even major)
                best, best_ver = o, ver
        if not best:
            raise ProviderError("cloudzy: no Ubuntu LTS image offered")
        return str(best["id"])

    def ensure_ssh_key(self, name: str, pubkey: str) -> str:
        want = pubkey.split()[1] if " " in pubkey else pubkey
        def find():
            for k in ((self._json("GET", "/v1/ssh-keys").get("data") or {}).get("sshKeys") or []):
                pk = k.get("public_key") or k.get("publicKey") or ""
                if want in pk:
                    return str(k["id"])
            return None
        kid = find()
        if kid:
            return kid
        self._json("POST", "/v1/ssh-keys", json={"name": name, "publicKey": pubkey.strip()})
        kid = find()
        if not kid:
            raise ProviderError("cloudzy: ssh key upload did not register")
        return kid

    def create(self, spec: VpsSpec) -> VpsHandle:
        body = {"hostnames": [spec.hostname], "region": spec.region_id, "productId": spec.plan_id, "osId": spec.image_id,
                "billingCycle": "hourly", "sshKeyIds": [spec.ssh_key_id], "assignIpv4": True}
        d = self._json("POST", "/v1/instances", json=body)
        data = d.get("data") or {}
        iid = None
        if isinstance(data.get("instances"), list) and data["instances"]:
            inst = data["instances"][0]
            iid = (inst.get("data") or {}).get("id") or inst.get("id")
        iid = iid or data.get("id")
        if not iid:
            raise ProviderError(f"cloudzy: create returned no instance id: {str(d)[:200]}")
        return VpsHandle(self.name, str(iid))

    def wait_ip(self, handle: VpsHandle, timeout: int = 600, on_line: Optional[Callable[[str], None]] = None) -> str:
        def probe():
            d = self._json("GET", f"/v1/instances/{handle.id}").get("data") or {}
            inst = d.get("instance") or d
            return inst.get("mainIp") or None
        handle.ip = self._poll(probe, timeout, 5, on_line)
        return handle.ip

    def destroy(self, handle: VpsHandle) -> None:
        self._json("DELETE", f"/v1/instances/{handle.id}")
