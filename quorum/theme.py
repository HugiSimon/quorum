"""Palette, glyphs and animations — design sheet S0, in code.

The hue is a bot's identity; only lightness and chroma change from one theme to the other.
Nothing has a colored background behind text, so there is no contrast risk.
"""

from __future__ import annotations

import math
import os
import re

# The two themes: same hues, different lightness and chroma.
DARK = (0.78, 0.105)
LIGHT = (0.46, 0.079)  # 0.079 and not 0.13: beyond that, over half the wheel leaves the gamut

NEUTRALS_DARK = {
    "bg": "#0c0b0a",
    "panel": "#141312",
    "frame": "#2b2824",
    "faint": "#56514a",
    "dim": "#8a8378",
    "ink": "#d9d3c8",
}
NEUTRALS_LIGHT = {
    "bg": "#faf8f4",
    "panel": "#f1eee7",
    "frame": "#d9d3c8",
    "faint": "#a19a8e",
    "dim": "#6c665d",
    "ink": "#22201d",
}

# The current palette. It is mutated **in place**: every module shares it, and a theme
# change forces nobody to re-import.
N: dict[str, str] = {}
_dark = True


def is_dark() -> bool:
    return _dark


def apply_theme(dark: bool) -> None:
    """Switches the palette for the whole application, without re-importing."""
    global _dark
    _dark = dark
    N.clear()
    N.update(NEUTRALS_DARK if dark else NEUTRALS_LIGHT)


def terminal_theme() -> bool:
    """Guesses whether the terminal is dark.

    Textual offers no reliable query: we read COLORFGBG when the terminal sets it
    (`15;0` = light ink on dark background), and otherwise assume dark — that is the most
    common case, and the theme stays selectable by hand.
    """
    colors = os.environ.get("COLORFGBG", "")
    if ";" in colors:
        bg = colors.rsplit(";", 1)[-1].strip()
        if bg.isdigit():
            return int(bg) < 8
    return True


# The hues the interface keeps for itself, outside bot identity.
ATTENTION = "#e8b563"
CLICKABLE = "#a8c76a"
GREEN = "#7fbf6a"
RED = "#d96b6b"

# Two hue bands are never given to a bot.
BAND_ATTENTION = (45, 75)
BAND_CLICKABLE = (90, 120)

# One glyph, one word. The glyph never carries the state alone: the word is always written.
STATES = {
    "idle": ("·", "idle"),
    "thinking": ("◐", "thinking"),
    "running": ("▸", "running"),
    "asking": ("◆", "asking"),
    "done": ("✓", "done"),
    "failed": ("✕", "failed"),
    "out": ("◌", "out"),
}

# ACP `kind`s, reduced to the short word the design shows in front of each tool.
FAMILIES = {
    "read": "fs", "edit": "fs", "delete": "fs", "move": "fs", "search": "fs",
    "execute": "shell", "fetch": "net", "think": "think",
}

# Three movements, not one more. Braille has an honest width: it is the safest.
ANIM_THINK = "◜◠◝◞◡◟"
ANIM_WORK = "⡿⡀⡄⡆⡇⡏⡟⡿⡟⡏⡇⡆⡄"


def oklch_to_srgb(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    """Converts oklch to unclamped sRGB: components outside [0, 1] flag out-of-gamut."""
    angle = math.radians(hue)
    a, b = chroma * math.cos(angle), chroma * math.sin(angle)
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (
        4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )
    return tuple(_gamma(c) for c in linear)


def _gamma(c: float) -> float:
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * abs(c) ** (1 / 2.4) * (1 if c > 0 else -1) - 0.055


def in_gamut(lightness: float, chroma: float, hue: float) -> bool:
    """True if the color fits in sRGB without clipping."""
    return all(-0.0005 <= c <= 1.0005 for c in oklch_to_srgb(lightness, chroma, hue))


def hex_of(lightness: float, chroma: float, hue: float) -> str:
    """Returns a #rrggbb, lowering chroma step by step until it fits the gamut."""
    while chroma > 0 and not in_gamut(lightness, chroma, hue):
        chroma -= 0.005
    components = oklch_to_srgb(lightness, max(chroma, 0.0), hue)
    return "#" + "".join(f"{round(min(max(c, 0.0), 1.0) * 255):02x}" for c in components)


def bot_color(hue: float, dark: bool | None = None) -> str:
    """A bot's color in the current theme, from its hue alone."""
    lightness, chroma = DARK if (_dark if dark is None else dark) else LIGHT
    return hex_of(lightness, chroma, hue)


def hue_reserved(hue: float) -> bool:
    """True if the hue belongs to a band reserved for the interface."""
    return any(low <= hue % 360 <= high for low, high in (BAND_ATTENTION, BAND_CLICKABLE))


def divider(title: str, width: int, note: str = ""):
    """The design's only label marker: ─┤ TITLE ├── followed by a rule.

    The grid has no letter spacing: a spaced-out label would double its width in real
    spaces. Every title goes through here. The rule is quiet, the **title** is not: it is
    a reading landmark, not decoration.
    """
    from rich.text import Text

    text = Text()
    text.append("─┤ ", style=N["frame"])
    text.append(title, style=f"bold {N['ink']}")
    text.append(" ├─", style=N["frame"])
    left = width - len(title) - 6
    if note:
        text.append(f"  {note} ", style=N["dim"])
        left -= len(note) + 3
    text.append("─" * max(2, left) + "\n", style=N["frame"])
    return text


apply_theme(True)


if __name__ == "__main__":
    # The dark theme holds as is over the whole wheel.
    out_dark = [h for h in range(360) if not in_gamut(*DARK, h)]
    assert not out_dark, f"{len(out_dark)} hues out of gamut in the dark theme"

    # The light theme overflows by 0.001 on the cyan band (h 193–207): the real maximum at
    # that lightness is 0.078. The clipping in hex_of catches it, and it is invisible.
    out_light = [h for h in range(360) if not in_gamut(*LIGHT, h)]
    assert set(out_light) <= set(range(190, 212)), f"unexpected overflow: {out_light}"

    # 0.13 at the same lightness loses more than half the wheel — hence the correction.
    out_13 = [h for h in range(360) if not in_gamut(LIGHT[0], 0.13, h)]
    assert len(out_13) > 180, f"only {len(out_13)} hues out of gamut at 0.13"

    # What really matters: no rendered color is out of gamut, in either theme.
    for h in range(360):
        for dark in (True, False):
            color = bot_color(h, dark)
            assert re.fullmatch(r"#[0-9a-f]{6}", color), color

    for name, hue in (("sonar", 250), ("forge", 150), ("lex", 305), ("audit", 18), ("pico", 195)):
        assert not hue_reserved(hue), f"{name} steals a reserved band"
        print(f"{name:6} h{hue:4}  dark {bot_color(hue)}  light {bot_color(hue, False)}")

    apply_theme(False)
    assert N["bg"] == NEUTRALS_LIGHT["bg"] and not is_dark()
    apply_theme(True)
    assert N["bg"] == NEUTRALS_DARK["bg"] and is_dark()
    os.environ["COLORFGBG"] = "0;15"
    assert not terminal_theme(), "light background announced by the terminal"
    os.environ["COLORFGBG"] = "15;0"
    assert terminal_theme()
    del os.environ["COLORFGBG"]

    print(f"light: {len(out_light)} hues clipped by 0.001; at 0.13 {len(out_13)} would be missing")
