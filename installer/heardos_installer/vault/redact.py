"""Convenience: a redactor bound to a vault, for text that does not go through logging (UI panes, CLI output)."""
from __future__ import annotations

import re


class Redactor:
    def __init__(self, vault):
        self.vault = vault

    def __call__(self, text: str) -> str:
        vals = sorted({v for v in self.vault.values() if v and len(v) >= 8}, key=len, reverse=True)
        if not vals:
            return text
        return re.compile("|".join(re.escape(v) for v in vals)).sub("***", text)
