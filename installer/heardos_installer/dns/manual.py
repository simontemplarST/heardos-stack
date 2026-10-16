"""Manual DNS: we cannot write records, so we show exactly what to create and poll public resolvers until they exist."""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from .base import Record, wait_resolved


class ManualDNS:
    name = "manual"

    def __init__(self, checklist: Optional[Callable[[str, str, Callable[[], bool]], bool]] = None):
        self.checklist = checklist

    def validate(self) -> str:
        return "manual: records will be shown for you to create at your DNS host"

    @staticmethod
    def instructions(records: Iterable[Record]) -> str:
        rows = ["Create these records at your DNS host (TTL 300 or the lowest allowed), then wait a minute:", ""]
        for r in records:
            prio = f"  priority {r.priority}" if r.priority is not None else ""
            rows.append(f"  {r.type:5}  {r.name:40}  ->  {r.content}{prio}")
        return "\n".join(rows)

    def publish(self, records: Iterable[Record], on_line: Optional[Callable[[str], None]] = None) -> None:
        recs = list(records)
        text = self.instructions(recs)
        if on_line:
            on_line(text)
        if self.checklist:
            ok = self.checklist("Create DNS records", text, lambda: wait_resolved(recs, timeout=1, interval=1))
            if not ok:
                raise RuntimeError("DNS records were not created")
        if not wait_resolved(recs, timeout=1800, on_line=on_line):
            raise RuntimeError("DNS records did not become visible within 30 minutes")

    def delete(self, names: Iterable[str]) -> None:
        pass
