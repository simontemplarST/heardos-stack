"""Day-two screens: Health, People, Backups, Logs, Settings (Stacks and Assistant live in stacks.py and assistant.py). All slow work runs through st.tasks; these functions
only draw and start tasks. They work on st.inst (the install opened from Welcome or just finished)."""
from __future__ import annotations

import re

from imgui_bundle import imgui

from .. import ops
from ..kit import catalog
from . import widgets as w
from .theme import C, ICON_BAD, ICON_OK, ICON_WAIT

FLOW_GROUPS = [
    ("flow-mfa-required", "Must use a second factor (passkey, app code or email code)"),
    ("flow-recovery-allowed", "May reset a forgotten password by email"),
    ("flow-self-service", "May change their own password and profile"),
    ("flow-session-8h", "Signed out after 8 hours (shared computers)"),
    ("flow-login-blocked", "Cannot sign in (temporary suspension)"),
]
USERNAME = re.compile(r"^[a-z][a-z0-9._-]{1,30}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _need_install(st) -> bool:
    if st.inst is None:
        w.title("No install open")
        w.help_text("Open an install from the Welcome screen to manage it.")
        if w.primary("Go to Welcome"):
            st.mode, st.screen = "wizard", "welcome"
        return True
    return False


def _task_line(st, name: str, ok_text=None) -> None:
    t = st.tasks.get(name)
    if not t:
        return
    if t.running:
        w.spinner_text("Working")
    elif t.error:
        w.status_line(False, t.error)
    elif ok_text is not None:
        w.status_line(True, ok_text(t.result) if callable(ok_text) else ok_text)


def _run_steps(st, ids: set[str], label: str) -> None:
    if st.inst.running:
        st.messages["steps"] = (False, "Something is already running; wait for it to finish.")
        return
    st.messages["steps"] = (None, f"{label}: running")

    def done(ok):
        st.messages["steps"] = (ok, f"{label}: {'done' if ok else 'failed, see Logs'}")
    st.inst.run_async(only=ids, done=done)


def _steps_banner(st) -> None:
    m = st.messages.get("steps")
    if m:
        if m[0] is None and st.inst.running:
            w.spinner_text(m[1])
        else:
            w.status_line(m[0], m[1])


# ------------------------------------------------------------------ Health
def dashboard(st) -> None:
    if _need_install(st):
        return
    p = st.inst.plan
    w.title("Health")
    w.help_text(f"Checks on {p.get('DOMAIN')}: the public server, the home server, and every web address. Nothing is changed.")
    if w.primary("Run health check", not st.tasks.running("health")):
        st.tasks.start("health", lambda: ops.health(st.inst))
    imgui.same_line()
    if w.secondary("Scan the public server's ports", not st.tasks.running("scan") and bool(p.get("NEBULA_LH_IP"))):
        st.tasks.start("scan", lambda: ops.portscan(p["NEBULA_LH_IP"]))
    imgui.spacing()
    t = st.tasks.get("scan")
    if t:
        if t.running:
            w.spinner_text("Scanning all 65,535 ports (about 3 minutes)")
        elif t.error:
            w.status_line(False, t.error)
        else:
            r = t.result
            w.status_line(not r["unexpected"], f"Open ports: {', '.join(map(str, r['open'])) or 'none'}"
                          + (f"; UNEXPECTED: {', '.join(map(str, r['unexpected']))}" if r["unexpected"] else " (as expected)")
                          + f"  ({r['seconds']} s)")
        imgui.spacing()
    t = st.tasks.get("health")
    if not t:
        w.help_text("Run the check to see the current state.")
        return
    if t.running:
        w.spinner_text("Checking")
        return
    if t.error:
        w.status_line(False, t.error)
        return
    checks = t.result
    bad = sum(1 for c in checks if c.ok is False)
    w.status_line(bad == 0, "Everything looks good." if not bad else f"{bad} thing(s) need attention.")
    area = None
    flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.sizing_stretch_prop | imgui.TableFlags_.borders_inner_h
    if imgui.begin_table("health", 3, flags):
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed, 26)
        imgui.table_setup_column("Check", imgui.TableColumnFlags_.width_stretch, 1.2)
        imgui.table_setup_column("Detail", imgui.TableColumnFlags_.width_stretch, 1.6)
        for c in checks:
            if c.area != area:
                area = c.area
                imgui.table_next_row(); imgui.table_next_column(); imgui.table_next_column()
                imgui.text_colored(C["ORANGE"], area.upper())
            imgui.table_next_row(); imgui.table_next_column()
            if c.ok is None:
                imgui.text_colored(C["MUTED"], ICON_WAIT)
            else:
                imgui.text_colored(C["GOOD"] if c.ok else C["BAD"], ICON_OK if c.ok else ICON_BAD)
            imgui.table_next_column(); imgui.text(c.name)
            imgui.table_next_column(); imgui.text_colored(C["MUTED"], c.detail)
        imgui.end_table()


# ------------------------------------------------------------------ People
def people(st) -> None:
    if _need_install(st):
        return
    inst = st.inst
    w.title("People")
    w.help_text("Everyone in the family gets one account for every app. Choose which apps each person can open.")
    if not st.tasks.get("people") or (w.secondary("Refresh", not st.tasks.running("people"))):
        st.tasks.start("people", lambda: ops.people(inst))
    t = st.tasks.get("people")
    installed = [catalog.apps()[a] for a in inst.plan.get("apps", []) if catalog.apps()[a].slug and catalog.apps()[a].available]
    imgui.spacing()
    if t and t.running and not t.result:
        w.spinner_text("Loading accounts")
    elif t and t.error:
        w.status_line(False, t.error)
    elif t and t.result is not None:
        sel = st.inputs.get("person", "")
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.sizing_stretch_prop
        if imgui.begin_table("people", 4, flags):
            for col in ("Name", "Username", "Apps", "Status"):
                imgui.table_setup_column(col)
            imgui.table_headers_row()
            for u in t.result:
                imgui.table_next_row(); imgui.table_next_column()
                if imgui.selectable(f"{u['name']}##{u['username']}", sel == u["username"], imgui.SelectableFlags_.span_all_columns)[0]:
                    st.inputs["person"] = u["username"]
                    st.inputs["person_apps"] = ",".join(u["apps"]); st.inputs["person_flows"] = ",".join(u["flows"])
                imgui.table_next_column(); imgui.text(u["username"] + ("  (admin)" if u["admin"] else ""))
                imgui.table_next_column(); imgui.text_colored(C["MUTED"], f"{len(u['apps'])} of {len(installed)}")
                imgui.table_next_column()
                if not u["active"]:
                    imgui.text_colored(C["BAD"], "suspended")
                elif "flow-login-blocked" in u["flows"]:
                    imgui.text_colored(C["WARN"], "blocked")
                else:
                    imgui.text_colored(C["GOOD"], "active")
            imgui.end_table()
        u = next((x for x in t.result if x["username"] == sel), None)
        if u:
            _person_editor(st, u, installed)
    imgui.spacing(); imgui.separator(); imgui.spacing()
    _add_person(st, installed)


def _app_checkboxes(st, key: str, installed, default_all: bool) -> list[str]:
    chosen = set(filter(None, st.inputs.get(key, ",".join(a.slug for a in installed) if default_all else "").split(",")))
    for i, a in enumerate(installed):
        if i % 3:
            imgui.same_line(220 * (i % 3) + 8)
        ch, val = imgui.checkbox(f"{a.name}##{key}{a.stack}", a.slug in chosen)
        if ch:
            chosen.add(a.slug) if val else chosen.discard(a.slug)
    st.inputs[key] = ",".join(sorted(chosen))
    return sorted(chosen)


def _flow_checkboxes(st, key: str) -> list[str]:
    chosen = set(filter(None, st.inputs.get(key, "flow-recovery-allowed,flow-self-service").split(",")))
    for g, label in FLOW_GROUPS:
        ch, val = imgui.checkbox(f"{label}##{key}{g}", g in chosen)
        if ch:
            chosen.add(g) if val else chosen.discard(g)
    st.inputs[key] = ",".join(sorted(chosen))
    return sorted(chosen)


def _person_editor(st, u: dict, installed) -> None:
    inst = st.inst
    imgui.spacing()
    w.heading(f"{u['name']} ({u['username']})", u["email"])
    slug_to_stack = {a.slug: a.stack for a in installed}
    want = _app_checkboxes(st, "person_apps", installed, False)
    imgui.spacing()
    flows = _flow_checkboxes(st, "person_flows")
    if w.primary("Save changes", not st.tasks.running("person")):
        add = [slug_to_stack[s] for s in want if s not in u["apps"]]
        rem = [slug_to_stack[s] for s in u["apps"] if s not in want and s in slug_to_stack]

        def save():
            ops.set_access(inst, u["username"], add, rem)
            ops.set_groups(inst, u["username"], [g for g in flows if g not in u["flows"]], [g for g in u["flows"] if g not in flows])
            st.tasks.start("people", lambda: ops.people(inst))
            return "saved"
        st.tasks.start("person", save)
    imgui.same_line()
    if w.secondary("Email a set-password link", inst.plan.get("mail_mode") not in (None, "none") and not st.tasks.running("person")):
        st.tasks.start("person", lambda: ops.person_action(inst, u["username"], "welcome"))
    imgui.same_line()
    act = "resume" if not u["active"] else "suspend"
    if w.secondary("Allow sign-in again" if act == "resume" else "Suspend", not st.tasks.running("person")):
        st.tasks.start("person", lambda: (ops.person_action(inst, u["username"], act), st.tasks.start("people", lambda: ops.people(inst)))[0])
    _task_line(st, "person", lambda r: str(r))


def _add_person(st, installed) -> None:
    inst = st.inst
    w.heading("Add a family member")
    st.inputs["np_name"] = w.field("Full name", st.inputs.get("np_name", ""), key="npn")
    st.inputs["np_user"] = w.field("Username", st.inputs.get("np_user", ""), "lowercase, e.g. sam", key="npu").strip().lower()
    st.inputs["np_email"] = w.field("Email address", st.inputs.get("np_email", ""), key="npe").strip()
    imgui.text("Apps they can open")
    apps = _app_checkboxes(st, "np_apps", installed, True)
    imgui.text("Sign-in rules")
    flows = _flow_checkboxes(st, "np_flows")
    mail_on = inst.plan.get("mail_mode") not in (None, "none")
    if mail_on:
        _, v = imgui.checkbox("Email them a link to choose their own password", st.inputs.get("np_welcome", "1") == "1")
        st.inputs["np_welcome"] = "1" if v else "0"
    ok = bool(st.inputs["np_name"]) and USERNAME.match(st.inputs["np_user"] or "") and EMAIL.match(st.inputs["np_email"] or "")
    if w.primary("Add person", bool(ok) and not st.tasks.running("addperson")):
        slug_to_stack = {a.slug: a.stack for a in installed}
        args = (st.inputs["np_user"], st.inputs["np_name"], st.inputs["np_email"], [slug_to_stack[s] for s in apps], flows,
                mail_on and st.inputs.get("np_welcome", "1") == "1")

        def add():
            pw = ops.add_person(inst, *args)
            st.tasks.start("people", lambda: ops.people(inst))
            return (args[0], pw)
        st.tasks.start("addperson", add)
    t = st.tasks.get("addperson")
    if t and t.running:
        w.spinner_text("Creating the account")
    elif t and t.error:
        w.status_line(False, t.error)
    elif t and t.result:
        user, pw = t.result
        w.status_line(True, f"{user} can sign in at https://auth.{inst.plan.get('DOMAIN')} with this first password:")
        imgui.text("   "); imgui.same_line(); imgui.text_colored(C["ORANGE"], pw)
        imgui.same_line()
        if w.secondary("Copy"):
            imgui.set_clipboard_text(pw)
        imgui.same_line()
        if w.secondary("Done"):
            st.tasks.clear("addperson")
            for k in ("np_name", "np_user", "np_email"):
                st.inputs.pop(k, None)


# ------------------------------------------------------------------ Backups
def backups(st) -> None:
    if _need_install(st):
        return
    inst = st.inst
    w.title("Backups")
    w.help_text("A nightly, encrypted, deduplicated backup (restic) of every app's data, settings and secrets, and by "
                "default your photos and music too. Keep it on a different disk or machine than the home server.")
    cur = inst.vault.get("RESTIC_REPOSITORY") or ""
    st.inputs["repo"] = w.field("Where backups go", st.inputs.get("repo", cur),
                                "/mnt/backup/heardos   or   sftp:user@nas:/backups/heardos   or   s3:https://…", width=560, key="repo").strip()
    w.help_text("A folder on a USB disk or second drive mounted on the home server, a NAS over SFTP, or S3-compatible "
                "storage. The repository password is generated and kept on the home server and in its own backup.")
    busy = st.tasks.running("backup")
    installed = inst.plan.get("backups_installed")
    if w.primary("Set up nightly backups" if not installed else "Save and re-check setup", bool(st.inputs["repo"]) and not busy):
        st.tasks.start("backup", lambda: ops.backup(inst, "install", st.inputs["repo"]))
    if installed:
        imgui.same_line()
        if w.secondary("Back up now", not busy):
            st.tasks.start("backup", lambda: ops.backup(inst, "run"))
        imgui.same_line()
        if w.secondary("List backups", not busy):
            st.tasks.start("backup", lambda: ops.backup(inst, "list"))
        imgui.same_line()
        if w.secondary("Verify backups", not busy):
            st.tasks.start("backup", lambda: ops.backup(inst, "check"))
    t = st.tasks.get("backup")
    if t and t.running:
        w.spinner_text("Working (a first backup of photos can take hours)")
    elif t and t.error:
        w.status_line(False, t.error)
    elif t and t.result is not None:
        w.status_line(True, "Done.")
        imgui.begin_child("backup_out", imgui.ImVec2(0, 260))
        imgui.text_unformatted(t.result)
        imgui.end_child()
    imgui.spacing()
    w.help_text("Restoring is a deliberate, hands-on job: on the home server run\n"
                "  /opt/heardos-kit/generic/70-backup.sh restore <snapshot> /restore-here\n"
                "and copy back what you need.")


# ------------------------------------------------------------------ Logs
def logs(st) -> None:
    if _need_install(st):
        return
    inst = st.inst
    w.title("Logs")
    files = sorted(inst.logs.dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        w.help_text("Nothing logged yet.")
        return
    imgui.begin_child("logfiles", imgui.ImVec2(240, 0))
    for f in files:
        if imgui.selectable(f.stem, st.inputs.get("logfile") == f.name)[0]:
            st.inputs["logfile"] = f.name
    imgui.end_child()
    imgui.same_line()
    imgui.begin_child("logtext", imgui.ImVec2(0, 0))
    name = st.inputs.get("logfile") or files[0].name
    p = inst.logs.dir / name
    if p.exists():
        text = p.read_text(errors="replace")[-60000:]
        if w.secondary("Copy to clipboard"):
            imgui.set_clipboard_text(text)
        imgui.same_line(); imgui.text_colored(C["MUTED"], str(p))
        imgui.separator()
        imgui.text_unformatted(text)
    imgui.end_child()


# ------------------------------------------------------------------ Settings
def settings(st) -> None:
    if _need_install(st):
        return
    inst = st.inst
    p = inst.plan
    w.title("Settings")
    _steps_banner(st)
    w.heading("Look", "Change the name, colour or logo, then re-apply to the sign-in page, emails and hub.")
    st.inputs["s_title"] = w.field("Name of your cloud", st.inputs.get("s_title", p.get("BRAND_TITLE", "")), key="st")
    col = [int(st.inputs.get("s_color", p.get("BRAND_PRIMARY", "#f2a35e"))[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    imgui.text("Accent colour")
    ch, col = imgui.color_edit3("##s_accent", col, imgui.ColorEditFlags_.no_inputs)
    if ch:
        st.inputs["s_color"] = "#%02x%02x%02x" % tuple(int(c * 255) for c in col)
    st.inputs["s_logo"] = w.field("Logo (optional)", st.inputs.get("s_logo", p.get("BRAND_LOGO", "")), "path to a PNG or JPG", width=520, key="sl")
    if w.primary("Re-apply the look", not inst.running):
        ids = ops.reapply_brand(inst, BRAND_TITLE=st.inputs["s_title"], BRAND_PRIMARY=st.inputs.get("s_color", p.get("BRAND_PRIMARY")),
                                BRAND_LOGO=st.inputs["s_logo"])
        if "homepage" in p.get("apps", []):
            ids.add("app.homepage"); inst.state.reset_step("app.homepage")
        _run_steps(st, ids, "Applying the look")
    imgui.spacing(); imgui.separator(); imgui.spacing()
    w.heading("Vault passphrase")
    st.inputs["np1"] = w.field("New passphrase (12+ characters)", st.inputs.get("np1", ""), password=True, key="np1")
    st.inputs["np2"] = w.field("Again", st.inputs.get("np2", ""), password=True, key="np2")
    okp = len(st.inputs["np1"]) >= 12 and st.inputs["np1"] == st.inputs["np2"]
    if w.secondary("Change passphrase", okp):
        newpw = st.inputs.pop("np1"); st.inputs.pop("np2", None)
        inst.vault.change_passphrase(newpw)
        from ..vault import keyring_
        if keyring_.available() and keyring_.recall():
            keyring_.remember(newpw)          # keep "remember me" working with the new passphrase
        st.messages["vaultpw"] = (True, "Passphrase changed.")
    if "vaultpw" in st.messages:
        w.status_line(*st.messages["vaultpw"])
    imgui.spacing(); imgui.separator(); imgui.spacing()
    imgui.push_style_color(imgui.Col_.text, C["BAD"]); imgui.text("DANGER ZONE"); imgui.pop_style_color()
    h = inst.state.handle("vps")
    if h:
        w.help_text(f"Delete the public server ({h.get('provider')} {h.get('ip') or h.get('id')}). Your cloud stops being "
                    f"reachable from outside the house; everything at home is kept. Re-running the install creates a new one.")
        st.inputs["confirm_destroy"] = w.field(f"Type {p.get('DOMAIN')} to confirm", st.inputs.get("confirm_destroy", ""), key="cd")
        imgui.push_style_color(imgui.Col_.button, C["BAD"])
        if imgui.button("Delete the public server") and st.inputs["confirm_destroy"] == p.get("DOMAIN") and not st.tasks.running("destroy"):
            st.tasks.start("destroy", inst.destroy_vps)
        imgui.pop_style_color()
        _task_line(st, "destroy", lambda r: str(r))
    else:
        w.help_text("No public server was created by this app for this install (or it was already deleted).")
