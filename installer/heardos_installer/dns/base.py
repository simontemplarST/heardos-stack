"""DNS records the stack needs and a resolver check that works from any network (public resolvers, not the LAN's)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Protocol

PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]


@dataclass
class Record:
    type: str          # A | TXT | MX | CNAME
    name: str          # fully qualified, no trailing dot
    content: str
    priority: Optional[int] = None
    ttl: int = 300

    def to_json(self) -> dict:
        return {"type": self.type, "name": self.name, "content": self.content, "priority": self.priority, "ttl": self.ttl}


class DNSProvider(Protocol):
    name: str

    def validate(self) -> str: ...
    def publish(self, records: Iterable[Record], on_line: Optional[Callable[[str], None]] = None) -> None: ...
    def delete(self, names: Iterable[str]) -> None: ...


def public_hosts_records(domain: str, hosts: Iterable[str], ip: str) -> list[Record]:
    return [Record("A", f"{h}.{domain}", ip) for h in hosts]


def _resolver():
    import dns.resolver
    r = dns.resolver.Resolver(configure=False)
    r.nameservers = PUBLIC_RESOLVERS
    r.lifetime = 5
    return r


def resolves(rec: Record) -> bool:
    import dns.resolver
    try:
        ans = _resolver().resolve(rec.name, rec.type)
    except Exception:  # NXDOMAIN, timeout, NoAnswer
        return False
    for rr in ans:
        if rec.type == "A" and rr.address == rec.content:
            return True
        if rec.type == "TXT" and rec.content in b"".join(rr.strings).decode(errors="replace"):
            return True
        if rec.type == "MX" and str(rr.exchange).rstrip(".") == rec.content.rstrip("."):
            return True
        if rec.type == "CNAME" and str(rr.target).rstrip(".") == rec.content.rstrip("."):
            return True
    return False


def wait_resolved(records: Iterable[Record], timeout: int = 900, interval: int = 15, on_line: Optional[Callable[[str], None]] = None,
                  cancel: Optional[Callable[[], bool]] = None) -> bool:
    pending = list(records)
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        pending = [r for r in pending if not resolves(r)]
        if not pending:
            return True
        if on_line:
            on_line(f"waiting for DNS: {len(pending)} record(s) not yet visible on public resolvers: " + ", ".join(r.name for r in pending[:4]))
        if cancel and cancel():
            return False
        time.sleep(interval)
    return not pending
