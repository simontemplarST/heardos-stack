#!/usr/bin/env python3
"""compose-merge.py BASE OVERRIDE... — merge compose files the way `docker compose -f a -f b` does, without interpolating.

deploy_stack hands Portainer one file; `docker compose config --no-interpolate` is not usable for that: it rewrites
`${HEARDOS_ROOT}/data:/x` into long syntax and, since the uninterpolated source does not start with `/`, calls it a
named volume ("refers to undefined volume"). This keeps every string as written (`${VAR}` references included) and only
applies compose's merge rules: mappings merge recursively, `command`/`entrypoint`/`healthcheck.test` are replaced,
other lists (ports, volumes, env list, networks list, cap_add, ...) are appended without duplicates; a mapping-vs-list
clash (networks as list in one file, mapping in the other) becomes a mapping. Prints YAML to stdout. stdlib + PyYAML.
"""
import sys
import yaml

REPLACE = {"command", "entrypoint", "test"}


def merge(a, b, key=None):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v, k) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        if key in REPLACE:
            return list(b)
        out = list(a)
        for x in b:
            if x not in out:
                out.append(x)
        return out
    if isinstance(a, list) and isinstance(b, dict):      # networks: [proxy] + networks: {tools: {}}
        return merge({x: {} for x in a}, b, key)
    if isinstance(a, dict) and isinstance(b, list):
        return merge(a, {x: {} for x in b}, key)
    return b


doc = {}
for f in sys.argv[1:]:
    with open(f) as fh:
        doc = merge(doc, yaml.safe_load(fh) or {})
yaml.safe_dump(doc, sys.stdout, sort_keys=False, width=200, default_flow_style=False)
