"""Import an existing ~/.config/credentials.env (the kit's `set -a; . file` convention) into the vault."""
from __future__ import annotations

import re
from pathlib import Path

LINE = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def import_credentials_env(vault, path: Path | None = None, overwrite: bool = False) -> list[str]:
    """Returns the names imported. Empty values are skipped; existing vault entries kept unless overwrite."""
    path = path or (Path.home() / ".config" / "credentials.env")
    if not path.exists():
        return []
    have = set(vault.names())
    env = parse_env_file(path)
    if all(env.get(k) for k in ("OVH_APP_KEY", "OVH_APP_SECRET", "OVH_CONSUMER_KEY")):
        # the OVH provider takes its three keys as one JSON value
        import json
        env["OVH_CREDENTIALS"] = json.dumps({"endpoint": env.get("OVH_ENDPOINT", "us"), "app_key": env["OVH_APP_KEY"],
                                             "app_secret": env["OVH_APP_SECRET"], "consumer_key": env["OVH_CONSUMER_KEY"]})
    new = {k: v for k, v in env.items() if v and (overwrite or k not in have)}
    if new:
        vault.update(new)
    return sorted(new)
