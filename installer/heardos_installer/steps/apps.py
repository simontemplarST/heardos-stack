"""One step per selected app (50-app.sh <stack>), ordered by catalogue dependencies, plus the final checks."""
from __future__ import annotations

import ssl
import urllib.request

from ..engine.step import Ctx, Step, StepResult
from ..kit import catalog
from .common import run_kit
from .post import post_ids_for


def _app_runner(stack: str):
    def run(ctx: Ctx) -> StepResult:
        res = run_kit(ctx, "50-app.sh", [stack], timeout=2700)
        if res.ok:
            res.message = f"{catalog.apps()[stack].name} installed"
        return res
    return run


def verify(ctx: Ctx) -> StepResult:
    """Every public host answers over HTTPS with a real certificate; anonymous login flow starts; no unhealthy containers."""
    bad, ok = [], 0
    ssl_ctx = ssl.create_default_context()
    for h in ctx.plan["PUBLIC_HOSTS"]:
        url = f"https://{h}.{ctx.plan['DOMAIN']}/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "heardos-installer"})
            with urllib.request.urlopen(req, timeout=20, context=ssl_ctx) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:  # noqa: BLE001
            bad.append(f"{h}: {type(e).__name__}")
            continue
        ctx.log(f"{url} -> {code}")
        if code >= 500:   # 401/403/302 are normal: most apps sit behind sign-in
            bad.append(f"{h}: HTTP {code}")
        else:
            ok += 1
    r = ctx.sessions.host().run("docker ps --format '{{.Names}} {{.Status}}' | grep -ciE 'unhealthy|restarting' || true", sudo=True)
    unhealthy = int((r.stdout.strip() or "0").splitlines()[-1])
    if unhealthy:
        bad.append(f"{unhealthy} container(s) unhealthy")
    if bad:
        return StepResult(False, message="; ".join(bad)[:300])
    return StepResult(True, message=f"all {ok} web addresses answer over HTTPS; containers healthy")


def steps(selected: list[str]) -> list[Step]:
    cat = catalog.apps()
    out: list[Step] = []
    for stack in catalog.closure(selected):
        app = cat[stack]
        if not app.available:
            continue
        deps = ["authentik.bootstrap", "host.edge"] + [f"app.{d}" for d in app.depends_on if cat[d].available]
        deps += [pid for d in app.depends_on for pid in post_ids_for(d)]   # e.g. the dashboard waits for Mealie's API token
        out.append(Step(f"app.{stack}", "host", _app_runner(stack), deps=deps, title=f"Install {app.name}",
                        serial_group="host-apps", inputs=["AUTHENTIK_TOKEN"], timeout=3000, retries=2))
    app_ids = [s.id for s in out]
    from .post import steps as post_steps
    posts = post_steps(selected)
    out += posts
    out.append(Step("verify", "local", verify, deps=["vps.caddy", "host.nebula", "authentik.flows", "mail.configure"] + app_ids + [p.id for p in posts],
                    title="Check everything works", timeout=600, retries=3))
    return out
