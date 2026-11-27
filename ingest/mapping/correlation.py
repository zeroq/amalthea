"""Correlation-key extraction and v2 submapping resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .jsonpath import extract_by_jsonpath


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


@dataclass(frozen=True)
class MappingResolution:
    """Result of resolving a mapping config against an alert payload.

    `config` is the effective v2 config to use (master or matched submapping).
    `matched_type` is the raw payload `type` value that triggered a submapping match,
    or `None` if no submapping was matched.
    `used_submapping` is True exactly when a raw-type match replaced the master config.
    """

    config: dict[str, Any]
    matched_type: str | None = None
    used_submapping: bool = False


def submapping_count(config: dict[str, Any] | None) -> int:
    """Return the number of submappings in a v2 config, for UI display.

    Returns 0 when config is None, empty, or has no submappings key.
    """
    if not isinstance(config, dict):
        return 0
    submappings = config.get("submappings")
    if not isinstance(submappings, dict):
        return 0
    return len(submappings)


def resolve_mapping_config(
    mapping_config: dict[str, Any] | None,
    payload: Any,
) -> MappingResolution:
    """Resolve the effective v2 config for a payload, supporting per-type submappings.

    Behaviour:
    - Return master unchanged for empty config, no submappings, non-mapping payload,
      missing/non-string `type`, or no exact case-sensitive key match.
    - On exact match return the submapping object as `config`, set `matched_type`
      and `used_submapping=True`.
    - Never mutate `mapping_config` or `payload`.
    - Do not recurse into nested submappings.

    The payload `type` is read as a raw top-level value (exact case-sensitive match).
    If the payload is not a dict, or has no `type` key, or the `type` value is not a
    string, the master config is returned unchanged.
    """
    # Baseline: no config or no payload dict → master unchanged
    if not isinstance(mapping_config, dict):
        # Non-dict master config: fall back to empty (prevents .get() crash on non-dict)
        return MappingResolution(config={}, matched_type=None, used_submapping=False)
    if not isinstance(payload, dict):
        return MappingResolution(config=mapping_config, matched_type=None, used_submapping=False)

    # Read the payload's raw type; missing/non-string → master fallback
    payload_type = payload.get("type")
    if not isinstance(payload_type, str):
        return MappingResolution(config=mapping_config, matched_type=None, used_submapping=False)

    # No submappings in master → master unchanged
    submappings = mapping_config.get("submappings")
    if not isinstance(submappings, dict) or not submappings:
        return MappingResolution(config=mapping_config, matched_type=None, used_submapping=False)

    # Exact case-sensitive key match
    if payload_type in submappings:
        # Return the matched submapping as the effective config
        # Do not recurse into nested submappings within the submapping itself
        config = dict(submappings[payload_type])  # shallow copy
        config.pop("submappings", None)  # strip nested submappings
        return MappingResolution(
            config=config,
            matched_type=payload_type,
            used_submapping=True,
        )

    # No match → master unchanged
    return MappingResolution(config=mapping_config, matched_type=None, used_submapping=False)
