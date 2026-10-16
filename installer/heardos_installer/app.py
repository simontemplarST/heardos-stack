"""The desktop app shell: sidebar with the wizard (or day-two tabs), the current screen, and the window plumbing.

Debug/screenshot mode (used for docs and visual checks):
  HEARDOS_UI_DEMO=1          throwaway vault + sample answers, nothing real is touched
  HEARDOS_UI_SCREEN=<id>     start on that screen
  HEARDOS_UI_SHOT=<png>      save a screenshot after a few frames and quit
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from imgui_bundle import hello_imgui, imgui, immapp

from . import __version__
from .ui import theme
from .ui import widgets as w
from .ui.screens import SCREENS
from .ui.state import DAY2, WIZARD, UIState
from .ui.theme import C, ICON_CUR, ICON_OK


def _sidebar(st: UIState) -> None:
    imgui.begin_child("sidebar", imgui.ImVec2(230, 0))
    imgui.dummy(imgui.ImVec2(0, 6))
    imgui.push_font(None, imgui.get_font_size() * 1.3)
    imgui.text_colored(C["ORANGE"], st.draft.get("BRAND_TITLE") or "HeardOS")
    imgui.pop_font()
    imgui.text_colored(C["MUTED"], "family cloud setup")
    imgui.spacing(); imgui.separator(); imgui.spacing()
    items = WIZARD if st.mode == "wizard" else DAY2
    ids = [i for i, _ in items]
    cur = ids.index(st.screen) if st.screen in ids else 0
    installing = st.inst is not None and st.screen in ("install", "done")
    for i, (sid, label) in enumerate(items):
        if st.mode == "wizard":
            mark = ICON_OK if i < cur else (ICON_CUR if i == cur else "  ")
            col = C["GOOD"] if i < cur else (C["ORANGE"] if i == cur else C["MUTED"])
            x0 = imgui.get_cursor_pos_x()
            imgui.text_colored(col, mark); imgui.same_line(); imgui.set_cursor_pos_x(x0 + 22)
            can_jump = i < cur and not installing and st.vault.unlocked and sid not in ("install", "done")
            if not can_jump and i != cur:
                imgui.begin_disabled()
            if imgui.selectable(label, i == cur)[0] and can_jump:
                st.go(sid)
            if not can_jump and i != cur:
                imgui.end_disabled()
        else:
            if imgui.selectable(label, sid == st.screen)[0]:
                st.screen = sid
    if st.mode == "day2":
        imgui.spacing(); imgui.separator(); imgui.spacing()
        if imgui.selectable("All installs", False)[0]:
            st.mode, st.screen = "wizard", "welcome"
    imgui.set_cursor_pos_y(imgui.get_window_height() - 34)
    imgui.text_colored(C["MUTED"], f"v{__version__}")
    if st.inst is not None and st.mode == "wizard" and st.screen == "done":
        pass
    imgui.end_child()


def _day2_placeholder(st: UIState) -> None:
    from .ui import day2
    fn = getattr(day2, st.screen, None)
    if fn:
        fn(st)
    else:
        w.title(dict(DAY2).get(st.screen, st.screen))


def make_gui(st: UIState):
    frame = {"n": 0}
    shot = os.environ.get("HEARDOS_UI_SHOT")

    def gui() -> None:
        frame["n"] += 1
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(vp.work_pos)
        imgui.set_next_window_size(vp.work_size)
        flags = (imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_saved_settings |
                 imgui.WindowFlags_.no_bring_to_front_on_focus)
        imgui.begin("##root", None, flags)
        _sidebar(st)
        imgui.same_line()
        imgui.begin_child("main", imgui.ImVec2(0, 0), imgui.ChildFlags_.always_use_window_padding)
        imgui.push_style_color(imgui.Col_.child_bg, C["CHARCOAL"])
        imgui.pop_style_color()
        if not st.vault.unlocked and st.screen != "welcome":
            st.screen = "welcome"
        if st.mode == "wizard":
            SCREENS[st.screen](st)
        else:
            _day2_placeholder(st)
        imgui.end_child()
        imgui.end()
        if shot and frame["n"] == 40:
            hello_imgui.get_runner_params().app_shall_exit = True

    return gui


def _demo_state() -> UIState:
    from .vault import Vault
    d = Path(tempfile.mkdtemp(prefix="heardos-demo-"))
    os.environ["HEARDOS_HOME"] = str(d)
    v = Vault(d / "vault.json")
    v.create("demo-passphrase-123")
    v.update({"CLOUDZY_API_TOKEN": "demo", "CF_API_TOKEN": "demo", "RESEND_API_KEY": "demo"})
    st = UIState(v)
    st.draft.update({"BRAND_TITLE": "The Smith Family Cloud", "DOMAIN": "smithfamily.example", "ADMIN_NAME": "Alice Smith",
                     "ADMIN_USER": "alice", "ADMIN_EMAIL": "alice@example.net", "HOST_LAN_IP": "192.168.1.50",
                     "provider": "cloudzy", "vps_region": "MIA", "vps_region_name": "Miami", "vps_plan": "x",
                     "vps_plan_name": "1 vCPU · 1 GB RAM · 25 GB disk", "vps_monthly": 6.95, "profile": "family", "mail_mode": "resend"})
    return st


def _demo_ops() -> None:
    """Screenshot mode: day-two operations return sample data instead of touching servers."""
    from . import ops
    C_ = ops.Check
    ops.health = lambda inst: [
        C_("Public server", "Password logins off", True, "passwordauthentication no"), C_("Public server", "Firewall on", True, "Status: active"),
        C_("Public server", "Web, tunnel, ban and update services running", True, "active active active active"),
        C_("Public server", "Security updates installed", False, "3 pending"), C_("Public server", "Disk space", True, "21% used"),
        C_("Home server", "Apps running", True, "27 of 27 containers fine"), C_("Home server", "Disk space", True, "48% used"),
        C_("Home server", "Private tunnel to the public server", True, "tunnel-ok"),
        C_("Home server", "Last backup", True, "success Thu 2026-10-16 02:41"),
        C_("Web addresses", "auth.smithfamily.example", True, "HTTP 302, certificate valid 84 more days"),
        C_("Web addresses", "photos.smithfamily.example", True, "HTTP 200, certificate valid 84 more days"),
        C_("Web addresses", "docs.smithfamily.example", True, "HTTP 302, certificate valid 84 more days")]
    ops.portscan = lambda ip, **k: {"open": [22, 80, 443], "unexpected": [], "seconds": 171}
    ops.people = lambda inst: [
        {"username": "alice", "name": "Alice Smith", "email": "alice@example.net", "active": True, "admin": True,
         "apps": ["baikal", "immich", "mealie", "nextcloud", "vaultwarden"], "flows": ["flow-mfa-required", "flow-self-service"]},
        {"username": "bob", "name": "Bob Smith", "email": "bob@example.net", "active": True, "admin": False,
         "apps": ["immich", "mealie", "nextcloud"], "flows": ["flow-recovery-allowed", "flow-self-service"]},
        {"username": "sam", "name": "Sam Smith", "email": "sam@example.net", "active": True, "admin": False,
         "apps": ["immich"], "flows": ["flow-session-8h"]}]
    ops.app_status = lambda inst: {a: "running" for a in inst.plan.get("apps", [])} | {"homebox": "unhealthy"}


def main() -> None:
    if os.environ.get("HEARDOS_UI_DEMO"):
        st = _demo_state()
    else:
        from .vault import Vault
        st = UIState(Vault())
    if os.environ.get("HEARDOS_UI_SCREEN"):
        st.screen = os.environ["HEARDOS_UI_SCREEN"]
        if st.screen in ("install", "done") and os.environ.get("HEARDOS_UI_DEMO"):
            from .ui.screens import _start_install
            from .steps import apply_defaults
            import copy
            st.demo_dry_run = True
            _start_install(st, apply_defaults(copy.deepcopy(st.draft)))
            st.screen = os.environ["HEARDOS_UI_SCREEN"]
        if st.screen in dict(DAY2):
            st.mode = "day2"
            if os.environ.get("HEARDOS_UI_DEMO"):
                from .ui.screens import _start_install
                from .steps import apply_defaults
                import copy
                _demo_ops()
                st.demo_dry_run = True
                _start_install(st, apply_defaults(copy.deepcopy(st.draft)))
                st.inst._thread.join(30)
                st.screen, st.mode = os.environ["HEARDOS_UI_SCREEN"], "day2"
                if st.screen == "dashboard":
                    st.tasks.start("health", lambda: __import__("heardos_installer.ops", fromlist=["x"]).health(st.inst))
                    st.tasks.start("scan", lambda: {"open": [22, 80, 443], "unexpected": [], "seconds": 171})
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "HeardOS setup"
    params.app_window_params.window_geometry.size = (1180, 780)
    params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.no_default_window
    params.callbacks.show_gui = make_gui(st)
    params.callbacks.setup_imgui_style = theme.apply
    params.callbacks.default_icon_font = hello_imgui.DefaultIconFont.font_awesome6
    params.fps_idling.fps_idle = 6          # low CPU while idle; full rate while the user interacts
    params.ini_disable = True
    addons = immapp.AddOnsParams()
    immapp.run(params, addons)
    shot = os.environ.get("HEARDOS_UI_SHOT")
    if shot:   # the final frame is only available after the loop has exited
        import numpy as np
        from PIL import Image
        Image.fromarray(np.ascontiguousarray(hello_imgui.final_app_window_screenshot())).save(shot)


if __name__ == "__main__":
    main()
