"""Guard: every Font Awesome icon used in templates must be defined in the subset CSS.

The shipped subset (`ui/static/vendor/fontawesome/css/fontawesome-subset.min.css`)
contains only the icons Amalthea actually uses. This test parses every
`fa-<name>` token from all HTML templates, filters out style/size/animation
helper classes, and asserts that each remaining icon has a corresponding
`.fa-<name>::before` rule in the subset CSS.

Defined-but-unused icons are allowed (the subset may contain icons reserved
for future features). Used-but-undefined icons fail with a clear message
naming the offending icon(s) and template file(s).
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "ui" / "templates" / "ui"
SUBSET_CSS = (
    Path(__file__).resolve().parents[2]
    / "ui"
    / "static"
    / "vendor"
    / "fontawesome"
    / "css"
    / "fontawesome-subset.min.css"
)

# Matches fa-<name> where name is lowercase alnum + hyphen.
# We intentionally capture the full token (including fa- prefix) for reporting.
_FA_TOKEN_RE = re.compile(r"\bfa-([a-z0-9-]+)\b")

# FA helper classes that are NOT icon names — these are style/size/animation modifiers.
_FA_NON_ICON_CLASSES = {
    # Styles
    "solid",
    "regular",
    "brands",
    "light",
    "duotone",
    "thin",
    # Fixed width
    "fw",
    # Sizing
    "lg",
    "xs",
    "sm",
    "2x",
    "3x",
    "4x",
    "5x",
    "6x",
    "7x",
    "8x",
    "9x",
    "10x",
    # Animation
    "spin",
    "pulse",
    "beat",
    "beat-fade",
    "bounce",
    "flip",
    "shake",
    # Rotate
    "rotate-90",
    "rotate-180",
    "rotate-270",
    "rotate-by",
    # Flip
    "flip-horizontal",
    "flip-vertical",
    "flip-both",
    # Stack
    "stack",
    "stack-1x",
    "stack-2x",
    "inverse",
    # Pull
    "pull-left",
    "pull-right",
    # Border
    "border",
    # List
    "li",
    # Alias (deprecated in FA6 but may appear)
    "fa",
    "fas",
    "far",
    "fab",
    "fal",
    "fad",
    "fat",
}


def _read_css_icons() -> set[str]:
    """Extract all icon names that have a ::before rule in the subset CSS."""
    css_text = SUBSET_CSS.read_text(encoding="utf-8")
    # Match .fa-<name>::before { content: "..."; }
    # The icon name is the part after .fa- and before ::before
    icons = set()
    for match in re.finditer(r"\.fa-([a-z0-9-]+)::before", css_text):
        icons.add(match.group(1))
    return icons


def _collect_template_icons() -> dict[str, set[str]]:
    """Return a dict of {template_file: set(icon_names)} for all icons used in templates."""
    result: dict[str, set[str]] = defaultdict(set)
    for template_path in TEMPLATE_DIR.glob("*.html"):
        text = template_path.read_text(encoding="utf-8")
        # Strip Django comments so we don't match icons inside comments
        text = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "", text, flags=re.DOTALL)
        text = re.sub(r"{#.*?#}", "", text, flags=re.DOTALL)
        for match in _FA_TOKEN_RE.finditer(text):
            icon_name = match.group(1)
            if icon_name not in _FA_NON_ICON_CLASSES:
                result[template_path.name].add(icon_name)
    return result


def test_subset_css_is_readable() -> None:
    """Sanity: the subset CSS file exists and contains at least some icons."""
    icons = _read_css_icons()
    assert icons, "no icons parsed from subset CSS — file may be empty or malformed"
    # Spot-check a few known icons
    assert "house" in icons
    assert "bell" in icons
    assert "shield-halved" in icons


def test_all_template_icons_defined_in_subset() -> None:
    """Every icon used in templates (excluding helpers) must have a ::before rule in the subset."""
    defined_icons = _read_css_icons()
    template_icons = _collect_template_icons()

    # Build a flat list of (icon, template_file) for all used icons
    used: list[tuple[str, str]] = []
    for template_file, icons in template_icons.items():
        for icon in icons:
            used.append((icon, template_file))

    # Find missing icons
    missing: list[tuple[str, str]] = [
        (icon, tmpl) for icon, tmpl in used if icon not in defined_icons
    ]

    if missing:
        # Group by icon for a cleaner message
        by_icon: dict[str, list[str]] = defaultdict(list)
        for icon, tmpl in missing:
            by_icon[icon].append(tmpl)

        msg_lines = [
            "The following Font Awesome icons are used in templates but NOT defined in the subset CSS:"
        ]
        for icon, files in sorted(by_icon.items()):
            msg_lines.append(f"  fa-{icon}: {', '.join(sorted(set(files)))}")
        msg_lines.append("")
        msg_lines.append(
            f"Defined icons in subset ({len(defined_icons)}): {', '.join(sorted(defined_icons))}"
        )
        msg_lines.append("")
        msg_lines.append(
            "Add missing .fa-<name>::before rules to ui/static/vendor/fontawesome/css/fontawesome-subset.min.css"
        )
        pytest.fail("\n".join(msg_lines))


def test_no_undefined_icons_in_base_html() -> None:
    """Explicit check for base.html since it contains the rail — most visible icons."""
    defined_icons = _read_css_icons()
    template_icons = _collect_template_icons()

    base_icons = template_icons.get("base.html", set())
    missing = [icon for icon in base_icons if icon not in defined_icons]
    assert not missing, (
        f"base.html uses undefined icons: {', '.join(f'fa-{i}' for i in sorted(missing))}"
    )


# --- AC-15: icon-only controls must carry an accessible name --------------------------------------

# An "icon-only" control is an <a> or <button> whose *only element content* is an <i> icon (the
# icon is the clickable surface). Such a control has no visible text, so it must carry an
# accessible name (aria-label, or title) and the icon itself must be aria-hidden.
_ICON_ONLY_RE = re.compile(
    r"<(a|button)\b([^>]*?)>\s*<i\b([^>]*?)>\s*</i>\s*</\1>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(r"([a-zA-Z-]+)\s*=\s*\"([^\"]*)\"")


def _attrs(attr_text: str) -> dict[str, str]:
    return {name.lower(): value for name, value in _ATTR_RE.findall(attr_text)}


def _iter_icon_only_controls():
    """Yield (template_name, tag, attrs, icon_attrs) for every icon-only control in templates."""
    for template_path in sorted(TEMPLATE_DIR.glob("*.html")):
        text = template_path.read_text(encoding="utf-8")
        text = re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "", text, flags=re.DOTALL)
        text = re.sub(r"{#.*?#}", "", text, flags=re.DOTALL)
        for match in _ICON_ONLY_RE.finditer(text):
            tag = match.group(1).lower()
            ctrl_attrs = _attrs(match.group(2))
            icon_attrs = _attrs(match.group(3))
            yield template_path.name, tag, ctrl_attrs, icon_attrs


def test_icon_only_controls_have_accessible_names() -> None:
    """AC-15 — every icon-only control has an accessible name and a hidden icon.

    A control whose only content is an icon is invisible to assistive tech unless it carries an
    accessible name (`aria-label`, or a `title` fallback). The decorative icon must also be
    `aria-hidden="true"` so it is not announced. This is the mechanical form of AGENTS.md §6 and
    guards the D6 rule ("icon-only is restricted to secondary row actions, with an accessible
    name") against regressions like the §12.5-fu1 modality bug.
    """
    missing_name: list[str] = []
    not_hidden: list[str] = []

    for template, tag, ctrl_attrs, icon_attrs in _iter_icon_only_controls():
        label = ctrl_attrs.get("aria-label") or ctrl_attrs.get("title")
        if not label or not label.strip():
            missing_name.append(f"{template}: <{tag} …> has no aria-label/title")
        if icon_attrs.get("aria-hidden") != "true":
            not_hidden.append(f"{template}: <{tag} …> icon is not aria-hidden")

    assert not missing_name, (
        "Icon-only controls with no accessible name (add aria-label or title):\n  "
        + "\n  ".join(missing_name)
    )
    assert not not_hidden, (
        'Icon-only controls whose icon is not aria-hidden="true" (decorative icons must be '
        "hidden from assistive tech):\n  " + "\n  ".join(not_hidden)
    )


def test_icon_only_controls_are_actually_present() -> None:
    """Sanity — the AC-15 extractor must find icon-only controls, else the guard is vacuous.

    Phase D converts the ubiquitous secondary row actions (Edit/Delete/View) to icon-only
    controls, so there must be some. An empty set means the regex silently stopped matching (the
    exact way an a11y guard goes quietly dead), so this fails loudly rather than passing green.
    """
    found = list(_iter_icon_only_controls())
    assert found, (
        "no icon-only controls were detected in templates — the AC-15 extractor is broken or the "
        "icon-only row actions are missing."
    )
