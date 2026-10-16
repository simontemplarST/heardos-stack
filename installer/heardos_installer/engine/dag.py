"""Dependency ordering for steps (Kahn's algorithm) and validation of the graph."""
from __future__ import annotations

from collections import deque
from typing import Iterable

from .step import Step


def validate_dag(steps: Iterable[Step]) -> dict[str, Step]:
    by_id = {s.id: s for s in steps}
    for s in by_id.values():
        for d in s.deps:
            if d not in by_id:
                raise ValueError(f"step {s.id} depends on unknown step {d}")
    # cycle check via layering
    topo_layers(by_id.values())
    return by_id


def topo_layers(steps: Iterable[Step]) -> list[list[Step]]:
    """Return steps grouped into layers: everything in layer N depends only on layers < N."""
    by_id = {s.id: s for s in steps}
    indeg = {sid: 0 for sid in by_id}
    children: dict[str, list[str]] = {sid: [] for sid in by_id}
    for s in by_id.values():
        for d in s.deps:
            indeg[s.id] += 1
            children[d].append(s.id)
    layer = sorted(sid for sid, n in indeg.items() if n == 0)
    layers: list[list[Step]] = []
    seen = 0
    while layer:
        layers.append([by_id[sid] for sid in layer])
        seen += len(layer)
        nxt: list[str] = []
        for sid in layer:
            for c in children[sid]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    nxt.append(c)
        layer = sorted(nxt)
    if seen != len(by_id):
        stuck = [sid for sid, n in indeg.items() if n > 0]
        raise ValueError(f"dependency cycle among steps: {stuck}")
    return layers


def ancestors(steps: Iterable[Step], target: str) -> list[str]:
    by_id = {s.id: s for s in steps}
    out: list[str] = []
    q = deque(by_id[target].deps)
    while q:
        d = q.popleft()
        if d in out:
            continue
        out.append(d)
        q.extend(by_id[d].deps)
    return out
