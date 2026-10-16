"""Outgoing mail: Resend domain + DNS (local, early so DNS propagates while the rest installs), then Authentik's SMTP settings."""
from __future__ import annotations

from ..dns.base import resolves
from ..engine.step import Ctx, Step, StepResult
from .common import dns_for, run_kit


def mail_domain(ctx: Ctx) -> StepResult:
    from ..mail import Resend, dmarc_record, records_for
    p = ctx.plan
    if p.get("mail_mode") != "resend":
        return StepResult(True, message="no sending domain needed")
    dom = p["DOMAIN"]
    rs = Resend(ctx.vault.get("RESEND_API_KEY") or "")
    if ctx.dry_run:
        return StepResult(True, message=f"[dry-run] would add {dom} to Resend and publish its DNS records")
    access = rs.access()
    if access == "sending":
        text = (f"Your Resend key can only send mail, so {dom} has to be added in Resend by hand:\n\n"
                f"  1. Open https://resend.com/domains and click Add Domain: {dom}\n"
                f"  2. Create the DNS records Resend lists (at Cloudflare or your DNS host)\n"
                f"  3. Click Verify in Resend and wait for 'Verified'\n\n"
                f"Tick done once Resend shows the domain as verified. (A full-access key lets the installer do all of this.)")
        if not ctx.checklist("Verify your email domain in Resend", text, lambda: False):
            return StepResult(False, message="the email domain was not verified")
        return StepResult(True, message=f"{dom} verified in Resend (by hand)")
    d = rs.ensure_domain(dom)
    recs = records_for(dom, d)
    if not resolves(dmarc_record(dom)) and not p.get("skip_dmarc"):
        recs.append(dmarc_record(dom, p.get("ADMIN_EMAIL", "")))
    ctx.log("records for Resend: " + ", ".join(f"{r.type} {r.name}" for r in recs))
    if d.get("status") != "verified":
        dns_for(ctx).publish(recs, on_line=ctx.log)
        rs.verify(d["id"])
        if not rs.wait_verified(d["id"], on_line=ctx.log, cancel=ctx.cancel):
            return StepResult(False, outputs={"RESEND_DOMAIN_ID": d["id"]}, message="Resend has not verified the domain yet; retry in a few minutes")
    return StepResult(True, outputs={"RESEND_DOMAIN_ID": d["id"]}, message=f"{dom} verified for sending")


def mail_configure(ctx: Ctx) -> StepResult:
    p = ctx.plan
    mode = p.get("mail_mode") or "none"
    if mode == "none":
        return StepResult(True, message="email skipped (passwords are reset by the admin)")
    env = {"MAIL_MODE": mode, "MAIL_FROM": p.get("MAIL_FROM", "")}
    if mode == "resend":
        env["RESEND_API_KEY"] = ctx.vault.get("RESEND_API_KEY") or ""
    else:
        env.update(SMTP_HOST=p.get("SMTP_HOST", ""), SMTP_PORT=str(p.get("SMTP_PORT", 587)), SMTP_USER=p.get("SMTP_USER", ""),
                   SMTP_PASSWORD=ctx.vault.get("SMTP_PASSWORD") or "")
    res = run_kit(ctx, "43-authentik-mail.sh", env=env, timeout=900)
    if res.ok:
        res.message = f"email works (a test message went to {p.get('ADMIN_EMAIL')})"
    return res


def steps() -> list[Step]:
    return [
        Step("mail.domain", "local", mail_domain, deps=["dns.validate"], title="Set up the email sending domain",
             inputs=["RESEND_API_KEY", "CF_API_TOKEN"], timeout=2400, optional=True),
        Step("mail.configure", "host", mail_configure, deps=["authentik.bootstrap", "mail.domain", "brand.apply"], title="Connect email to the sign-in service",
             inputs=["RESEND_API_KEY", "SMTP_PASSWORD"], serial_group="host-apps", timeout=900, optional=True),
    ]
