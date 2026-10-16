"""Palette from one accent colour. Neutrals stay the Hearth Crest charcoals; the accent gets a deeper and a lighter shade
and a readable text colour for buttons."""
from __future__ import annotations

import colorsys
from dataclasses import dataclass

DEFAULT_PRIMARY = "#f2a35e"          # Hearth Crest orange
INK, CHARCOAL, SLATE, LIGHT = "#1f2023", "#26272b", "#3a3b40", "#ececec"

# Homepage only accepts Tailwind colour names; pick the nearest by hue.
_TAILWIND = {"red": 0, "orange": 25, "amber": 38, "yellow": 50, "lime": 80, "green": 140, "emerald": 155, "teal": 172,
             "cyan": 188, "sky": 200, "blue": 217, "indigo": 235, "violet": 258, "purple": 270, "fuchsia": 292, "pink": 330, "rose": 350}


def _rgb(h: str) -> tuple[float, float, float]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise ValueError(f"not a colour: {h!r} (use #RRGGBB)")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _shade(h: str, dl: float) -> str:
    hh, l, s = colorsys.rgb_to_hls(*_rgb(h))
    return _hex(colorsys.hls_to_rgb(hh, max(0.0, min(1.0, l + dl)), s))


def luminance(h: str) -> float:
    def ch(c): return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@dataclass(frozen=True)
class Palette:
    primary: str
    deep: str
    light: str
    on_primary: str
    homepage_color: str
    ink: str = INK
    charcoal: str = CHARCOAL
    slate: str = SLATE
    text_light: str = LIGHT

    def rgb(self, name: str) -> tuple[int, int, int]:
        return tuple(round(c * 255) for c in _rgb(getattr(self, name)))  # type: ignore[return-value]


def derive(primary: str = DEFAULT_PRIMARY) -> Palette:
    p = _hex(_rgb(primary))
    if p == DEFAULT_PRIMARY:
        deep, light = "#ed8325", "#f5b984"     # the hand-picked Hearth Crest shades
    else:
        deep, light = _shade(p, -0.10), _shade(p, +0.08)
    on = INK if contrast(p, INK) >= contrast(p, "#ffffff") else "#ffffff"
    hue = colorsys.rgb_to_hls(*_rgb(p))[0] * 360
    _, l, s = colorsys.rgb_to_hls(*_rgb(p))
    name = "slate" if s < 0.15 else min(_TAILWIND, key=lambda k: min(abs(_TAILWIND[k] - hue), 360 - abs(_TAILWIND[k] - hue)))
    return Palette(p, deep, light, on, name)
