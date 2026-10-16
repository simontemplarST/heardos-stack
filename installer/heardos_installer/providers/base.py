"""The provider seam: every cloud is reduced to the same eight operations the lighthouse needs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

import httpx


class ProviderError(RuntimeError):
    pass


@dataclass
class Account:
    label: str                      # e.g. email or account name, shown on the Provider screen
    extra: dict = field(default_factory=dict)


@dataclass
class Region:
    id: str
    name: str
    country: str = ""               # ISO-3166 alpha-2 when the API gives one, else ""
    available: bool = True


@dataclass
class Plan:
    id: str
    name: str
    vcpu: int
    ram_mb: int
    disk_gb: int
    monthly_usd: float
    hourly_usd: float = 0.0
    region_ids: list[str] = field(default_factory=list)   # empty = all regions

    @property
    def eligible(self) -> bool:
        return self.vcpu >= 1 and self.ram_mb >= 1024


@dataclass
class VpsSpec:
    hostname: str
    region_id: str
    plan_id: str
    image_id: str
    ssh_key_id: str
    label: str = "heardos-lighthouse"
    tags: list[str] = field(default_factory=lambda: ["heardos"])


@dataclass
class VpsHandle:
    provider: str
    id: str
    ip: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"provider": self.provider, "id": self.id, "ip": self.ip, "extra": self.extra}

    @staticmethod
    def from_json(d: dict) -> "VpsHandle":
        return VpsHandle(d["provider"], d["id"], d.get("ip"), d.get("extra", {}))


class Provider(Protocol):
    name: str

    def validate_token(self) -> Account: ...
    def regions(self) -> list[Region]: ...
    def plans(self, region_id: str) -> list[Plan]: ...
    def ubuntu_image(self) -> str: ...
    def ensure_ssh_key(self, name: str, pubkey: str) -> str: ...
    def create(self, spec: VpsSpec) -> VpsHandle: ...
    def wait_ip(self, handle: VpsHandle, timeout: int = 600, on_line: Optional[Callable[[str], None]] = None) -> str: ...
    def destroy(self, handle: VpsHandle) -> None: ...


class HttpProvider:
    """Shared httpx plumbing: base URL, auth headers, JSON helpers with readable errors."""

    name = "http"
    base_url = ""
    timeout = 30.0

    def __init__(self, token: str, client: Optional[httpx.Client] = None):
        self.token = token
        self._client = client

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json", "User-Agent": "heardos-installer/0.1"}

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, headers=self.headers(), timeout=self.timeout)
        return self._client

    retries = 4   # transient 5xx / network errors are retried with backoff (cloud APIs flap; Cloudzy's edge 502s)

    def _json(self, method: str, path: str, **kw):
        r = None
        for attempt in range(self.retries):
            try:
                r = self.client.request(method, path, **kw)
            except httpx.HTTPError as e:
                if attempt == self.retries - 1:
                    raise ProviderError(f"{self.name}: network error on {method} {path}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            # retry server errors, but never re-POST a create that may have succeeded server-side
            if r.status_code >= 500 and attempt < self.retries - 1 and not (method == "POST" and "instance" in path):
                time.sleep(2 ** attempt)
                continue
            break
        if r.status_code == 401 or r.status_code == 403:
            raise ProviderError(f"{self.name}: the API token was rejected ({r.status_code})")
        if r.status_code >= 400:
            raise ProviderError(f"{self.name}: {method} {path} -> {r.status_code}: {r.text[:300]}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            raise ProviderError(f"{self.name}: non-JSON response from {path}: {r.text[:200]}")

    @staticmethod
    def _poll(fn: Callable[[], Optional[str]], timeout: int, interval: int = 5, on_line=None, what: str = "ip") -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            v = fn()
            if v:
                return v
            if on_line:
                on_line(f"waiting for the VPS {what}…")
            time.sleep(interval)
        raise ProviderError(f"timed out after {timeout}s waiting for the VPS {what}")
