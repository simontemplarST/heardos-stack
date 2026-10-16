"""Cloudflare DNS with a zone-scoped token. One small client here; the kit's 32-dns.py stays for CLI use."""
from __future__ import annotations

from typing import Callable, Iterable, Optional

import httpx

from .base import Record


class CloudflareDNS:
    name = "cloudflare"
    base = "https://api.cloudflare.com/client/v4"

    def __init__(self, token: str, zone_id: str = "", domain: str = "", client: Optional[httpx.Client] = None):
        self.token, self.zone_id, self.domain = token, zone_id, domain
        self._client = client or httpx.Client(base_url=self.base, timeout=30,
                                              headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _req(self, method: str, path: str, **kw):
        r = self._client.request(method, path, **kw)
        d = r.json() if r.content else {}
        if r.status_code >= 400 or not d.get("success", True):
            errs = "; ".join(e.get("message", "") for e in d.get("errors", [])) or r.text[:200]
            raise RuntimeError(f"cloudflare {method} {path}: {errs}")
        return d

    def validate(self) -> str:
        """Checks the token and resolves the zone id from the domain when it is not given."""
        self._req("GET", "/user/tokens/verify")
        if not self.zone_id:
            if not self.domain:
                raise RuntimeError("domain needed to look up the Cloudflare zone")
            zones = self._req("GET", "/zones", params={"name": self.domain}).get("result") or []
            if not zones:
                raise RuntimeError(f"the token cannot see a zone named {self.domain}; it must be scoped to that zone with DNS:Edit")
            self.zone_id = zones[0]["id"]
        z = self._req("GET", f"/zones/{self.zone_id}").get("result") or {}
        return f"zone {z.get('name', self.zone_id)} ({z.get('status', '?')})"

    def _existing(self, name: str, rtype: str) -> list[dict]:
        return self._req("GET", f"/zones/{self.zone_id}/dns_records", params={"name": name, "type": rtype}).get("result") or []

    def publish(self, records: Iterable[Record], on_line: Optional[Callable[[str], None]] = None) -> None:
        for rec in records:
            body = {"type": rec.type, "name": rec.name, "content": rec.content, "ttl": rec.ttl, "proxied": False, "comment": "heardos"}
            if rec.priority is not None:
                body["priority"] = rec.priority
            ex = self._existing(rec.name, rec.type)
            same = [x for x in ex if x["content"].strip('"') == rec.content and (rec.type != "MX" or x.get("priority") == rec.priority)]
            if same:
                if on_line: on_line(f"dns: {rec.type} {rec.name} already {rec.content}")
                continue
            if ex and rec.type in ("A", "CNAME", "MX"):
                self._req("PUT", f"/zones/{self.zone_id}/dns_records/{ex[0]['id']}", json=body)
                if on_line: on_line(f"dns: updated {rec.type} {rec.name} -> {rec.content}")
            else:
                self._req("POST", f"/zones/{self.zone_id}/dns_records", json=body)
                if on_line: on_line(f"dns: created {rec.type} {rec.name} -> {rec.content}")

    def delete(self, names: Iterable[str]) -> None:
        for n in names:
            for x in self._req("GET", f"/zones/{self.zone_id}/dns_records", params={"name": n}).get("result") or []:
                self._req("DELETE", f"/zones/{self.zone_id}/dns_records/{x['id']}")
