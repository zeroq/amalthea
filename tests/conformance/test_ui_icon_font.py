"""Guard: every Font Awesome icon used in a template must actually render.

Two ways the icon set silently broke once (found 2026-10-17, Safari drawing hollow
tofu boxes):

1. **The style class never set `font-family`.** The templates write icons as
   ``<i class="fa-solid fa-pen">``. The hand-written subset stylesheet applied
   ``font-family`` only to ``.fa, .fas, .far, .fab`` — never to ``.fa-solid`` /
   ``.fa-regular`` / ``.fa-brands`` — so the element inherited the page font and the
   Font Awesome codepoints (Private Use Area, ``U+F000``+) rendered as ``.notdef``.
   Safari paints that as a visible box; other browsers often paint nothing.
2. **A malformed rule swallowed later ones.** ``.fa-calendar-year::before`` was left
   unclosed on the last line, so ``.fa-comments`` / ``.fa-list-check`` /
   ``.fa-paperclip`` — written after it as nested text — were dropped by the CSS
   parser and lost their ``::before`` content.

A source scan has to match the *parsed* structure, not raw text: the swallowed rules
were present as text but nested inside another block, and the stylesheet's brace count
stayed balanced. So this parses top-level rules and asserts against those.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "ui" / "templates" / "ui"
CSS_PATH = (
    Path(__file__).resolve().parents[2]
    / "ui"
    / "static"
    / "vendor"
    / "fontawesome"
    / "css"
    / "fontawesome-subset.min.css"
)

# Classes that select an icon *style* (they carry the weight/family custom properties).
STYLE_CLASSES = {"fa", "fas", "far", "fab", "fa-solid", "fa-regular", "fa-brands"}

_ICON_CLASS = re.compile(r"fa-[a-z0-9-]+")
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _top_level_rules(css: str) -> list[tuple[str, str]]:
    """Split a stylesheet into ``(selector, body)`` pairs for top-level rules only.

    Nested blocks are kept *inside* their parent's body, which is the point: a rule
    that was accidentally nested (see module docstring) is not reported as top-level.
    """
    css = _COMMENT.sub("", css)
    rules: list[tuple[str, str]] = []
    i = 0
    while (brace := css.find("{", i)) != -1:
        selector = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        rules.append((selector, css[brace + 1 : j - 1]))
        i = j
    return rules


def _class_tokens() -> dict[str, set[str]]:
    """Every ``fa-*`` class token used across the templates, split by kind."""
    icon: set[str] = set()
    style: set[str] = set()
    for path in TEMPLATE_DIR.glob("*.html"):
        for attr in re.finditer(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            for token in attr.group(1).split():
                if not _ICON_CLASS.fullmatch(token):
                    continue
                (style if token in STYLE_CLASSES else icon).add(token)
    return {"icon": icon, "style": style}


def test_icon_classes_are_discovered() -> None:
    # A guard that silently finds nothing is worse than no guard.
    tokens = _class_tokens()
    assert tokens["icon"], "no `fa-<name>` icon classes found in any template"
    assert "fa-solid" in tokens["style"], "expected the icons to use a `fa-solid` style class"


def test_stylesheet_has_no_nested_rule_blocks() -> None:
    nested = [
        sel for sel, body in _top_level_rules(CSS_PATH.read_text(encoding="utf-8")) if "{" in body
    ]
    assert nested == [], (
        "these top-level rules contain a nested `{` block, so the parser will discard the "
        f"inner rules (a missing `}}` is the usual cause): {nested}"
    )


def test_every_used_icon_class_has_a_content_rule() -> None:
    rules = _top_level_rules(CSS_PATH.read_text(encoding="utf-8"))
    defined = {
        name
        for sel, body in rules
        for name in re.findall(r"\.(fa-[a-z0-9-]+)::before", sel)
        if "content" in body
    }
    missing = sorted(_class_tokens()["icon"] - defined)
    assert missing == [], (
        f"templates use icon classes with no `::before {{ content }}` rule in the subset "
        f"stylesheet, so they render blank: {missing}"
    )


def test_icon_style_classes_apply_the_font_family() -> None:
    rules = _top_level_rules(CSS_PATH.read_text(encoding="utf-8"))
    font_selectors = [sel for sel, body in rules if "font-family" in body]
    for token in sorted(_class_tokens()["style"]):
        # The class must be named in a selector that also sets font-family (the `@font-face`
        # block declares font-family too, but its selector is `@font-face`, so it never counts).
        pattern = re.compile(r"\." + re.escape(token) + r"(?![\w-])")
        assert any(pattern.search(sel) for sel in font_selectors), (
            f"`.{token}` is used on an icon element but no rule that sets `font-family` names "
            f"it, so the element inherits the page font and the icon renders as tofu"
        )
