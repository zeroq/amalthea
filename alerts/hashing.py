"""SSDEEP-compatible fuzzy hashing for alert payloads.

This module provides canonicalization and SSDEEP hashing of alert payloads
to enable fuzzy similarity comparison between alerts. The hash is computed
once at ingest and stored on the Alert model for later comparison.

Uses `ppdeep` — a pure-Python SSDEEP-compatible implementation.
"""

from __future__ import annotations

import json
from typing import Any

from django.conf import settings

try:
    import ppdeep  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — guarded by requirements
    ppdeep = None


#: Volatile keys that are dropped during canonicalization.
#: Comparison is case-insensitive (key.casefold()).
#: These are fields that change on every event but don't change the event's
#: semantic identity (timestamps, IDs, process-specific values, etc.).
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "uuid",
        "date",
        "timestamp",
        "time",
        "@timestamp",
        "created",
        "createdat",
        "updatedat",
        "starttime",
        "endtime",
        "eventid",
        "event_id",
        "requestid",
        "request_id",
        "traceid",
        "trace_id",
        "pid",
        "processid",
        "process_id",
        "ingestiontime",
        "ingestedat",
        "hash",
        "signature",
        "sourceref",
        "source_ref",
    }
)


def _is_scalar(value: Any) -> bool:
    """Return True if value is a JSON scalar (not a container)."""
    return not isinstance(value, (dict, list, tuple))


def _canonicalize(obj: Any) -> Any:
    """Recursively canonicalize a JSON-serializable object.

    - Drops keys whose casefolded name is in VOLATILE_KEYS
    - Sorts dict keys
    - Sorts lists whose elements are ALL scalars
    - Recurses into lists of containers (dicts), preserving order after
      canonicalizing each element
    """
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for key, value in obj.items():
            # Mongo-style `_id` canonicalises to `id` (without keeping the wire literal here).
            if key.casefold().lstrip("_") in VOLATILE_KEYS:
                continue
            result[key] = _canonicalize(value)
        # Sort keys for deterministic output
        return {k: result[k] for k in sorted(result.keys())}
    elif isinstance(obj, (list, tuple)):
        canonicalized = [_canonicalize(item) for item in obj]
        # Sort only if all elements are scalars
        if all(_is_scalar(item) for item in canonicalized):
            try:
                return sorted(canonicalized)
            except TypeError:
                # Mixed types that can't be sorted — preserve order
                return canonicalized
        return canonicalized
    return obj


def canonicalize_payload(payload: Any) -> str:
    """Canonicalize a payload and serialize to deterministic JSON string.

    Args:
        payload: Any JSON-serializable object (typically a dict from raw_payload).

    Returns:
        Deterministic JSON string with sorted keys, no whitespace, volatile
        keys removed, and scalar lists sorted.
    """
    canonical = _canonicalize(payload)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _check_bounds(payload: Any) -> bool:
    """Check if payload is within configured size/depth bounds.

    Returns:
        True if within bounds, False if oversize/overdeep.
    """
    max_body = getattr(settings, "WEBHOOK_MAX_BODY_SIZE", 5 * 1024 * 1024)
    max_depth = getattr(settings, "WEBHOOK_MAX_DEPTH", 30)

    # Approximate size check (same as ingest path)
    payload_size = len(str(payload))
    if payload_size > max_body:
        return False

    # Depth check — iterative (no recursion on attacker-controlled structures).
    if _max_json_depth(payload) > max_depth:
        return False

    return True


def _max_json_depth(obj: Any) -> int:
    """Return the maximum nesting depth of a JSON-like structure, iteratively.

    Uses an explicit stack so a pathologically deep payload cannot exhaust the
    Python recursion limit before the caller's depth bound is enforced (FIND-02).
    """
    max_depth = 0
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            max_depth = depth
        if isinstance(node, dict):
            for value in node.values():
                stack.append((value, depth + 1))
        elif isinstance(node, (list, tuple)):
            for value in node:
                stack.append((value, depth + 1))
    return max_depth


def compute_payload_ssdeep(summary: str, payload: Any) -> str:
    """Compute an SSDEEP-compatible hash of the canonicalized alert content.

    The hash is computed from: summary + canonicalized payload.
    Title is intentionally excluded because SIEM/EDR titles commonly embed
    per-event tokens (timestamps, IDs) that make the 80-threshold fragile.
    Measured: same title + volatile-only payload diff → score 100; title with
    a timestamp → 33; title with an ID → 22; different title + identical
    payload → 22. See PLAN-2026-10-20-alert-fuzzy-similarity.md Decision (A).

    Returns empty string on any failure (oversize, overdeep, exception, missing ppdeep).

    Args:
        summary: Alert summary.
        payload: Raw payload dict (from alert.raw_payload).

    Returns:
        SSDEEP hash string, or "" if not computable.
    """
    if ppdeep is None:
        return ""

    # Bound check
    if not _check_bounds(payload):
        return ""

    try:
        canonical_text = canonicalize_payload(payload)
        # Build the full text to hash: summary + canonical payload (title excluded)
        parts = []
        if summary:
            parts.append(summary)
        parts.append(canonical_text)
        full_text = "\n".join(parts)

        if not full_text.strip():
            return ""

        return str(ppdeep.hash(full_text))
    except (Exception, RecursionError, MemoryError):
        # Never raise — empty hash means "not fuzzy-comparable"
        return ""
