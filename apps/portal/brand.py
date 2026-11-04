"""Turn an institute's brand colour into accessible accent tokens."""
import re

DEFAULT = "#1759a6"


def _luminance(rgb):
    channels = [c / 255 for c in rgb]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_with_white(rgb):
    return 1.05 / (_luminance(rgb) + 0.05)


def _hex(rgb):
    return "#" + "".join(f"{round(c):02x}" for c in rgb)


def _darken_until(rgb, minimum):
    rgb = list(rgb)
    while _contrast_with_white(rgb) < minimum and any(rgb):
        rgb = [c * 0.94 for c in rgb]
    return rgb


def accent_style(colour):
    """Inline CSS custom properties for a tenant colour, or '' for the default palette.

    Buttons need 4.5:1 for white text; links need 4.5:1 on white. Pale brand colours are
    darkened until they pass, so any colour an admin picks stays readable.
    """
    if not colour or not re.fullmatch(r"#[0-9a-fA-F]{6}", colour) or colour.lower() == DEFAULT:
        return ""
    rgb = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    accent = _darken_until(rgb, 4.6)
    strong = _darken_until([c * 0.82 for c in accent], 6.0)
    soft = [255 - (255 - c) * 0.1 for c in accent]
    return (f"--accent:{_hex(accent)};--accent-strong:{_hex(strong)};--accent-text:{_hex(strong)};"
            f"--accent-soft:{_hex(soft)};--focus:{_hex(accent)}")
