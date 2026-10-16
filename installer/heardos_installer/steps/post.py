"""First-run app setup that used to need a browser (63-post-install.py on the host). Two kinds:

  automatic  ha-onboard, mealie-token: pure API, the step just runs it
  guided     nextcloud-admin, baikal-user: need one human action first (sign in once / finish Baïkal's wizard); the step
             shows a checklist and re-runs the task every poll until it stops answering 'not yet' (exit 3)
"""
from __future__ import annotations

from ..engine.step import Ctx, Step, StepResult
from ..kit import catalog
from .common import run_kit

GUIDED = {
    "nextcloud-admin": ("Sign in to Documents once",
                        "Open https://docs.{DOMAIN} and sign in with your new account ({ADMIN_USER}).\n"
                        "Nextcloud creates your account on that first sign-in; the installer then makes you its administrator."),
    "baikal-user": ("Finish the calendar server setup",
                    "Open https://dav.{DOMAIN}/admin/install/ (sign in with your new account if asked).\n"
                    "  1. Time zone: yours; leave the rest; Save\n"
                    "  2. Database: keep SQLite; Save\n"
                    "Then come back here. The installer creates your calendar account and remembers its password."),
}
SECRET_OUTPUTS = {"HA_TOKEN", "HA_OWNER_PASSWORD", "MEALIE_TOKEN", "BAIKAL_ADMIN_PASSWORD"}


def _split_secrets(res: StepResult) -> StepResult:
    for k in list(res.outputs):
        if k in SECRET_OUTPUTS:
            res.secrets[k] = res.outputs.pop(k)
    return res


def _runner(task: str, label: str):
    def run(ctx: Ctx) -> StepResult:
        if task not in GUIDED or ctx.dry_run:
            res = run_kit(ctx, "63-post-install.py", [task], timeout=1200)
            return _split_secrets(res) if res.ok else res
        last: dict = {}

        def attempt() -> bool:
            r = run_kit(ctx, "63-post-install.py", [task], timeout=300)
            last["r"] = r
            return r.ok

        if attempt():
            res = last["r"]
        else:
            title, text = GUIDED[task]
            if not ctx.checklist(title, text.format(**ctx.plan), attempt):
                return StepResult(False, message=f"{label}: waiting for you ({title.lower()})")
            if not last["r"].ok and not attempt():
                return last["r"]
            res = last["r"]
        res.message = f"{label} done"
        return _split_secrets(res)
    return run


def steps(selected: list[str]) -> list[Step]:
    cat, out = catalog.apps(), []
    for stack in catalog.closure(selected):
        app = cat[stack]
        if not app.available:
            continue
        for task in app.post_install:
            out.append(Step(f"post.{task}", "host", _runner(task, f"{app.name}: {task.replace('-', ' ')}"),
                            deps=[f"app.{stack}"] + (["vps.caddy"] if task in GUIDED else []), title=f"Finish {app.name} setup", inputs=["AUTHENTIK_TOKEN"],
                            timeout=3600 if task in GUIDED else 1500, optional=task in GUIDED))
    return out


def post_ids_for(stack: str) -> list[str]:
    """Post steps an app depending on <stack> must wait for (the dashboard needs Mealie's token)."""
    app = catalog.apps().get(stack)
    return [f"post.{t}" for t in (app.post_install if app and app.available else []) if t not in GUIDED]
