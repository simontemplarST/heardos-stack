"""Hearth Crest palette for Dear ImGui: charcoal panels, ink text fields, orange accent, light text."""
from __future__ import annotations

from imgui_bundle import imgui


def hex4(h: str, a: float = 1.0) -> imgui.ImVec4:
    h = h.lstrip("#")
    return imgui.ImVec4(int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


ORANGE = "#f2a35e"
ORANGE_DEEP = "#ed8325"
ORANGE_LIGHT = "#f5b984"
INK = "#1f2023"
CHARCOAL = "#26272b"
SLATE = "#3a3b40"
LIGHT = "#ececec"
MUTED = "#a3a3aa"
GOOD = "#7cc48c"
BAD = "#e8786a"
WARN = "#e9c46a"

C = {name: hex4(v) for name, v in dict(ORANGE=ORANGE, ORANGE_DEEP=ORANGE_DEEP, ORANGE_LIGHT=ORANGE_LIGHT, INK=INK, CHARCOAL=CHARCOAL,
                                       SLATE=SLATE, LIGHT=LIGHT, MUTED=MUTED, GOOD=GOOD, BAD=BAD, WARN=WARN).items()}


def apply() -> None:
    s = imgui.get_style()
    s.window_rounding = 8
    s.child_rounding = 8
    s.frame_rounding = 6
    s.grab_rounding = 6
    s.popup_rounding = 8
    s.tab_rounding = 6
    s.frame_padding = imgui.ImVec2(10, 7)
    s.item_spacing = imgui.ImVec2(10, 9)
    s.window_padding = imgui.ImVec2(18, 16)
    s.scrollbar_size = 12
    s.window_border_size = 0
    s.child_border_size = 0
    col = imgui.Col_
    pairs = {
        col.text: LIGHT, col.text_disabled: MUTED, col.window_bg: CHARCOAL, col.child_bg: INK, col.popup_bg: CHARCOAL,
        col.border: SLATE, col.frame_bg: INK, col.frame_bg_hovered: SLATE, col.frame_bg_active: SLATE,
        col.title_bg: INK, col.title_bg_active: INK, col.menu_bar_bg: INK, col.scrollbar_bg: INK, col.scrollbar_grab: SLATE,
        col.check_mark: ORANGE, col.slider_grab: ORANGE, col.slider_grab_active: ORANGE_LIGHT,
        col.button: SLATE, col.button_hovered: "#4a4b52", col.button_active: ORANGE_DEEP,
        col.header: SLATE, col.header_hovered: "#4a4b52", col.header_active: ORANGE_DEEP,
        col.separator: SLATE, col.tab: INK, col.tab_hovered: SLATE, col.table_header_bg: INK,
        col.table_row_bg: CHARCOAL, col.table_row_bg_alt: "#2b2c31", col.text_selected_bg: ORANGE_DEEP,
        col.nav_cursor: ORANGE, col.plot_histogram: ORANGE,
    }
    for k, v in pairs.items():
        try:
            s.set_color_(k, hex4(v))
        except AttributeError:
            pass


# status symbols come from the Font Awesome 6 icons merged into the default font (the text font lacks ✓ ✗ ● etc.)
from imgui_bundle import icons_fontawesome_6 as _fa
ICON_OK, ICON_BAD, ICON_RUN, ICON_SKIP, ICON_TODO = _fa.ICON_FA_CHECK, _fa.ICON_FA_XMARK, _fa.ICON_FA_CIRCLE_NOTCH, _fa.ICON_FA_MINUS, _fa.ICON_FA_CIRCLE
ICON_CUR, ICON_WAIT, ICON_WARN = _fa.ICON_FA_CHEVRON_RIGHT, _fa.ICON_FA_HOURGLASS_HALF, _fa.ICON_FA_TRIANGLE_EXCLAMATION
