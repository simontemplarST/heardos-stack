"""Build the environment a step's subprocess/remote command receives: the non-secret plan plus only the secrets it asked for."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def plan_env(plan: Mapping[str, Any]) -> dict[str, str]:
    """Plan values as strings; lists become space-separated (PUBLIC_HOSTS style); None/False dropped."""
    out: dict[str, str] = {}
    for k, v in plan.items():
        if v is None or v is False:
            continue
        if isinstance(v, (list, tuple)):
            out[k] = " ".join(str(x) for x in v)
        elif isinstance(v, bool):
            out[k] = "1"
        else:
            out[k] = str(v)
    return out


def env_for(plan: Mapping[str, Any], vault, inputs: Iterable[str]) -> dict[str, str]:
    """Least privilege: a step only sees the vault keys listed in its Step.inputs."""
    env = plan_env(plan)
    if vault is not None and vault.unlocked:
        env.update(vault.as_env(inputs))
    return env
