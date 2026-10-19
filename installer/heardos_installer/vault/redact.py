"""Convenience: a redactor bound to a vault, for text that does not go through logging (UI panes, CLI output, state file)."""
from __future__ import annotations

import json
import re
from typing import Iterable

OUTPUT_KV = re.compile(r"(HEARDOS_OUTPUT [A-Za-z0-9_]+=)\S+")


def secret_values(values: Iterable[str]) -> list[str]:
    """Every value worth masking (8+ chars), longest first. A JSON object value (OVH_CREDENTIALS) also contributes its
    string fields, so app_secret or consumer_key printed on their own are masked too."""
    out: set[str] = set()
    for v in values:
        if not v:
            continue
        out.add(v)
        if v.lstrip().startswith("{"):
            try:
                d = json.loads(v)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.update(x for x in d.values() if isinstance(x, str))
    return sorted((v for v in out if len(v) >= 8), key=len, reverse=True)


def redact_text(text: str, values: Iterable[str]) -> str:
    """Mask the given secret values and any `HEARDOS_OUTPUT KEY=VALUE` value (tokens the vault has not stored yet)."""
    vals = secret_values(values)
    if vals:
        text = re.compile("|".join(re.escape(v) for v in vals)).sub("***", text)
    return OUTPUT_KV.sub(r"\1***", text)


class Redactor:
    def __init__(self, vault):
        self.vault = vault

    def __call__(self, text: str) -> str:
        return redact_text(text, self.vault.values())
