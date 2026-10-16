"""brand.apply: build the family's brand bundle on this machine, upload it to the VPS (/srv/heardos-brand, served at
cloud.<domain>/brand/ for emails) and to Authentik (media/public/branding + templates/email), then set the brand via API."""
from __future__ import annotations

from pathlib import Path

from ..engine.state import config_dir
from ..engine.step import Ctx, Step, StepResult
from ..remote.push import put_dir
from .common import run_kit


def brand_apply(ctx: Ctx) -> StepResult:
    from ..brand import build
    p = ctx.plan
    out = config_dir() / "installs" / ctx.state.install_id / "brand"
    logo = Path(p["BRAND_LOGO"]).expanduser() if p.get("BRAND_LOGO") else None
    if logo and not logo.is_file():
        return StepResult(False, message=f"logo file not found: {logo}")
    pal = build(out, title=p.get("BRAND_TITLE") or "HeardOS", primary=p.get("BRAND_PRIMARY") or "#f2a35e", domain=p["DOMAIN"], logo=logo)
    ctx.log(f"brand bundle built: accent {pal.primary}, hub colour {pal.homepage_color}, {len(list((out / 'files').iterdir()))} files")
    if ctx.dry_run:
        return StepResult(True, message="[dry-run] brand built locally; upload skipped")
    vps = ctx.sessions.vps()
    put_dir(vps, out / "files", "/srv/heardos-brand", sudo=vps.user != "root", owner="root:root", clean=True)
    vps.run("chmod -R a+rX /srv/heardos-brand", sudo=vps.user != "root")
    host = ctx.sessions.host()
    root = p.get("HEARDOS_ROOT", "/opt/heardos")
    sudo = host.user != "root"
    put_dir(host, out / "files", f"{root}/data/authentik/media/public/branding", sudo=sudo, owner="1000:1000", clean=True)
    put_dir(host, out / "email", f"{root}/data/authentik/templates/email", sudo=sudo, owner="1000:1000", clean=True)
    res = run_kit(ctx, "44-authentik-brand.py", timeout=300)
    if res.ok:
        res.message = f"{p.get('BRAND_TITLE') or 'HeardOS'} look applied to sign-in, emails and the hub"
        res.outputs["BRAND_COLOR"] = pal.homepage_color
    return res


def steps() -> list[Step]:
    return [Step("brand.apply", "host", brand_apply, deps=["authentik.bootstrap", "vps.caddy"], title="Apply your family's look",
                 inputs=["AUTHENTIK_TOKEN"], timeout=600, optional=True)]
