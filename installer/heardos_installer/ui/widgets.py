"""Small building blocks so the screens read like forms, not like ImGui calls."""
from __future__ import annotations

from typing import Optional

from imgui_bundle import imgui

from .theme import C, ICON_BAD, ICON_OK, ICON_WAIT


def heading(text: str, sub: str = "") -> None:
    imgui.push_style_color(imgui.Col_.text, C["ORANGE"])
    imgui.text(text.upper())
    imgui.pop_style_color()
    if sub:
        imgui.push_text_wrap_pos(imgui.get_content_region_avail().x + imgui.get_cursor_pos_x() - 10)
        imgui.text_colored(C["MUTED"], sub)
        imgui.pop_text_wrap_pos()
    imgui.spacing()


def title(text: str) -> None:
    imgui.push_font(None, imgui.get_font_size() * 1.45)
    imgui.text(text)
    imgui.pop_font()


def help_text(text: str) -> None:
    imgui.push_text_wrap_pos(0)
    imgui.text_colored(C["MUTED"], text)
    imgui.pop_text_wrap_pos()


def field(label: str, value: str, hint: str = "", password: bool = False, width: float = 420, key: str = "") -> str:
    imgui.text(label)
    imgui.set_next_item_width(width)
    flags = imgui.InputTextFlags_.password if password else 0
    _, value = imgui.input_text_with_hint(f"##{key or label}", hint, value or "", flags)
    return value


def primary(label: str, enabled: bool = True, width: float = 0) -> bool:
    imgui.push_style_color(imgui.Col_.button, C["ORANGE"])
    imgui.push_style_color(imgui.Col_.button_hovered, C["ORANGE_LIGHT"])
    imgui.push_style_color(imgui.Col_.button_active, C["ORANGE_DEEP"])
    imgui.push_style_color(imgui.Col_.text, C["INK"])
    if not enabled:
        imgui.begin_disabled()
    clicked = imgui.button(label, imgui.ImVec2(width, 0))
    if not enabled:
        imgui.end_disabled()
    imgui.pop_style_color(4)
    return clicked and enabled


def secondary(label: str, enabled: bool = True) -> bool:
    if not enabled:
        imgui.begin_disabled()
    clicked = imgui.button(label)
    if not enabled:
        imgui.end_disabled()
    return clicked and enabled


def status_line(ok: Optional[bool], text: str) -> None:
    if ok is None:
        imgui.text_colored(C["MUTED"], ICON_WAIT + "  " + text)
    elif ok:
        imgui.text_colored(C["GOOD"], ICON_OK + "  " + text)
    else:
        imgui.push_text_wrap_pos(0)
        imgui.text_colored(C["BAD"], ICON_BAD + "  " + text)
        imgui.pop_text_wrap_pos()


def spinner_text(text: str) -> None:
    dots = "." * (1 + int(imgui.get_time() * 3) % 3)
    imgui.text_colored(C["MUTED"], f"{ICON_WAIT}  {text.rstrip('....')}{dots}")


def card_begin(id_: str, height: float = 0) -> None:
    imgui.begin_child(id_, imgui.ImVec2(0, height), imgui.ChildFlags_.auto_resize_y | imgui.ChildFlags_.always_use_window_padding)


def card_end() -> None:
    imgui.end_child()


def chip(text: str, color: str = "SLATE", text_color: str = "LIGHT") -> None:
    dl = imgui.get_window_draw_list()
    p = imgui.get_cursor_screen_pos()
    sz = imgui.calc_text_size(text)
    pad = imgui.ImVec2(8, 3)
    dl.add_rect_filled(p, imgui.ImVec2(p.x + sz.x + pad.x * 2, p.y + sz.y + pad.y * 2), imgui.get_color_u32(C[color]), 10)
    imgui.set_cursor_screen_pos(imgui.ImVec2(p.x + pad.x, p.y + pad.y))
    imgui.text_colored(C[text_color], text)
    imgui.set_cursor_screen_pos(imgui.ImVec2(p.x + sz.x + pad.x * 2 + 6, p.y))
    imgui.dummy(imgui.ImVec2(0, sz.y + pad.y * 2))
