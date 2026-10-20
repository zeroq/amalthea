"""Regression guard: the stylesheet must not reference undefined theme tokens.

An undefined custom property inside ``var()`` is the silent failure mode of this stylesheet: the
declaration becomes *invalid at computed-value time*, so the whole ``border``/``background``/
``color``/``box-shadow`` is dropped with no error anywhere. It has shipped twice as a real defect —
the D3 Markdown block referenced ``--color-surface-elevated``, and the P3 procedures block
referenced seven invented names (``--color-border``, ``--color-fg``, ``--color-fg-muted``,
``--color-bg-elevated``, ``--color-primary``, ``--color-primary-focus``, ``--color-danger``), which
turned ATT&CK-ID chips into plain text and removed every border.

The check is deliberately scoped to ``--color-*``: Tailwind's own ``--tw-*`` properties are declared
with ``@property`` (and are not part of our theme), and the ``--font-*-font-feature-settings``
brokers it emits are Tailwind's business, not ours.

The second guard — no ``outline: none`` — protects the keyboard focus indicator (WCAG 2.4.7). The
house pattern is the global ``:focus-visible { outline: 2px solid var(--color-accent) }``; a rule
that sets ``outline: none`` and relies on a colour-only cue fails the "never signal by colour alone"
rule in AGENTS.md §6.

The third guard — tab strips must not show a scrollbar — pins the ``.tabs`` scrollbar suppression,
which counteracts the deliberate 1px active-tab overlap that would otherwise draw a bar on every
strip (see the test for the full explanation).

The fourth guard — cheat-sheet modal must have correct styling — ensures the ``dialog.cheatsheet-modal``
rule exists with its critical declarations (``max-width:400px``, ``overflow:auto``). The selector
was accidentally stripped during the Phase A split, causing the modal to render without border,
background, or scrolling.

The fifth guard — filter-bar text input must have correct sizing — ensures a rule targeting the
filter input (``.filter-bar input[type="text"]`` or minified equivalent) exists with ``flex:1``
and ``max-width:300px``. A duplicate unquoted selector and a dead group selector caused the rule
to vanish entirely after the Phase A split.

The sixth guard (AC-9) — layout-standardization components are defined — asserts that the CSS
classes *this wave owns* (the ``.control-row`` utility it introduces) are backed by a real rule in
the stylesheet, so a template can reference them and render correctly. See
``test_wave_layout_components_are_defined`` for why the guard is deliberately *scoped* rather than a
blanket "every referenced class is defined" check: a blanket check fails today on a large backlog of
*pre-existing* undefined component classes (the ``modal-*`` and ``token-*`` families, etc.), whose
remediation is a separate design wave — not something to paper over with invented rules (which is
exactly how an earlier attempt at this guard went wrong). The backlog is reported as a non-fatal
warning instead.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

STYLESHEET = Path(__file__).resolve().parents[2] / "ui" / "static" / "ui" / "app.css"
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "ui" / "templates"

DEFINED_RE = re.compile(r"(--color-[a-zA-Z0-9-]+)\s*:")
USED_RE = re.compile(r"var\(\s*(--color-[a-zA-Z0-9-]+)\s*[,)]")
OUTLINE_NONE_RE = re.compile(r"outline\s*:\s*(?:none|0)\b")

# --- AC-9 helpers ------------------------------------------------------------------------------

#: Component classes introduced/owned by the layout-standardization wave. Each must be defined in
#: ``app.css`` (a template referencing it depends on the rule existing). `.control-row` is the
#: neutral control-group utility this wave adds (`.case-actions`/`.filter-bar` are kept as aliases).
WAVE_COMPONENT_CLASSES = frozenset(
    {
        "control-row",
        "case-actions",  # one-wave alias — kept so the rename is reviewable
        "filter-bar",  # one-wave alias
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_ATTR_RE = re.compile(r'class="([^"]*)"')
_TEMPLATE_EXPR_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)

#: Django tag/context words that appear inside class attributes and are not CSS classes.
_TEMPLATE_KEYWORDS = frozenset(
    {
        "if",
        "else",
        "elif",
        "endif",
        "not",
        "for",
        "endfor",
        "in",
        "and",
        "or",
        "with",
        "as",
        "block",
        "endblock",
        "scope",
        "value",
        "True",
        "False",
        "None",
    }
)

#: Tailwind utility prefixes/shapes — these are Tailwind's business, not project component classes.
_TAILWIND_PREFIXES = (
    "text-",
    "bg-",
    "border-",
    "ring-",
    "shadow",
    "outline-",
    "divide-",
    "placeholder-",
    "accent-",
    "caret-",
    "fill-",
    "stroke-",
    "p-",
    "px-",
    "py-",
    "pt-",
    "pb-",
    "pl-",
    "pr-",
    "m-",
    "mx-",
    "my-",
    "mt-",
    "mb-",
    "ml-",
    "mr-",
    "w-",
    "h-",
    "max-w-",
    "min-w-",
    "max-h-",
    "min-h-",
    "gap-",
    "space-",
    "inset-",
    "top-",
    "bottom-",
    "left-",
    "right-",
    "z-",
    "opacity-",
    "cursor-",
    "leading-",
    "tracking-",
    "font-",
    "rounded",
    "duration-",
    "delay-",
    "ease-",
    "order-",
    "basis-",
    "grow",
    "shrink",
    "col-",
    "row-",
    "grid-cols-",
    "grid-rows-",
    "translate-",
    "rotate-",
    "scale-",
    "skew-",
    "origin-",
    "transition",
    "animate-",
    "backdrop-",
    "aspect-",
    "object-",
    "overflow-",
    "whitespace-",
    "break-",
    "list-",
    "indent-",
    "align-",
    "justify-",
    "items-",
    "content-",
    "self-",
    "place-",
    "flex-",
    "from-",
    "via-",
    "to-",
    "hover:",
    "focus:",
)
_TAILWIND_SIMPLE = frozenset(
    {
        "sr-only",
        "absolute",
        "relative",
        "fixed",
        "sticky",
        "block",
        "inline",
        "inline-block",
        "inline-flex",
        "flex",
        "grid",
        "hidden",
        "table",
        "contents",
        "truncate",
        "underline",
        "italic",
        "uppercase",
        "lowercase",
        "capitalize",
        "group",
        "peer",
        "prose",
        "full",
        "auto",
        "none",
    }
)


def _stylesheet() -> str:
    return STYLESHEET.read_text(encoding="utf-8")


def _defined_class_selectors(css: str) -> set[str]:
    """Every class token that appears as a selector in the stylesheet."""
    defined: set[str] = set()
    for match in re.finditer(r"([^{}]+)\{", css):
        for selector in match.group(1).split(","):
            defined.update(re.findall(r"\.([A-Za-z0-9_-]+)", selector))
    return defined


def _referenced_literal_classes() -> set[str]:
    """Literal class tokens referenced from ``class="…"`` attributes across templates.

    Template expressions (``{{ … }}`` / ``{% … %}``) are removed first, so dynamic fragments such as
    ``sev-{{ sev_class }}`` do not leak a bogus ``sev-`` token. Tokens that are an incomplete
    dynamic prefix (ending in ``-``) and known Django keywords are dropped.
    """
    found: set[str] = set()
    for template in TEMPLATE_DIR.rglob("*.html"):
        for match in _ATTR_RE.finditer(template.read_text(encoding="utf-8")):
            cleaned = _TEMPLATE_EXPR_RE.sub(" ", match.group(1))
            for token in _TOKEN_RE.findall(cleaned):
                if token.endswith("-") or token in _TEMPLATE_KEYWORDS:
                    continue
                found.add(token)
    return found


def _is_tailwind_utility(cls: str) -> bool:
    if cls in _TAILWIND_SIMPLE:
        return True
    return any(cls.startswith(prefix) for prefix in _TAILWIND_PREFIXES)


def test_every_color_token_used_is_defined() -> None:
    css = _stylesheet()
    defined = set(DEFINED_RE.findall(css))
    used = set(USED_RE.findall(css))
    missing = sorted(used - defined)

    assert not missing, (
        "app.css references undefined --color-* tokens: "
        + ", ".join(missing)
        + " — an undefined var() drops the whole declaration silently. "
        "Use the theme tokens (--color-text, --color-text-dim, --color-line, --color-bg, "
        "--color-bg-raised, --color-bg-inset, --color-accent, --color-err, …)."
    )


def test_no_rule_removes_the_focus_outline() -> None:
    offenders = [line.strip() for line in _stylesheet().split("}") if OUTLINE_NONE_RE.search(line)]
    assert not offenders, (
        "app.css sets `outline: none`, which removes the keyboard focus indicator: "
        f"{offenders!r}. Keep the global `:focus-visible` outline, or pair the rule with an "
        "explicit non-colour focus style."
    )


def test_tab_strips_do_not_show_a_scrollbar() -> None:
    """Guard against the "every tab strip has a scrollbar" regression.

    The active tab (``.tab.tab-on``) is deliberately 1px taller than the strip's content box: its
    ``margin-bottom: -1px`` is what paints its background over the strip's bottom border, producing
    the "connected to the panel below" look (``case_detail.html``). ``.tabs`` also sets
    ``overflow-x: auto``, which makes ``overflow-y`` compute to ``auto`` as well — so that 1px
    registers as vertical scroll overflow and, because this stylesheet styles ``::-webkit-scrollbar``
    (forcing classic, non-overlay bars), a 10px scrollbar was drawn on the right of *every* strip,
    including the two-tab settings one.

    The strip must keep suppressing its scrollbar chrome. Suppressing the *overflow* instead
    (``overflow-y: hidden``) would clip the active tab's 1px and break the connection, so the fix is
    to hide the bar, not the overflow — hence this asserts the chrome-suppression, not clipping.
    """
    css = _stylesheet()
    assert re.search(r"\.tabs\s*\{[^}]*scrollbar-width:\s*none", css), (
        "`.tabs` must set `scrollbar-width: none` — otherwise the active tab's 1px overlap puts a "
        "scrollbar on every tab strip (Firefox)."
    )
    assert re.search(r"\.tabs::-webkit-scrollbar\s*\{[^}]*display:\s*none", css), (
        "`.tabs::-webkit-scrollbar { display: none }` is required to hide the scrollbar in "
        "Chrome/Safari/Edge, where this stylesheet forces classic (non-overlay) scrollbars."
    )


def test_cheatsheet_modal_has_correct_styling() -> None:
    """Guard against loss of the cheat-sheet modal rule (Regression 1).

    The ``dialog.cheatsheet-modal`` rule must exist with its critical declarations.
    The Phase A split accidentally stripped the ``dialog`` type selector, causing the modal to
    render without border, background, max-height constraint, or scrolling.
    """
    css = _stylesheet()
    # Find the dialog.cheatsheet-modal rule (minifier may normalize whitespace)
    rule_match = re.search(r"dialog\.cheatsheet-modal\s*\{([^}]+)\}", css)
    assert rule_match, (
        "`dialog.cheatsheet-modal` rule is missing from app.css — the cheat-sheet modal will have "
        "no border, background, max-width, max-height, or overflow styling."
    )
    declarations = rule_match.group(1)
    # Normalize whitespace for robust checking
    normalized = re.sub(r"\s+", " ", declarations)
    assert "max-width:400px" in normalized.replace(" ", ""), (
        f"`dialog.cheatsheet-modal` missing `max-width:400px` declaration. Found: {declarations!r}"
    )
    assert "overflow:auto" in normalized.replace(" ", ""), (
        f"`dialog.cheatsheet-modal` missing `overflow:auto` declaration. Found: {declarations!r}"
    )


def test_filter_bar_text_input_has_correct_sizing() -> None:
    """Guard against loss of the filter-bar text input rule (Regression 2).

    A rule targeting the filter text input must exist with ``flex:1`` and ``max-width:300px``.
    Accept the minified selector form (``.filter-bar input[type=text]`` or
    ``.filter-bar input[type='text']``) — the point is that the rule exists and a quoted-selector
    input is covered. The Phase A split introduced a dead unquoted duplicate and a group selector
    that caused the compiled rule to vanish.
    """
    css = _stylesheet()
    # Match either quoted or unquoted attribute selector (minifier normalizes)
    rule_match = re.search(
        r"\.filter-bar\s+input\s*\[\s*type\s*=\s*[\"']?text[\"']?\s*\]\s*\{([^}]+)\}", css
    )
    assert rule_match, (
        'Filter-bar text input rule (`.filter-bar input[type="text"]`) is missing from app.css — '
        "the filter input will not have the correct flex/max-width sizing."
    )
    declarations = rule_match.group(1)
    normalized = re.sub(r"\s+", " ", declarations)
    # Check for flex:1 (or flex: 1) and max-width:300px
    norm_nospace = normalized.replace(" ", "")
    assert "flex:1" in norm_nospace, (
        f"Filter-bar input rule missing `flex:1` declaration. Found: {declarations!r}"
    )
    assert "max-width:300px" in norm_nospace, (
        f"Filter-bar input rule missing `max-width:300px` declaration. Found: {declarations!r}"
    )


def test_wave_layout_components_are_defined() -> None:
    """AC-9 — the classes this wave introduces are backed by a real stylesheet rule.

    This guards the *mechanical* form of the "referenced-but-undefined class" bug class (§12.10):
    if a template references ``.control-row`` but the rule is accidentally dropped from the
    stylesheet, the control groups silently lose their flex layout. Scoping the assertion to the
    wave-owned classes keeps it meaningful and cheap; a blanket "every referenced class is defined"
    check is **not** used because it fails on a pre-existing backlog (see the backlog test below),
    and forcing it green is precisely what led an earlier attempt to invent ~11 component styles.
    """
    defined = _defined_class_selectors(_stylesheet())
    missing = sorted(WAVE_COMPONENT_CLASSES - defined)
    assert not missing, (
        "Layout-standardization component classes are referenced by templates but have no rule in "
        f"app.css: {missing}. The control-group layout (`.control-row`) depends on these rules "
        "existing."
    )


def test_pre_existing_undefined_component_classes_are_reported() -> None:
    """Informational (non-fatal) — surface the undefined-component backlog without failing CI.

    Scanning every literal ``class`` token in templates against the stylesheet reveals a sizeable
    set of **pre-existing** project classes with no rule (the ``modal-*`` family, ``token-*``,
    ``pager-sizes``, ``readonly-value``, …). These are styling gaps that predate this wave; the
    modals, for instance, currently render with only the native ``<dialog>`` defaults. Fixing them
    is a design task (AGENTS.md §6 forbids design changes here), so this test *reports* them rather
    than asserting their absence. It still fails if the extraction machinery itself breaks (so the
    report can't silently go empty by accident), which keeps the check honest.
    """
    defined = _defined_class_selectors(_stylesheet())
    referenced = _referenced_literal_classes()
    undefined = {
        cls
        for cls in referenced
        if cls not in defined
        and not cls.startswith("fa-")  # Font Awesome — covered by test_ui_icons.py
        and not _is_tailwind_utility(cls)
    }

    # Sanity: the extractor must find *something* — an empty report would mean the scan silently
    # stopped working, which is the failure mode this test exists to avoid.
    assert referenced, "class extractor found no tokens at all — the scan is broken."

    if undefined:
        warnings.warn(
            "Pre-existing project classes referenced in templates but not defined in app.css "
            f"({len(undefined)}): {', '.join(sorted(undefined))}. Fixing these requires a design "
            "decision (out of scope for the layout-standardization wave); tracked as follow-up.",
            stacklevel=1,
        )
