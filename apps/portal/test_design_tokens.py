"""WCAG AA contrast checks for the design tokens, plus guards that keep colours in tokens.css."""
import re
from pathlib import Path

from django.test import SimpleTestCase

STATIC = Path(__file__).resolve().parent / "static" / "portal"
TEXT, UI = 4.5, 3.0  # WCAG AA: body text, and UI components / state indicators

# (foreground token, background token, minimum ratio, what it is)
PAIRS = [
    ("ink", "bg", TEXT, "body text"), ("ink", "surface", TEXT, "text on cards"),
    ("ink-2", "surface", TEXT, "secondary text"), ("ink", "surface-2", TEXT, "text on tinted panels"),
    ("muted", "bg", TEXT, "muted text"), ("muted", "surface", TEXT, "muted on cards"),
    ("muted", "surface-2", TEXT, "muted on tinted panels"),
    ("accent-text", "surface", TEXT, "links"), ("accent-text", "bg", TEXT, "links on page"),
    ("accent-text", "accent-soft", TEXT, "badges / selected labels"), ("ink", "accent-soft", TEXT, "selected option text"),
    ("on-accent", "accent", TEXT, "primary buttons"), ("on-accent", "accent-strong", TEXT, "button hover"),
    ("on-accent", "hero-from", TEXT, "hero text"), ("on-accent", "hero-to", TEXT, "hero text"),
    ("ok-text", "ok-soft", TEXT, "correct pill"), ("bad-text", "bad-soft", TEXT, "wrong pill"),
    ("warn-text", "warn-soft", TEXT, "warning pill"), ("rev-text", "rev-soft", TEXT, "review pill"),
    ("ink", "ok-soft", TEXT, "correct option text"), ("ink", "bad-soft", TEXT, "wrong option text"),
    ("on-accent", "ok", TEXT, "answered chip"), ("on-accent", "bad", TEXT, "not-answered chip"),
    ("on-accent", "rev", TEXT, "marked chip"), ("on-accent", "warn", TEXT, "timer warning"),
    ("exam-jee-main", "exam-jee-main-soft", TEXT, "JEE Main label"), ("exam-neet", "exam-neet-soft", TEXT, "NEET label"),
    ("exam-jee-advanced", "exam-jee-advanced-soft", TEXT, "JEE Advanced label"),
    ("exam-jee-main", "surface", TEXT, "JEE Main text"), ("exam-neet", "surface", TEXT, "NEET text"),
    ("exam-jee-advanced", "surface", TEXT, "JEE Advanced text"),
    ("on-accent", "exam-jee-main-solid", TEXT, "JEE Main banner"), ("on-accent", "exam-neet-solid", TEXT, "NEET banner"),
    ("on-accent", "exam-jee-advanced-solid", TEXT, "JEE Advanced banner"),
    ("line-strong", "surface", UI, "input / option borders"), ("line-strong", "surface-2", UI, "borders on tinted panels"),
    ("accent", "surface", UI, "selected borders"), ("focus", "surface", UI, "focus ring"), ("focus", "bg", UI, "focus ring on page"),
    ("ok", "surface", UI, "answered chip vs card"), ("bad", "surface", UI, "not-answered chip vs card"),
    ("rev", "surface", UI, "marked chip vs card"), ("warn", "surface", UI, "warning vs card"),
]


def luminance(value):
    value = value.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a, b):
    high, low = sorted([luminance(a), luminance(b)], reverse=True)
    return (high + 0.05) / (low + 0.05)


def themes():
    css = (STATIC / "tokens.css").read_text()
    blocks = {}
    for name, pattern in [("light", r":root,\[data-theme=light\]\{(.*?)\}"), ("dark", r"\[data-theme=dark\]\{(.*?)\}"),
                          ("dark-auto", r":root:not\(\[data-theme=light\]\)\{(.*?)\}")]:
        body = re.search(pattern, css, re.S)[1]
        blocks[name] = dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-fA-F]{6})\b", body))
    return blocks


class DesignTokenTests(SimpleTestCase):
    def test_all_pairs_meet_wcag_aa_in_both_themes(self):
        failures = []
        for theme in ("light", "dark"):
            tokens = themes()[theme]
            for fg, bg, minimum, label in PAIRS:
                ratio = contrast(tokens[fg], tokens[bg])
                if ratio < minimum:
                    failures.append(f"{theme}: {label} ({fg} {tokens[fg]} on {bg} {tokens[bg]}) = {ratio:.2f}, needs {minimum}")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_system_dark_mode_matches_explicit_dark_theme(self):
        blocks = themes()
        self.assertEqual(blocks["dark"], blocks["dark-auto"])

    def test_component_stylesheets_use_tokens_not_raw_colours(self):
        # Translucent white/black overlays on the hero gradient are the only literal colours allowed.
        allowed = re.compile(r"#(?:fff|ffffff|000|0003|ffffff[0-9a-f]{2}|000000[0-9a-f]{2})\b", re.I)
        for name in ("app.css", "vault.css", "academic-blue.css", "ui.css", "exam.css"):
            path = STATIC / name
            if not path.exists():
                continue
            raw = [c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", path.read_text()) if not allowed.fullmatch(c)]
            self.assertEqual(raw, [], f"{name} has hard-coded colours; add a token in tokens.css instead")

    def test_no_text_smaller_than_12px(self):
        for name in ("app.css", "vault.css", "academic-blue.css", "ui.css", "exam.css"):
            path = STATIC / name
            if not path.exists():
                continue
            tiny = re.findall(r"font-size:\s*(?:[0-9]|1[01])(?:\.\d+)?px", path.read_text())
            self.assertEqual(tiny, [], f"{name} has text below 12px")


class BrandAccentTests(SimpleTestCase):
    def test_pale_brand_colours_are_darkened_to_pass_contrast(self):
        from apps.portal.brand import accent_style
        self.assertEqual(accent_style("#1759a6"), "")
        for colour in ("#ffff00", "#7fdbff", "#ff69b4", "#f0f0f0", "#000000"):
            values = dict(part.split(":") for part in accent_style(colour).split(";"))
            self.assertGreaterEqual(contrast("#ffffff", values["--accent"]), TEXT, colour)
            self.assertGreaterEqual(contrast(values["--accent-text"], "#ffffff"), TEXT, colour)
            self.assertGreaterEqual(contrast(values["--accent-text"], values["--accent-soft"]), TEXT, colour)
