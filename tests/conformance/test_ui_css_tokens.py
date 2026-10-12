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
"""

from __future__ import annotations

import re
from pathlib import Path

STYLESHEET = Path(__file__).resolve().parents[2] / "ui" / "static" / "ui" / "app.css"

DEFINED_RE = re.compile(r"(--color-[a-zA-Z0-9-]+)\s*:")
USED_RE = re.compile(r"var\(\s*(--color-[a-zA-Z0-9-]+)\s*[,)]")
OUTLINE_NONE_RE = re.compile(r"outline\s*:\s*(?:none|0)\b")


def _stylesheet() -> str:
    return STYLESHEET.read_text(encoding="utf-8")


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
