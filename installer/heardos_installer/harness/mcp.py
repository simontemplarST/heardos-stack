"""heardos-mcp: the same tools as the harness, as a Model Context Protocol server on stdio, for agents that bring their
own loop (Claude Code, Open WebUI, Goose, LM Studio…).

  heardos-mcp --sim                         # simulated install
  heardos-mcp --install ID                  # read-only tools only
  heardos-mcp --install ID --allow-changes  # also install/configure/restart; remove_app only with --allow-remove

The vault passphrase comes from the system keyring or HEARDOS_VAULT_PASSPHRASE (stdin is the protocol channel).
Change tools are marked with MCP annotations so the client asks its user before each call. Tool results are scrubbed
(harness/redact.py: tokens, passwords in URLs, e-mail and public IP addresses, the install's own secrets) before they go
to the client, whose model may well be a third party's. The registry and variable limits of manage/tools.py apply.
"""
from __future__ import annotations

import argparse
import json
import sys

from .. import __version__
from ..manage import tools as T
from .redact import scrub

PROTOCOL = "2025-06-18"


def _tool_list(b, allow_changes: bool, allow_remove: bool) -> list[dict]:
    out = []
    for t in T.TOOLS:
        if (t.risk == "change" and not allow_changes) or (t.risk == "danger" and not allow_remove) or not t.usable(b):
            continue
        out.append({"name": t.name, "description": t.summary + ("" if t.risk == "read" else " Changes the install."),
                    "inputSchema": t.schema(b),
                    "annotations": {"readOnlyHint": t.risk == "read", "destructiveHint": t.risk == "danger", "idempotentHint": t.risk == "read",
                                    "openWorldHint": t.name == "search_images"}})
    return out


def serve(b, allow_changes: bool = False, allow_remove: bool = False, inp=None, outp=None) -> None:
    inp, outp = inp or sys.stdin, outp or sys.stdout

    def send(msg: dict) -> None:
        outp.write(json.dumps(msg) + "\n")
        outp.flush()

    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        mid, method, params = req.get("id"), req.get("method", ""), req.get("params") or {}
        if mid is None:            # notifications (initialized, cancelled): nothing to answer
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "heardos", "version": __version__},
                          "instructions": "Manage a HeardOS family cloud. Start with overview. Use exact app names from it."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tool_list(b, allow_changes, allow_remove)}
            elif method == "tools/call":
                name = params.get("name", "")
                if name not in {t["name"] for t in _tool_list(b, allow_changes, allow_remove)}:
                    result = {"content": [{"type": "text", "text": f"tool {name} is not available on this server"}], "isError": True}
                else:
                    from .schema import coerce
                    t = T.BY_NAME[name]
                    args = coerce(t.schema(b), params.get("arguments") or {})
                    why = T.needs_person(name, args)
                    if why:        # MCP clients may or may not ask their user: loosening a protection is never done from here
                        result = {"content": [{"type": "text", "text": f"Not done: {why}, so only a person may approve it. Change it in the "
                                               "HeardOS app (Stacks screen) or with `heardos-cli tool`, which asks."}], "isError": True}
                    else:
                        r = T.call(b, name, args)
                        result = {"content": [{"type": "text", "text": scrub(r.text, b.secret_values())}], "isError": not r.ok}
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        except Exception as e:  # noqa: BLE001
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:300]}})


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="heardos-mcp")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--install", metavar="ID")
    src.add_argument("--sim", action="store_true")
    ap.add_argument("--allow-changes", action="store_true")
    ap.add_argument("--allow-remove", action="store_true")
    a = ap.parse_args(argv)
    if a.sim:
        from ..manage.sim import SimBackend
        b = SimBackend()
    else:
        import os
        from ..manage.backend import RealBackend
        from ..session import Installation
        from ..vault import Vault, keyring_
        v = Vault()
        pw = os.environ.get("HEARDOS_VAULT_PASSPHRASE") or keyring_.recall()
        if not pw:
            sys.exit("heardos-mcp needs the vault passphrase in the keyring or HEARDOS_VAULT_PASSPHRASE")
        v.unlock(pw)
        b = RealBackend(Installation(a.install, v))
    serve(b, a.allow_changes, a.allow_remove)


if __name__ == "__main__":
    main()
