"""Correlation result types and helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict
from uuid import UUID

from alerts.models import Alert
from cases.models import Case


@dataclass(frozen=True)
class AutoBundleDecision:
    """Explainable result of an auto-bundle attempt.

    Invariants:
      - Exactly one of ``status ∈ {"bundled", "skipped", "failed"}`` is set.
      - ``reason`` is a short, non-secret, human-readable explanation.
      - ``mode`` is the match mode used: "correlation_key" or "shared_observable".
      - ``case_id`` is set only when ``status == "bundled"``.
      - ``window_minutes`` is the rule's window, or ``None`` if skipped before evaluation.
      - ``signal`` describes the matching artefact (key name or observable type/value).
    """

    status: str  # "bundled" | "skipped" | "failed"
    reason: str
    mode: str = ""
    case_id: UUID | None = None
    window_minutes: int | None = None
    signal: str = ""


@dataclass(frozen=True)
class SimilarCase:
    """A case that shares observable artifacts with a source case or alert."""

    case: Case
    shared_count: int
    shared: list[tuple[str, str]]  # ordered by data_type then data


@dataclass(frozen=True)
class SimilarAlertMatch:
    """An alert matched via the similar-alerts helper."""

    alert: Alert
    similarity: int | None  # fuzzy score 0-100, or None when only observables matched
    shared_observable_count: int  # 0 when only fuzzy matched


class _SimilarUnionEntry(TypedDict):
    alert: Alert
    similarity: int | None
    shared_observable_count: int


@dataclass(frozen=True)
class SimilarAlert:
    """An unlinked alert that is fuzzily similar to this case's attached alerts.

    The exact-overlap fields (shared_count, shared) were retired in Phase B
    (PLAN-2026-10-20-alert-fuzzy-similarity.md §3.4). Only the fuzzy
    similarity score is retained.
    """

    alert: Alert
    similarity: int  # 0-100, maximum ppdeep.compare score against case's alerts


def _json_depth(obj: object, current: int = 0) -> int:
    """Iterative depth check to avoid RecursionError (M3)."""
    max_depth = 0
    stack = [(obj, current)]
    while stack:
        node, depth = stack.pop()
        max_depth = max(max_depth, depth)
        if isinstance(node, dict):
            for v in node.values():
                stack.append((v, depth + 1))
        elif isinstance(node, (list, tuple)):
            for v in node:
                stack.append((v, depth + 1))
    return max_depth
