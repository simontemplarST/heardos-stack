"""One step per custom stack ("your own app", plan["custom_stacks"]): upload the rendered compose file, its custom.json and
any settings override, then 51-custom-stack.sh registers the sign-in gate, makes data folders and secrets, and deploys.
Settings overrides for catalogue apps are pushed the same way (push_stack_files) before a redeploy."""
from __future__ import annotations

import json
import shlex
from typing import Optional

from ..engine.step import Ctx, Step, StepResult
from ..manage.spec import CustomStack, render_compose, render_override
from .common import run_kit


def stack_dir(plan: dict, name: str) -> str:
    return f"{plan.get('HEARDOS_ROOT', '/opt/heardos')}/stacks/{name}"


def push_stack_files(session, plan: dict, name: str, files: dict[str, Optional[str]]) -> None:
    """Write files into the stack folder on the host as root (None = delete that file)."""
    d = stack_dir(plan, name)
    tmp = session.tempdir()
    cmds = [f"install -d -m 755 {shlex.quote(d)}"]
    for fname, text in files.items():
        if text is None:
            cmds.append(f"rm -f {shlex.quote(d + '/' + fname)}")
        else:
            session.put_text(text, f"{tmp}/{fname}", 0o644)
            cmds.append(f"install -m 644 {shlex.quote(tmp + '/' + fname)} {shlex.quote(d + '/' + fname)}")
    cmds.append(f"rm -rf {shlex.quote(tmp)}")
    session.run(" && ".join(cmds), sudo=True, check=True, timeout=60)


def custom_files(plan: dict, name: str) -> dict[str, Optional[str]]:
    spec = CustomStack.from_dict(plan["custom_stacks"][name])
    meta = {"title": spec.title, "host": spec.host, "access": spec.access, "secrets": spec.secrets, "dirs": spec.dirs()}
    return {"compose.yml": render_compose(spec), "custom.json": json.dumps(meta, indent=1),
            "compose.override.yml": render_override((plan.get("stack_settings") or {}).get(name) or {})}


def _runner(name: str):
    def run(ctx: Ctx) -> StepResult:
        if name not in (ctx.plan.get("custom_stacks") or {}):
            return StepResult(True, message=f"{name} is no longer in the plan; nothing to do")
        push_stack_files(ctx.sessions.host(), ctx.plan, name, custom_files(ctx.plan, name))
        res = run_kit(ctx, "51-custom-stack.sh", [name], timeout=1800)
        if res.ok:
            res.message = f"{ctx.plan['custom_stacks'][name].get('title') or name} running"
        return res
    return run


def steps(plan: dict) -> list[Step]:
    return [Step(f"custom.{n}", "host", _runner(n), deps=["authentik.bootstrap", "host.edge"], title=f"Install {spec.get('title') or n}",
                 serial_group="host-apps", inputs=["AUTHENTIK_TOKEN"], timeout=2400, retries=2)
            for n, spec in sorted((plan.get("custom_stacks") or {}).items())]
