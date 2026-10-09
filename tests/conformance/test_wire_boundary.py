"""TODO 2.3 / RISK R3 — the one-way dependency rule, enforced.

ADR-002 §D11 / BRIEF §0.4: Amalthea serves a Django-clean internal representation **and** a
TheHive-shaped wire representation from the same resources ("compatible on the wire, clean
inside"). TheHive *field-name string literals* (`_id`, `_createdAt`, `dataType`, …) belong to
the wire boundary only. The day a model, service, mapper or automation task starts reading
`payload["dataType"]`, that contract is already broken — it just has not failed a test yet.

This guard walks the source tree and fails when a wire literal appears outside the boundary.

The boundary is the *implemented* API surface — the wire renderers in ``core/serializers.py``,
the query DSL (``query/``), the per-app DRF ``views.py``/``urls.py``, and ``compat/`` itself.
(The brief drew that surface under ``compat/serializers``, ``compat/views`` and
``compat/query``; the implementation co-located it with the apps — recorded as a deviation.
Widening the allowlist is not the fix for a real leak: it is the review trigger.)

The detector is made non-vacuous by ``test_the_detector_flags_a_literal`` — a detector that
silently matched nothing could never pass it.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: TheHive wire field names that must not survive outside the wire boundary. Each is chosen
#: because it has a clean internal analogue (`data_type`, `start_date`, `case`, …), so a match
#: is always a leak and never a coincidence. `tlp`/`pap` are deliberately **not** here — they
#: are real internal column names.
FORBIDDEN_LITERALS = frozenset(
    {
        "_id",
        "_type",
        "_createdAt",
        "_updatedAt",
        "dataType",
        "customFields",
        "severityLabel",
        "startDate",
        "endDate",
        "caseId",
        "alertId",
        "taskId",
    }
)

#: Directories that make up the wire boundary. A trailing `/` matches the whole directory;
#: `*/name` matches that basename anywhere; anything else is an exact repo-relative path.
WIRE_BOUNDARY = (
    "compat/",
    "query/",
    "core/serializers.py",
    "*/views.py",
    "*/urls.py",
)

_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache", ".ruff_cache"}
)


def _matches_boundary(rel: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return rel.startswith(pattern)
    if pattern.startswith("*/"):
        return Path(rel).name == pattern[2:]
    return rel == pattern


def is_wire_boundary(rel: str) -> bool:
    """Is `rel` (a repo-relative POSIX path) allowed to hold TheHive field-name literals?"""
    return any(_matches_boundary(rel, pattern) for pattern in WIRE_BOUNDARY)


def wire_literals_in(source: str) -> list[tuple[int, str]]:
    """Return ``(lineno, literal)`` for every forbidden literal used as a string constant.

    Exact-match on the constant's value, so `"data_type"` (internal) and `"dataTypeCache"`
    (a longer identifier that merely contains the wire name) are correctly ignored.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in FORBIDDEN_LITERALS
        ):
            found.append((node.lineno, node.value))
    return found


def _iter_source_files() -> Iterator[tuple[Path, str]]:
    for root, dirs, files in os.walk(REPO_ROOT):
        # `.venv` alone holds thousands of files; prune before descending.
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            rel = path.relative_to(REPO_ROOT).as_posix()
            # Tests assert the wire shape on purpose, so they are the boundary's mirror.
            if rel.startswith("tests/"):
                continue
            yield path, rel


def test_no_wire_literals_outside_the_wire_boundary() -> None:
    offenders: list[str] = []
    for path, rel in _iter_source_files():
        if is_wire_boundary(rel):
            continue
        for lineno, literal in wire_literals_in(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}:{lineno}: {literal!r}")
    assert not offenders, (
        "TheHive wire field-name literals leaked outside the wire boundary (RISK R3 / TODO 2.3). "
        "Move the read/write through the serializer/compat layer, or add the module to WIRE_BOUNDARY "
        "in this test only if it is genuinely part of the API surface:\n  " + "\n  ".join(offenders)
    )


def test_the_detector_flags_a_literal() -> None:
    """Non-vacuity control: the detector must catch a leak and leave clean code alone."""
    assert wire_literals_in('payload["dataType"]') == [(1, "dataType")]
    assert wire_literals_in('{"_createdAt": value}') == [(1, "_createdAt")]
    assert wire_literals_in('row.get("caseId")') == [(1, "caseId")]
    # Internal snake_case and a longer identifier containing a wire name are not leaks.
    assert wire_literals_in('obj.data_type == "data_type"') == []
    assert wire_literals_in('"dataTypeCache"') == []
    assert wire_literals_in('"""doc prose"""') == []


def test_the_boundary_matcher() -> None:
    assert is_wire_boundary("compat/enums.py")
    assert is_wire_boundary("query/engine.py")
    assert is_wire_boundary("core/serializers.py")
    assert is_wire_boundary("cases/views.py")
    assert is_wire_boundary("cases/urls.py")
    assert not is_wire_boundary("cases/models.py")
    assert not is_wire_boundary("services/foo.py")
    assert not is_wire_boundary("core/events.py")
