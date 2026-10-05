"""Mapping utilities for extracting alert fields from raw payloads.

The mapping engine reads JSON-path rules from `IngestionSource.mapping_config`
and extracts normalized values for alert fields. This includes the rule that
produces each alert's `correlation_key` (REVIEW C4).
"""

from __future__ import annotations

from typing import Any

try:
    from jsonpath_ng import parse as _parse  # type: ignore[import-untyped]
except (ImportError, Exception):  # pragma: no cover
    _parse = None


def _extract_by_jsonpath(path: str, payload: Any) -> Any | None:
    """Extract a single value from payload using a JSONPath expression."""
    if _parse is None or not path:
        return None
    try:
        expr = _parse(path)
        matches = expr.find(payload)
        if matches:
            return matches[0].value
    except Exception:
        return None
    return None


def extract_correlation_key(mapping_config: dict[str, Any] | None, payload: Any) -> str:
    """Extract correlation_key from payload using mapping_config.

    Looks for a rule in mapping_config that defines how to derive correlation_key.
    Common keys: "correlation_key", "correlation", "correlationKey",
    "correlationKeyPath", "correlation_key_path".
    """
    if not mapping_config:
        return ""

    for key in (
        "correlation_key",
        "correlation",
        "correlationKey",
        "correlationKeyPath",
        "correlation_key_path",
        "correlationId",
    ):
        if key in mapping_config:
            path = mapping_config[key]
            if isinstance(path, str) and path.strip():
                result = _extract_by_jsonpath(path.strip(), payload)
                if result is not None:
                    if isinstance(result, str):
                        return result.strip()
                    return str(result)

    return ""
