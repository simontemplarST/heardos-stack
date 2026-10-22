"""Entry points: `heardos` opens the desktop app; `heardos-cli` runs the same engine headless.

  heardos-cli init                         create the encrypted vault (asks for a passphrase)
  heardos-cli secret NAME [--value-stdin]  store a secret (hidden prompt, or read from stdin); `secret --import` reads credentials.env
  heardos-cli install --plan plan.json [--id ID] [--dry-run] [--yes]
  heardos-cli status [ID]                  step table for an install
  heardos-cli retry ID STEP                forget a step's result and run the install again from there
  heardos-cli destroy ID                   delete the VPS this install created (billing stops)
  heardos-cli ovh-confirm SERVICE TOKEN    finish an OVH termination with the token from OVH's email
  heardos-cli list                         installs on this machine
  heardos-cli adopt SPEC.json [--vault F] [--secrets F] [--apply]   take over a HeardOS set up without this installer (adopt.py)
  heardos-cli migrate ID STACK [--apply]   move an adopted stack into the kit layout (migrate.py)
  heardos-cli tools [ID|sim] [--json]      the stack-management tools (same as the GUI, the harness and heardos-mcp)
  heardos-cli tool ID|sim NAME [--args JSON] [--yes]   run one of them; changes ask first unless --yes ("sim": a
                                           simulated install, nothing real is touched)
Model harness: heardos-agent (see heardos_installer/harness/cli.py); MCP server: heardos-mcp; chat front ends
(Open WebUI and the like, OpenAI/Ollama protocols): heardos-serve.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path


def _vault(create_ok: bool = False):
    from .vault import Vault, WrongPassphrase
    from .vault import keyring_
    v = Vault()
    if not v.exists():
        if not create_ok:
            sys.exit("no vault yet: run `heardos-cli init` first")
        return v
    pw = os.environ.get("HEARDOS_VAULT_PASSPHRASE") or keyring_.recall()
    for _ in range(3):
        if pw is None:
            pw = getpass.getpass("vault passphrase: ")
        try:
            v.unlock(pw)
            return v
        except WrongPassphrase:
            print("wrong passphrase")
            pw = None
    sys.exit("could not unlock the vault")


def _prompt(title: str, instructions: str, poll) -> bool:
    """Checklist hook for the CLI. On a terminal the operator can answer (Enter = done/matches, "no" = cancel) while
    poll() keeps checking; without a terminal it only polls, and a question only a person can answer is refused."""
    import threading
    manual = bool(getattr(poll, "manual_only", False))
    print(f"\n=== {title} ===\n{instructions}")
    if not sys.stdin.isatty():
        if manual:
            print("no terminal to confirm on: refusing")
            return False
        print("(checking every 15 s; press Ctrl+C to abort)")
        while True:
            if poll():
                print("done.")
                return True
            time.sleep(15)
    answer: list[str] = []
    ev = threading.Event()

    def ask():
        try:
            answer.append(input("type yes if it matches, anything else cancels: " if manual else
                                "press Enter when done (type no to cancel); checking every 15 s meanwhile: ").strip().lower())
        except EOFError:
            answer.append("no")
        ev.set()
    threading.Thread(target=ask, daemon=True).start()
    while True:
        if not manual and poll():
            print("done.")
            return True
        if ev.wait(15):
            ok = answer[0] in ("y", "yes") if manual else answer[0] not in ("n", "no")
            print("confirmed." if ok else "cancelled.")
            return ok


def cmd_init(a) -> None:
    from .vault import Vault
    from .vault import keyring_
    v = Vault()
    if v.exists():
        sys.exit(f"vault already exists at {v.path}")
    pw = getpass.getpass("new vault passphrase (12+ characters): ")
    if len(pw) < 12 or pw != getpass.getpass("again: "):
        sys.exit("passphrases must match and be at least 12 characters")
    v.create(pw)
    if keyring_.available() and input("remember it in the system keyring? [y/N] ").lower().startswith("y"):
        keyring_.remember(pw)
    print(f"vault created: {v.path}")


def cmd_secret(a) -> None:
    v = _vault()
    if a.name == "--import":
        from .vault.migrate import import_credentials_env
        names = import_credentials_env(v, Path(a.value) if a.value else None)
        print(f"imported {len(names)} value(s): {', '.join(names) or 'none'}")
        return
    if a.value is not None:
        # a value on the command line ends up in shell history and the process list: refuse it
        sys.exit("pass the value on stdin (--value-stdin) or at the hidden prompt, not as an argument")
    if a.value_stdin:
        val = sys.stdin.read().rstrip("\r\n")
    else:
        val = getpass.getpass(f"{a.name}: ")
    if not val:
        sys.exit("empty value; nothing stored")
    v.set(a.name, val)
    print(f"stored {a.name}")


def _print_plan_summary(inst) -> None:
    p = inst.plan
    from .kit import catalog
    print(f"install {inst.install_id}: {p['DOMAIN']} · home server {p['HOST_LAN_IP']} · provider {p['provider']}")
    print("apps: " + ", ".join(catalog.apps()[s].name for s in p["apps"]))
    print("public addresses: " + ", ".join(f"{h}.{p['DOMAIN']}" for h in p["PUBLIC_HOSTS"]))


def _cost_line(inst) -> str:
    from .providers import smart_default
    from .steps.common import provider_for
    from .engine.step import Ctx
    p = inst.plan
    if p["provider"] == "byo":
        return "using your own VPS: no new charges"
    prov = provider_for(Ctx(p, inst.vault, None, inst.logs.logger("cost"), inst.state, inst.kit_root, inst.workdir))
    if not (p.get("vps_region") and p.get("vps_plan")):
        ch = smart_default(prov.regions(), prov.plans, tz=p.get("TZ"))
        if not ch:
            sys.exit("no eligible plan available at this provider right now")
        p["vps_region"], p["vps_plan"], p["vps_monthly"] = ch.region.id, ch.plan.id, ch.plan.monthly_usd
        inst.state.save()
        return f"{p['provider']}: {ch.region.name}, {ch.plan.name}, about ${ch.plan.monthly_usd:.2f}/month (billed {'monthly' if p['provider'] == 'ovh' else 'hourly'})"
    return f"{p['provider']}: region {p['vps_region']}, plan {p['vps_plan']}" + (f", about ${p['vps_monthly']}/month" if p.get("vps_monthly") else "")


def _status_printer(sid, status, msg):
    mark = {"done": "✓", "failed": "✗", "running": "…", "skipped": "-"}.get(status.value, " ")
    print(f"  {mark} {sid:24} {msg}", flush=True)


def cmd_install(a) -> None:
    from .session import Installation, new_install_id
    from .steps import missing_inputs
    v = _vault()
    plan = json.loads(Path(a.plan).read_text()) if a.plan else {}
    iid = a.id or plan.get("install_id") or new_install_id(plan.get("DOMAIN", "install"))
    plan["install_id"] = iid
    inst = Installation(iid, v, plan, prompt=_prompt)
    need = missing_inputs(inst.plan, v)
    if need:
        sys.exit("still needed: " + "; ".join(need))
    _print_plan_summary(inst)
    print("cost: " + _cost_line(inst))
    if not (a.yes or a.dry_run) and input("create these resources and install? [y/N] ").lower() != "y":
        sys.exit("cancelled")
    inst.listeners.append(_status_printer)
    ok = inst.run(dry_run=a.dry_run)
    print(f"\ninstall {iid}: {'complete' if ok else 'stopped with errors'}; logs in {inst.logs.dir}")
    if ok and not a.dry_run and v.get("ADMIN_PASSWORD"):
        pw = v.get("ADMIN_PASSWORD") if a.show else "(in the vault as ADMIN_PASSWORD; rerun with --show to print it)"
        print(f"admin sign-in: https://auth.{inst.plan['DOMAIN']}  user {inst.plan['ADMIN_USER']}  password: {pw}")
    sys.exit(0 if ok else 1)


def cmd_status(a) -> None:
    from .engine import InstallState
    ids = [a.id] if a.id else InstallState.list_installs()
    for iid in ids:
        st = InstallState(iid)
        print(f"{iid}  ({st.plan.get('DOMAIN')})")
        for sid, s in st.data["steps"].items():
            print(f"  {s['status']:8} {sid:24} {s.get('message', '')}")


def cmd_retry(a) -> None:
    from .session import Installation
    v = _vault()
    inst = Installation(a.id, v, prompt=_prompt)
    inst.retry(a.step)
    inst.listeners.append(_status_printer)
    # an adopted install may list apps whose install step never ran because they still live in their old layout
    # (heardos-cli migrate moves them): a full run would install them on empty folders, so retry stays with its step
    only = {a.step} if inst.plan.get("adopted_pending") else None
    sys.exit(0 if inst.run(only=only) else 1)


def cmd_destroy(a) -> None:
    from .session import Installation
    v = _vault()
    inst = Installation(a.id, v)
    if not a.yes and input(f"delete the VPS of install {a.id}? [y/N] ").lower() != "y":
        sys.exit("cancelled")
    print(inst.destroy_vps())


def cmd_ovh_confirm(a) -> None:
    """Finish an OVH termination with the token from the confirmation email."""
    from .providers.ovh import Ovh
    Ovh(_vault().get("OVH_CREDENTIALS") or "").confirm_destroy(a.service, a.token)
    print(f"termination of {a.service} confirmed")


def cmd_health(a) -> None:
    from . import ops
    from .session import Installation
    inst = Installation(a.id, _vault())
    bad = 0
    for c in ops.health(inst):
        mark = "INFO" if c.ok is None else ("PASS" if c.ok else "WARN")
        bad += c.ok is False
        print(f"{mark}  {c.area:14} {c.name:44} {c.detail}")
    if a.scan and inst.plan.get("NEBULA_LH_IP"):
        r = ops.portscan(inst.plan["NEBULA_LH_IP"])
        bad += bool(r["unexpected"])
        print(f"{'WARN' if r['unexpected'] else 'PASS'}  {'Public server':14} {'Open TCP ports':44} {r['open']} ({r['seconds']} s)")
    sys.exit(1 if bad else 0)


def cmd_people(a) -> None:
    from . import ops
    from .session import Installation
    inst = Installation(a.id, _vault())
    if a.add:
        user, name, email = a.add
        apps = a.apps.split(",") if a.apps else [x for x in inst.plan.get("apps", [])]
        pw = ops.add_person(inst, user, name, email, apps, ["flow-recovery-allowed", "flow-self-service"], a.welcome)
        print(f"{user} created; first password: " + (pw if a.show else f"in the vault as PERSON_{user.upper()}_FIRST_PASSWORD (--show prints it)"))
        return
    for u in ops.people(inst):
        print(f"{u['username']:16} {u['name']:24} {'active' if u['active'] else 'SUSPENDED':10} apps: {', '.join(u['apps'])}")


def cmd_plan(a) -> None:
    """Plain plan keys (never secrets): `heardos-cli plan ID` prints them, `heardos-cli plan ID KEY=VALUE ...` sets them."""
    from .engine import InstallState
    st = InstallState(a.id)
    if not a.kv:
        for k in sorted(st.plan):
            print(f"{k}={st.plan[k]}")
        return
    for kv in a.kv:
        if "=" not in kv:
            sys.exit(f"expected KEY=VALUE, got {kv!r}")
        k, v = kv.split("=", 1)
        if v[:1] in ("[", "{"):          # lists and objects (e.g. apps=["immich","mealie"]) as JSON
            v = json.loads(v)
        if any(w in k.upper() for w in ("PASSWORD", "SECRET", "TOKEN", "_KEY")):
            sys.exit(f"{k} looks like a secret: put it in the vault (heardos-cli secret {k})")
        st.set_plan(**{k: v})
        print(f"{k}={v}")


def _backend(a):
    if a.id in (None, "sim"):
        from .manage.sim import SimBackend
        return SimBackend()
    from .manage.backend import RealBackend
    from .session import Installation
    return RealBackend(Installation(a.id, _vault(), prompt=_prompt))


def cmd_tools(a) -> None:
    from .manage import tools as T
    b = _backend(a)
    rows = [{"name": t.name, "risk": t.risk, "summary": t.summary, "schema": t.schema(b)} for t in T.TOOLS if t.usable(b)]
    if a.json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        args = ", ".join(f"{k}{'' if k in r['schema']['required'] else '?'}" for k in r["schema"]["properties"])
        print(f"{r['risk']:7} {r['name']}({args})  {r['summary']}")


def cmd_tool(a) -> None:
    from .harness.schema import check, coerce
    from .manage import tools as T
    b = _backend(a)
    t = T.BY_NAME.get(a.name) or sys.exit(f"no tool {a.name}; see heardos-cli tools")
    args = coerce(t.schema(b), json.loads(a.args or "{}"))
    errs = check(t.schema(b), args)
    if errs:
        sys.exit("wrong arguments: " + "; ".join(errs))
    if t.preview and t.risk != "read":
        ok, preview = t.preview(b, args)
        print(preview)
        if not ok:
            sys.exit(1)
        if t.risk == "danger" or not a.yes:
            want = str(args.get("app", "yes")) if t.risk == "danger" else "y"
            if not sys.stdin.isatty() or input(f"type {want} to go ahead: ").strip().lower() != want.lower():
                sys.exit("cancelled")
    r = T.call(b, t.name, args)
    print(r.text)
    sys.exit(0 if r.ok else 1)


def cli(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="heardos-cli", description="HeardOS installer (headless)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    s = sub.add_parser("secret"); s.add_argument("name"); s.add_argument("value", nargs="?", help=argparse.SUPPRESS)
    s.add_argument("--value-stdin", action="store_true", help="read the value from stdin instead of a hidden prompt"); s.set_defaults(fn=cmd_secret)
    s = sub.add_parser("install"); s.add_argument("--plan"); s.add_argument("--id"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--yes", action="store_true"); s.add_argument("--show", action="store_true", help="print the admin password at the end")
    s.set_defaults(fn=cmd_install)
    s = sub.add_parser("status"); s.add_argument("id", nargs="?"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("retry"); s.add_argument("id"); s.add_argument("step"); s.set_defaults(fn=cmd_retry)
    s = sub.add_parser("destroy"); s.add_argument("id"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_destroy)
    s = sub.add_parser("ovh-confirm", help="confirm an OVH VPS termination with the emailed token"); s.add_argument("service"); s.add_argument("token"); s.set_defaults(fn=cmd_ovh_confirm)
    s = sub.add_parser("health", help="posture check of an install (read-only)"); s.add_argument("id"); s.add_argument("--scan", action="store_true", help="also scan all TCP ports of the VPS"); s.set_defaults(fn=cmd_health)
    s = sub.add_parser("people", help="list family accounts, or --add USER NAME EMAIL"); s.add_argument("id"); s.add_argument("--add", nargs=3, metavar=("USER", "NAME", "EMAIL"))
    s.add_argument("--apps", help="comma-separated app stacks (default: all installed)"); s.add_argument("--welcome", action="store_true", help="email a set-password link")
    s.add_argument("--show", action="store_true", help="print the new person's first password"); s.set_defaults(fn=cmd_people)
    s = sub.add_parser("tools", help="list the stack-management tools"); s.add_argument("id", nargs="?", help="install id, or sim (default)")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_tools)
    s = sub.add_parser("tool", help="run one stack-management tool"); s.add_argument("id"); s.add_argument("name"); s.add_argument("--args", help="JSON object")
    s.add_argument("--yes", action="store_true", help="skip the question for changes (removals still ask)")
    s.set_defaults(fn=cmd_tool)
    s = sub.add_parser("adopt", help="take over a HeardOS that was set up without this installer"); s.add_argument("spec")
    s.add_argument("--vault", help="KEY=VALUE file of values for the vault (tokens)"); s.add_argument("--secrets", help="KEY=VALUE file seeded into the host's secrets.env")
    s.add_argument("--apply", action="store_true"); s.set_defaults(fn=lambda a: __import__("heardos_installer.adopt", fromlist=["main"]).main(a))
    s = sub.add_parser("migrate", help="move an adopted stack into the kit layout"); s.add_argument("id"); s.add_argument("stack", nargs="?")
    s.add_argument("--spec", help="migration spec JSON (default: ~/.config/heardos/migrate/<id>.json)"); s.add_argument("--apply", action="store_true")
    s.add_argument("--rollback", action="store_true", help="undo a migrated stack: folders back, old stack redeployed")
    s.add_argument("--release-hold", action="store_true", help="migrate a stack the spec marks on hold (the planned core cutover)")
    s.set_defaults(fn=lambda a: __import__("heardos_installer.migrate", fromlist=["main"]).main(a))
    s = sub.add_parser("plan", help="show the plan of an install, or set KEY=VALUE entries in it"); s.add_argument("id"); s.add_argument("kv", nargs="*", metavar="KEY=VALUE")
    s.set_defaults(fn=cmd_plan)
    sub.add_parser("list").set_defaults(fn=lambda a: [print(i) for i in __import__("heardos_installer.engine", fromlist=["InstallState"]).InstallState.list_installs()])
    a = ap.parse_args(argv)
    a.fn(a)


def gui() -> None:
    from .app import main
    main()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("init", "secret", "install", "status", "retry", "destroy", "list", "health", "people", "tools", "tool",
                                                   "adopt", "migrate", "plan", "-h", "--help"):
        cli()
    else:
        gui()
