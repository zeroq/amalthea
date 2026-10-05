"""Mapping utilities for extracting alert fields from raw payloads.

The mapping engine reads JSON-path rules from `IngestionSource.mapping_config`
and extracts normalized values for alert fields. This includes the rule that
produces each alert's `correlation_key` (REVIEW C4).
"""

from __future__ import annotations

from typing import Any

try:
    from jsonpath_ng import parse as _parse  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    # The package is a hard dependency; this branch exists so a missing wheel degrades the mapping
    # to "no mapping" instead of taking the whole ingest app down at import time.
    _parse = None


def extract_by_jsonpath(path: str, payload: Any) -> Any | None:
    """First value at JSONPath `path` in `payload`, or `None`.

    A bad path returns `None` rather than raising: `mapping_config` is operator-authored data and a
    typo in it should produce an alert with a `severity_defaulted` warning, not a 500 that teaches
    the sender to stop retrying.
    """
    if _parse is None or not path:
        return None
    try:
        matches = _parse(path).find(payload)
    except Exception:
        return None
    return matches[0].value if matches else None


# Retained for callers that predate the rename; `extract_by_jsonpath` is the public name.
_extract_by_jsonpath = extract_by_jsonpath


def extract_correlation_key(mapping_config: dict[str, Any] | None, payload: Any) -> str:
    """Extract correlation_key from payload using mapping_config.

    Looks for a rule in mapping_config that defines how to derive correlation_key.
    Common keys: "correlation_key", "correlation", "correlationKey",
    "correlationKeyPath", "correlation_key_path".
    """
    if not mapping_config:
        return ""

    # The first spelling that yields a value wins, so a source configured with both a legacy and a
    # current key still correlates. Order is newest-convention-first.
    for key in (
        "correlation_key",
        "correlationKey",
        "correlation_key_path",
        "correlationKeyPath",
        "correlation_id",
        "correlationId",
        "correlation",
    ):
        path = mapping_config.get(key)
        if isinstance(path, str) and path.strip():
            result = extract_by_jsonpath(path.strip(), payload)
            if result is not None:
                return result.strip() if isinstance(result, str) else str(result)

    return ""
