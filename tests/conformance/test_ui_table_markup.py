"""Regression guard: data-table markup must not collide with Tailwind utilities.

Every data table used to carry ``class="grid"``. Tailwind v4 also emits a ``.grid`` *utility*
(``display: grid``) because that token appears in the markup, and the ``utilities`` cascade layer
outranks the ``components`` layer that held the project's table rule. The ``<table>`` therefore
became a grid container: its ``<thead>`` and ``<tbody>`` were laid out as two independent grid items,
so each computed its own column widths and the **column headers no longer lined up with the rows**.

The component class is now ``.data-table`` (and ``.data-table-tight``). These tests fail if a table
regresses to a class that Tailwind defines as a layout utility.
"""

from __future__ import annotations

import re
from pathlib import Path

UI_DIR = Path(__file__).resolve().parents[2] / "ui"
TEMPLATES = sorted((UI_DIR / "templates").rglob("*.html"))
STYLESHEET = UI_DIR / "static" / "ui" / "app.css"

CLASS_ATTR = re.compile(r'class="([^"]*)"')
TABLE_TAG = re.compile(r"<table\b[^>]*>")

# Class names that collide with a Tailwind utility (``.grid`` -> ``display:grid``) or were renamed.
COLLIDING_TOKENS = frozenset({"grid", "grid-tight"})


def test_no_template_uses_a_colliding_grid_class() -> None:
    offenders: list[str] = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        for match in CLASS_ATTR.finditer(text):
            tokens = set(match.group(1).split())
            if tokens & COLLIDING_TOKENS:
                offenders.append(f"{path.relative_to(UI_DIR)}: {match.group(0)}")
    assert not offenders, (
        "tables must use .data-table, not a class that Tailwind defines as a layout utility "
        "(it breaks column-header alignment):\n" + "\n".join(offenders)
    )


def test_every_table_uses_the_data_table_component() -> None:
    offenders: list[str] = []
    for path in TEMPLATES:
        text = path.read_text(encoding="utf-8")
        for tag in TABLE_TAG.findall(text):
            match = CLASS_ATTR.search(tag)
            tokens = set(match.group(1).split()) if match else set()
            if "data-table" not in tokens:
                offenders.append(f"{path.relative_to(UI_DIR)}: {tag}")
    assert not offenders, "every <table> must carry the styled .data-table class:\n" + "\n".join(
        offenders
    )


def test_compiled_stylesheet_styles_the_data_table_component() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert ".data-table{" in css, "compiled stylesheet is missing the .data-table component"
    assert ".data-table th" in css and ".data-table td" in css, "column styling missing"
    assert "border-collapse:collapse" in css, "table layout reset missing"
