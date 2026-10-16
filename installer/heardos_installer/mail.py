"""Resend: tell a full-access key from a sending-only key, add the domain, fetch its DNS records, trigger and poll verify.

A sending-only key cannot read domains (Resend answers 401 restricted_api_key); then the domain has to be added in the
Resend dashboard by hand and the installer shows a checklist instead.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import httpx

from .dns.base import Record

API = "https://api.resend.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) heardos-installer"   # Resend sits behind Cloudflare, which 403s library user agents


class ResendError(RuntimeError):
    pass


class Resend:
    def __init__(self, key: str, client: Optional[httpx.Client] = None):
        self.c = client or httpx.Client(base_url=API, timeout=30, headers={"Authorization": f"Bearer {key}", "User-Agent": UA})

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in range(4):
            try:
                r = self.c.request(method, path, **kw)
            except httpx.TransportError as e:
                if attempt == 3:
                    raise ResendError(f"cannot reach Resend: {e}") from e
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code in (429, 502, 503, 504) and attempt < 3:
                time.sleep(2 * (attempt + 1)); continue
            return r
        raise ResendError("Resend kept failing")

    def access(self) -> str:
        """'full', 'sending' or raises for a bad key."""
        r = self._req("GET", "/domains")
        if r.status_code == 200:
            return "full"
        body = r.text.lower()
        if r.status_code == 401 and "restricted" in body:
            return "sending"
        raise ResendError(f"Resend rejected the key ({r.status_code}): {r.text[:160]}")

    def find_domain(self, name: str) -> Optional[dict]:
        r = self._req("GET", "/domains")
        r.raise_for_status()
        return next((d for d in r.json().get("data", []) if d.get("name") == name), None)

    def ensure_domain(self, name: str, region: str = "us-east-1") -> dict:
        d = self.find_domain(name)
        if d:
            return self.domain(d["id"])
        r = self._req("POST", "/domains", json={"name": name, "region": region})
        if r.status_code >= 300:
            raise ResendError(f"could not add {name} to Resend: {r.text[:200]}")
        return self.domain(r.json()["id"])

    def domain(self, domain_id: str) -> dict:
        r = self._req("GET", f"/domains/{domain_id}")
        r.raise_for_status()
        return r.json()

    def verify(self, domain_id: str) -> None:
        self._req("POST", f"/domains/{domain_id}/verify")

    def wait_verified(self, domain_id: str, timeout: int = 1800, interval: int = 30, on_line: Optional[Callable[[str], None]] = None,
                      cancel=None) -> bool:
        t0, last = time.time(), None
        while time.time() - t0 < timeout:
            st = self.domain(domain_id).get("status")
            if st != last and on_line:
                on_line(f"Resend domain status: {st}")
            last = st
            if st == "verified":
                return True
            if st == "failed":
                self.verify(domain_id)
            if cancel is not None and cancel.wait(interval):
                return False
            if cancel is None:
                time.sleep(interval)
        return False


def records_for(domain: str, resend_domain: dict) -> list[Record]:
    out = []
    for r in resend_domain.get("records", []):
        name = r["name"] if r["name"].endswith(domain) else f"{r['name']}.{domain}"
        prio = int(r["priority"]) if r.get("type") == "MX" and r.get("priority") is not None else None
        out.append(Record(r["type"], name, r["value"], prio))
    return out


def dmarc_record(domain: str, rua: str = "") -> Record:
    """A cautious starting DMARC policy: quarantine, relaxed alignment, reports to the admin when given."""
    v = "v=DMARC1; p=quarantine; adkim=r; aspf=r"
    return Record("TXT", f"_dmarc.{domain}", v + (f"; rua=mailto:{rua}" if rua else ""))
