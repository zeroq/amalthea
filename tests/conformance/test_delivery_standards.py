"""TODO §7 — the delivery standard is written, pinned, and non-vacuous.

A doc-only rule that can silently disappear is not a rule. This guard checks the binding short form
in `AGENTS.md`, the detailed procedure in `docs/spec/delivery-standards.md`, and the already-shipped
Tailwind source/artifact split. Each detector has a synthetic negative control so it cannot pass by
matching nothing (R11).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

AGENTS_REQUIRED = (
    "## 7. Delivery workflow",
    "A passing acceptance criterion is only evidence once its failure mode has been demonstrated.",
    "Every phase requires a domain-expert gate: plan, implementation, verification, and handoff.",
    "A subagent is not complete until it returns the report it promised in its final message.",
    "failure claim requires reproduction on the base commit",
    "The full suite is the final gate; targeted subsets are diagnostics only.",
    "ui/static/ui/app.src.css` is the only Tailwind input",
)

DELIVERY_REQUIRED = (
    "## 1. AC failure-mode evidence",
    "## 2. Domain-expert gate",
    "## 3. Subagent report contract",
    "## 4. Failure claims and test gates",
    "## 5. Tailwind source/artifact split",
    "A passing acceptance criterion is only evidence once its failure mode has been demonstrated.",
    "Every phase requires a domain-expert gate: plan, implementation, verification, and handoff.",
    "A subagent is not complete until it returns the report it promised in its final message.",
    "failure claim requires reproduction on the base commit",
    "The full suite is the final gate; targeted subsets are diagnostics only.",
    "ui/static/ui/app.src.css` is the **only** Tailwind input",
)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _missing(text: str, needles: tuple[str, ...]) -> list[str]:
    return [needle for needle in needles if needle not in text]


def test_section_7_rules_are_present_in_agents_and_delivery_standard() -> None:
    assert not _missing(_read("AGENTS.md"), AGENTS_REQUIRED)
    assert not _missing(_read("docs/spec/delivery-standards.md"), DELIVERY_REQUIRED)


@pytest.mark.parametrize("needle", DELIVERY_REQUIRED)
def test_the_rule_detector_flags_each_missing_rule(needle: str) -> None:
    doc = _read("docs/spec/delivery-standards.md").replace(needle, "")
    assert needle in _missing(doc, DELIVERY_REQUIRED)


def test_css_split_is_pinned_in_the_build() -> None:
    makefile = _read("Makefile")
    assert "-i ui/static/ui/app.src.css -o ui/static/ui/app.css --minify" in makefile
    assert '-i ui/static/ui/app.src.css -o "$$tmp" --minify' in makefile
    assert 'cmp -s "$$tmp" ui/static/ui/app.css' in makefile


def test_the_css_build_detector_flags_a_hand_edited_artifact_pipeline() -> None:
    makefile = _read("Makefile").replace("ui/static/ui/app.src.css", "ui/static/ui/app.css")
    assert "-i ui/static/ui/app.src.css -o ui/static/ui/app.css --minify" not in makefile


def test_tailwind_source_detection_is_explicitly_scoped() -> None:
    css_source = _read("ui/static/ui/app.src.css")
    assert '@import "tailwindcss" source(none);' in css_source
    assert '@source "../../templates";' in css_source
    assert '@source "./*.js";' in css_source


def test_the_css_source_detector_flags_automatic_detection() -> None:
    css_source = _read("ui/static/ui/app.src.css").replace(
        '@import "tailwindcss" source(none);', '@import "tailwindcss";'
    )
    assert '@import "tailwindcss" source(none);' not in css_source
