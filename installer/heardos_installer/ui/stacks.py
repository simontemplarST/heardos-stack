"""Stacks: every app on the home server in one place. Catalogue apps (install, start/stop/restart/update, remove),
"your own app" stacks from any Docker image, and for each one its containers, logs, settings (environment overrides
that survive updates) and compose file. Everything goes through manage.backend, the same code the model harness uses."""
from __future__ import annotations

import re
import webbrowser

from imgui_bundle import imgui

from ..manage.backend import taken
from ..manage.spec import ACCESS, ACCESS_LABEL, GENERATE, CustomStack, render_compose, validate
from . import widgets as w
from .theme import C, ICON_BAD, ICON_OK, ICON_WAIT

NEW = "__new__"
STATE_COLOR = {"running": "GOOD", "not installed": "MUTED", "": "MUTED"}


def backend(st):
    """The Backend for the open install: the simulated one in demo mode, else a RealBackend bound to st.inst."""
    if getattr(st, "sim_backend", None) is not None:
        return st.sim_backend
    cached = getattr(st, "_backend", None)
    if cached is None or cached.inst is not st.inst:
        from ..manage.backend import RealBackend
        st._backend = RealBackend(st.inst)
    return st._backend


def _need(st) -> bool:
    if getattr(st, "sim_backend", None) is not None:
        return False
    from .day2 import _need_install
    return _need_install(st)


def _refresh(st, b) -> None:
    st.tasks.start("inv", b.inventory)


def _task_status(st, name: str) -> None:
    t = st.tasks.get(name)
    if not t:
        return
    if t.running:
        w.spinner_text("Working")
    elif t.error:
        w.status_line(False, t.error)
    elif t.result:
        w.status_line(True, str(t.result))


def stacks(st) -> None:
    if _need(st):
        return
    b = backend(st)
    w.title("Stacks")
    w.help_text("Every app on the home server. Install from the catalogue or add your own from any Docker image; start, stop, "
                "update, read logs and change settings. Settings changed here survive updates and reinstalls.")
    if not st.tasks.get("inv"):
        _refresh(st, b)
    t = st.tasks.get("inv")
    rows = (t.result or []) if t and not t.running else (getattr(st, "_inv_last", None) or [])
    if t and t.result:
        st._inv_last = t.result
    if t and t.error:
        w.status_line(False, t.error)
    imgui.spacing()
    _list(st, b, rows)
    imgui.same_line()
    imgui.begin_child("stack_detail", imgui.ImVec2(0, 0), imgui.ChildFlags_.always_use_window_padding)
    sel = st.inputs.get("stack_sel", "")
    row = next((r for r in rows if r["stack"] == sel), None)
    if sel == NEW:
        _custom_form(st, b)
    elif row:
        _detail(st, b, row)
    elif t and t.running and not rows:
        w.spinner_text("Loading")
    else:
        w.help_text("Choose an app on the left, or add your own.")
    imgui.end_child()


def _list(st, b, rows) -> None:
    imgui.begin_child("stack_list", imgui.ImVec2(300, 0), imgui.ChildFlags_.borders)
    if w.primary("+ Add your own app", width=-1):
        st.inputs["stack_sel"] = NEW
    imgui.set_next_item_width(-1)
    _, st.inputs["stack_filter"] = imgui.input_text_with_hint("##sf", "filter", st.inputs.get("stack_filter", ""))
    f = st.inputs["stack_filter"].lower()
    if w.secondary("Refresh status", not st.tasks.running("inv")):
        _refresh(st, b)
    groups = [("INSTALLED", [r for r in rows if r["installed"] and r["kind"] == "app"]),
              ("YOUR OWN APPS", [r for r in rows if r["kind"] == "custom"]),
              ("AVAILABLE", [r for r in rows if not r["installed"]])]
    for label, items in groups:
        items = [r for r in items if not f or f in r["name"].lower() or f in r["stack"]]
        if not items:
            continue
        imgui.spacing()
        imgui.text_colored(C["ORANGE"], f"{label} ({len(items)})")
        for r in items:
            state = r.get("state", "")
            col = C[STATE_COLOR.get(state, "BAD")] if r["installed"] else C["MUTED"]
            _dot(col, filled=r["installed"])
            if imgui.selectable(f"{r['name']}##{r['stack']}", st.inputs.get("stack_sel") == r["stack"])[0]:
                st.inputs["stack_sel"] = r["stack"]
            if imgui.is_item_hovered():
                imgui.set_tooltip(f"{r['stack']}: {state or ('installed' if r['installed'] else 'not installed')}\n{r['blurb']}")
    imgui.end_child()


def _dot(col, filled: bool) -> None:
    """A status dot drawn, not typed: the UI font has no hollow-circle glyph."""
    p = imgui.get_cursor_screen_pos()
    h = imgui.get_text_line_height()
    c = imgui.ImVec2(p.x + h * 0.4, p.y + h * 0.55)
    dl = imgui.get_window_draw_list()
    if filled:
        dl.add_circle_filled(c, h * 0.3, imgui.get_color_u32(col))
    else:
        dl.add_circle(c, h * 0.28, imgui.get_color_u32(col), 0, 1.5)
    imgui.dummy(imgui.ImVec2(h * 0.9, h))
    imgui.same_line()


# ---------------------------------------------------------------- one stack
def _detail(st, b, r) -> None:
    s = r["stack"]
    imgui.push_font(None, imgui.get_font_size() * 1.25)
    imgui.text(r["name"])
    imgui.pop_font()
    imgui.same_line()
    w.chip("your own app" if r["kind"] == "custom" else r["stack"])
    w.help_text(r["blurb"])
    if r.get("address"):
        imgui.text_colored(C["MUTED"], "Address:")
        imgui.same_line()
        if imgui.selectable(r["address"], False, 0, imgui.calc_text_size(r["address"]))[0]:
            webbrowser.open(r["address"])
        if imgui.is_item_hovered():
            imgui.set_tooltip("Open in the browser")
    state = r.get("state", "")
    if r["installed"]:
        imgui.text_colored(C["MUTED"], "State:")
        imgui.same_line()
        imgui.text_colored(C[STATE_COLOR.get(state, "BAD")], state or "?")
    imgui.spacing()
    busy = st.tasks.running("act")
    if not r["installed"]:
        from ..kit import catalog
        extra = [catalog.apps()[d].name for d in catalog.closure([s]) if d != s and not any(x["stack"] == d and x["installed"] for x in getattr(st, "_inv_last", []))]
        if w.primary(f"Install {r['name']}", not busy):
            st.tasks.start("act", lambda: _after(st, b, b.install_app(s)))
        if extra:
            imgui.same_line()
            imgui.text_colored(C["MUTED"], "also installs: " + ", ".join(extra))
        _task_status(st, "act")
        return
    for verb, label in (("restart", "Restart"), ("update", "Update"), ("stop", "Stop"), ("start", "Start")):
        if w.secondary(f"{label}##{s}", not busy):
            st.tasks.start("act", lambda v=verb: _after(st, b, b.lifecycle(s, v)))
        imgui.same_line()
    imgui.push_style_color(imgui.Col_.button, C["BAD"])
    if imgui.button("Remove…"):
        imgui.open_popup("remove_stack")
    imgui.pop_style_color()
    _remove_popup(st, b, r)
    _task_status(st, "act")
    imgui.spacing()
    want = st.inputs.pop("stack_tab", "")          # jump to a tab once (screenshots, links from other screens)

    def tab(label: str) -> bool:
        flags = imgui.TabItemFlags_.set_selected if want == label else 0
        return imgui.begin_tab_item(label, None, flags)[0]
    if imgui.begin_tab_bar("stack_tabs"):
        if tab("Containers"):
            _containers(st, b, s)
            imgui.end_tab_item()
        if tab("Logs"):
            _logs(st, b, s)
            imgui.end_tab_item()
        if tab("Settings"):
            _settings(st, b, s)
            imgui.end_tab_item()
        if tab("Compose file"):
            text = b.compose_text(s)
            if w.secondary("Copy"):
                imgui.set_clipboard_text(text)
            imgui.begin_child("compose", imgui.ImVec2(0, 0), imgui.ChildFlags_.borders)
            imgui.text_unformatted(text)
            imgui.end_child()
            imgui.end_tab_item()
        imgui.end_tab_bar()


def _after(st, b, msg: str) -> str:
    _refresh(st, b)
    st.tasks.clear("cont")
    return msg


def _remove_popup(st, b, r) -> None:
    if imgui.begin_popup_modal("remove_stack", None, imgui.WindowFlags_.always_auto_resize)[0]:
        imgui.text(f"Remove {r['name']}?")
        w.help_text("Its containers and web address go away. Its data folder stays on the home server, so installing it\n"
                    "again later picks up where it left off.")
        st.inputs["rm_confirm"] = w.field(f"Type {r['stack']} to confirm", st.inputs.get("rm_confirm", ""), key="rmc")
        imgui.push_style_color(imgui.Col_.button, C["BAD"])
        if imgui.button("Remove") and st.inputs["rm_confirm"] == r["stack"]:
            st.inputs["rm_confirm"] = ""
            st.tasks.start("act", lambda: _after(st, b, b.remove(r["stack"])))
            imgui.close_current_popup()
        imgui.pop_style_color()
        imgui.same_line()
        if imgui.button("Cancel"):
            st.inputs["rm_confirm"] = ""
            imgui.close_current_popup()
        imgui.end_popup()


def _containers(st, b, s) -> None:
    key = f"cont.{s}"
    if st.inputs.get("cont_for") != s or not st.tasks.get("cont"):
        st.inputs["cont_for"] = s
        st.tasks.start("cont", lambda: b.containers(s))
    t = st.tasks.get("cont")
    if w.secondary(f"Refresh##{key}", not t.running):
        st.tasks.start("cont", lambda: b.containers(s))
    if t.running:
        w.spinner_text("Asking the home server")
        return
    if t.error:
        w.status_line(False, t.error)
        return
    flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.sizing_stretch_prop
    if imgui.begin_table("containers", 4, flags):
        for col in ("", "Container", "Status", "Image"):
            imgui.table_setup_column(col, imgui.TableColumnFlags_.width_fixed if not col else 0, 22 if not col else 0)
        imgui.table_headers_row()
        for c in t.result or []:
            imgui.table_next_row(); imgui.table_next_column()
            good = c["state"] == "running" and "unhealthy" not in c["status"]
            imgui.text_colored(C["GOOD"] if good else C["BAD"], ICON_OK if good else ICON_BAD)
            imgui.table_next_column(); imgui.text(c["name"])
            imgui.table_next_column(); imgui.text_colored(C["MUTED"], c["status"])
            imgui.table_next_column(); imgui.text_colored(C["MUTED"], c["image"])
        imgui.end_table()
    if not t.result:
        w.help_text("No containers: the app is not running (Start brings it up).")


def _logs(st, b, s) -> None:
    n = int(st.inputs.get("log_lines", "200"))
    imgui.set_next_item_width(120)
    opts = ["100", "200", "500", "2000"]
    ch, i = imgui.combo("lines##loglines", opts.index(str(n)) if str(n) in opts else 1, opts)
    if ch:
        st.inputs["log_lines"] = opts[i]
    imgui.same_line()
    t = st.tasks.get("logs")
    fresh = st.inputs.get("logs_for") != f"{s}:{n}"
    if fresh or w.secondary("Refresh##logs", not (t and t.running)):
        st.inputs["logs_for"] = f"{s}:{n}"
        st.tasks.start("logs", lambda: b.logs(s, n))
        t = st.tasks.get("logs")
    if t and t.result:
        imgui.same_line()
        if w.secondary("Copy##logs"):
            imgui.set_clipboard_text(t.result)
    if t and t.running:
        w.spinner_text("Fetching logs")
    elif t and t.error:
        w.status_line(False, t.error)
    elif t:
        imgui.begin_child("logtext", imgui.ImVec2(0, 0), imgui.ChildFlags_.borders, imgui.WindowFlags_.horizontal_scrollbar)
        for line in (t.result or "").splitlines():
            low = line.lower()
            bad = any(x in low for x in ("error", "err ", "fatal", "panic", "exception"))
            warn = not bad and any(x in low for x in ("warn", "wrn "))
            imgui.text_colored(C["BAD"] if bad else (C["WARN"] if warn else C["LIGHT"]), line)
        if st.inputs.get("logs_scrolled") != st.inputs["logs_for"]:
            imgui.set_scroll_here_y(1.0)
            st.inputs["logs_scrolled"] = st.inputs["logs_for"]
        imgui.end_child()


def _settings(st, b, s) -> None:
    info = b.settings(s)
    over = info["overrides"]
    w.help_text("The app's environment as HeardOS sets it. Values you change are kept in compose.override.yml next to the app, "
                "so updates and reinstalls keep them. Passwords and tokens are managed by the app's own setup and are hidden.")
    flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_h | imgui.TableFlags_.scroll_y
    busy = st.tasks.running("setting")
    if imgui.begin_table("env", 4, flags, imgui.ImVec2(0, 280)):
        imgui.table_setup_column("Service", imgui.TableColumnFlags_.width_fixed, 110)
        imgui.table_setup_column("Setting", imgui.TableColumnFlags_.width_stretch, 1.0)
        imgui.table_setup_column("Value", imgui.TableColumnFlags_.width_stretch, 1.1)
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed, 70)
        imgui.table_headers_row()
        keys = {(svc, k) for svc, kv in info["services"].items() for k in kv} | {(svc, k) for svc, kv in over.items() for k in kv}
        for svc, k in sorted(keys):
            changed = k in over.get(svc, {})
            v = over[svc][k] if changed else info["services"].get(svc, {}).get(k, "")
            imgui.table_next_row(); imgui.table_next_column(); imgui.text_colored(C["MUTED"], svc)
            imgui.table_next_column(); imgui.text_colored(C["ORANGE"] if changed else C["LIGHT"], k)
            imgui.table_next_column(); imgui.text(v)
            if changed and imgui.is_item_hovered():
                imgui.set_tooltip(f"changed by you; default: {info['services'].get(svc, {}).get(k, '(not set)')}")
            imgui.table_next_column()
            if changed and w.secondary(f"Reset##{svc}{k}", not busy):
                st.tasks.start("setting", lambda svc=svc, k=k: b.set_setting(s, k, None, svc))
            elif not changed and imgui.small_button(f"Edit##{svc}{k}"):
                st.inputs.update(set_svc=svc, set_key=k, set_val=v)
        imgui.end_table()
    imgui.spacing()
    w.heading("Change a setting")
    svcs = list(info["services"]) or [s]
    cur = st.inputs.get("set_svc") if st.inputs.get("set_svc") in svcs else info["main"] or svcs[0]
    imgui.set_next_item_width(180)
    ch, i = imgui.combo("service##setsvc", svcs.index(cur), svcs)
    st.inputs["set_svc"] = svcs[i]
    st.inputs["set_key"] = w.field("Setting", st.inputs.get("set_key", ""), "UPPER_CASE, e.g. LOG_LEVEL", width=300, key="setk").strip()
    st.inputs["set_val"] = w.field("Value", st.inputs.get("set_val", ""), width=420, key="setv")
    from ..manage.spec import check_setting
    errs = check_setting(st.inputs["set_key"], st.inputs["set_val"]) if st.inputs["set_key"] else []
    for e in errs:
        w.status_line(False, e)
    if w.primary("Save and redeploy", bool(st.inputs["set_key"]) and not errs and not busy):
        args = (s, st.inputs["set_key"], st.inputs["set_val"], st.inputs["set_svc"])
        st.tasks.start("setting", lambda: b.set_setting(*args))
    _task_status(st, "setting")


# ---------------------------------------------------------------- your own app
def _form_spec(st) -> CustomStack:
    i = st.inputs
    env = {}
    for line in i.get("cs_env", "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return CustomStack.from_dict({"name": i.get("cs_name", ""), "title": i.get("cs_title", ""), "image": i.get("cs_image", ""),
                                  "port": i.get("cs_port", "0"), "host": i.get("cs_host", ""), "access": i.get("cs_access", "admins"),
                                  "volumes": [v for v in i.get("cs_vols", "").splitlines() if v.strip()], "env": env,
                                  "user": i.get("cs_user", ""), "memory": i.get("cs_mem", "")})


def _custom_form(st, b) -> None:
    i = st.inputs
    w.title("Add your own app")
    w.help_text("Any app that ships as a Docker image. HeardOS gives it a web address with HTTPS, puts it behind the family "
                "sign-in, keeps its data with the other apps' data (and in the backups), and generates any secrets it needs.")
    imgui.spacing()
    w.heading("Find the image")
    i["cs_q"] = w.field("Search Docker Hub", i.get("cs_q", ""), "e.g. uptime kuma", width=300, key="csq")
    imgui.same_line()
    if w.secondary("Search", len(i["cs_q"].strip()) >= 2 and not st.tasks.running("cs_search")):
        q = i["cs_q"]
        st.tasks.start("cs_search", lambda: b.search_images(q))
    t = st.tasks.get("cs_search")
    if t and t.running:
        w.spinner_text("Searching")
    elif t and t.error:
        w.status_line(False, t.error)
    elif t and t.result is not None:
        for h in t.result:
            if imgui.selectable(f"{h['image']}{'  (official)' if h['official'] else ''}  {h['stars']} stars  {h['about'][:70]}##{h['image']}", False)[0]:
                i["cs_image"] = f"{h['image']}:latest"
                i.setdefault("cs_name", "")
                if not i["cs_name"]:
                    i["cs_name"] = re.sub(r"[^a-z0-9-]", "-", h["image"].rsplit("/", 1)[-1].lower())[:30].strip("-")
                st.tasks.clear("cs_search")
        if not t.result:
            w.help_text("Nothing found.")
    imgui.spacing()
    w.heading("The app")
    i["cs_image"] = w.field("Docker image (with a version tag)", i.get("cs_image", ""), "louislam/uptime-kuma:1", width=360, key="csi").strip()
    imgui.same_line()
    if w.secondary("Read from image", bool(i["cs_image"]) and not st.tasks.running("cs_inspect")):
        img = i["cs_image"]
        st.tasks.start("cs_inspect", lambda: b.inspect_image(img))
    t = st.tasks.get("cs_inspect")
    if t and t.running:
        w.spinner_text("Downloading the image on the home server")
    elif t and t.error:
        w.status_line(False, t.error)
    elif t and t.result and i.get("cs_inspected") != t.result["image"]:
        f = t.result
        i["cs_inspected"] = f["image"]
        if f["ports"] and i.get("cs_port", "0") in ("", "0"):
            web = [p for p in f["ports"] if p in (80, 8080, 3000, 8000, 5000, 9000)]
            i["cs_port"] = str((web or f["ports"])[0])
        if f["volumes"] and not i.get("cs_vols"):
            i["cs_vols"] = "\n".join(f["volumes"])
    if t and t.result and not t.running:
        f = t.result
        w.status_line(True, f"ports {', '.join(map(str, f['ports'])) or 'none declared'}; data folders {', '.join(f['volumes']) or 'none declared'}"
                      + (f"; variables {', '.join(f['env'][:8])}" if f["env"] else ""))
    imgui.begin_group()
    i["cs_name"] = w.field("Short name", i.get("cs_name", ""), "lowercase, e.g. uptime", width=220, key="csn").strip().lower()
    imgui.end_group()
    imgui.same_line(0, 24)
    imgui.begin_group()
    i["cs_title"] = w.field("Display name", i.get("cs_title", ""), "Uptime Kuma", width=260, key="cst")
    imgui.end_group()
    i["cs_port"] = w.field("Port the app listens on inside its container", i.get("cs_port", ""), "e.g. 3001", width=120, key="csp").strip()
    i["cs_host"] = w.field(f"Web address", i.get("cs_host", ""), i.get("cs_name") or "subdomain", width=220, key="csh").strip().lower()
    imgui.same_line(); imgui.text_colored(C["MUTED"], f".{b.domain}")
    imgui.text("Who can open it")
    for a in ACCESS:
        if imgui.radio_button(ACCESS_LABEL[a], i.get("cs_access", "admins") == a):
            i["cs_access"] = a
    if i.get("cs_access") == "public":
        w.status_line(None, "Public apps skip the sign-in completely: only choose this for something meant for strangers.")
    imgui.spacing()
    w.heading("Data and settings")
    imgui.text("Folders to keep (inside the container), one per line")
    _, i["cs_vols"] = imgui.input_text_multiline("##csv", i.get("cs_vols", ""), imgui.ImVec2(420, 70))
    imgui.text(f"Environment, one KEY=value per line ({GENERATE!r} as the value makes and keeps a random secret)")
    _, i["cs_env"] = imgui.input_text_multiline("##cse", i.get("cs_env", ""), imgui.ImVec2(420, 90))
    imgui.begin_group()
    i["cs_user"] = w.field("Run as uid:gid (optional)", i.get("cs_user", ""), "1000:1000", width=140, key="csu").strip()
    imgui.end_group()
    imgui.same_line(0, 24)
    imgui.begin_group()
    i["cs_mem"] = w.field("Memory limit (optional)", i.get("cs_mem", ""), "512m", width=120, key="csm").strip()
    imgui.end_group()
    imgui.spacing()
    spec = _form_spec(st)
    names, hosts = taken(getattr(st, "_inv_last", []) or [], b.custom)
    errs = validate(spec, taken_names=names, taken_hosts=hosts)
    for e in errs:
        w.status_line(False, e)
    if not errs:
        w.status_line(True, f"Ready: https://{spec.host}.{b.domain}, {ACCESS_LABEL[spec.access].lower()}.")
        if imgui.collapsing_header("Preview the compose file"):
            imgui.text_unformatted(render_compose(spec))
    busy = st.tasks.running("act")
    if w.primary("Add the app", not errs and not busy):
        d = spec.to_dict()
        st.tasks.start("act", lambda: _added(st, b, d))
    imgui.same_line()
    if w.secondary("Clear the form", not busy):
        for k in [k for k in i if k.startswith("cs_")]:
            i.pop(k)
        st.tasks.clear("cs_inspect"); st.tasks.clear("cs_search")
    t = st.tasks.get("act")
    if t and t.running:
        w.spinner_text("Adding it (DNS, certificate, sign-in, first start: a few minutes)")
    else:
        _task_status(st, "act")


def _added(st, b, d: dict) -> str:
    msg = b.add_custom(d)
    _refresh(st, b)
    st.inputs["stack_sel"] = d["name"]
    return msg
