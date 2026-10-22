"""Observable extraction filtering (P3) — aggressive, feed-aware noise suppression.

This module implements the default filtering rules for observable extraction findings.
Filtered findings are still recorded as suggestions with `filtered_out=True` so analysts
can see what was suppressed and override if needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from observables.extractor import Finding


@dataclass(frozen=True)
class FilterResult:
    """Result of a filtering decision."""

    keep: bool
    reason: str | None
    category: str | None


# --- Compiled regex patterns for performance ---

# CrowdStrike Falcon portal URLs: falcon.crowdstrike.com/activity/* and falcon.*.crowdstrike.com/activity/*
_FALCON_ACTIVITY_URL = re.compile(
    r"^https?://falcon(\.[a-z0-9\-]+)?\.crowdstrike\.com/activity/", re.IGNORECASE
)

# Platform ID field names (exact match or prefix match for config_id_*)
_PLATFORM_ID_FIELDS = frozenset(
    {
        "agent_id",
        "agent_scan_id",
        "aggregate_id",
        "composite_id",
        "poly_id",
        "cid",
        "scan_id",
        "event_id",
    }
)
_PLATFORM_ID_PREFIXES = ("config_id_",)

# Platform ID value patterns (common GUID/hex formats that are platform-internal)
# These are heuristics: 32-char hex (MD5-like), 36-char UUID, 64-char hex (SHA256-like)
# when found IN a platform ID field context
_HEX32 = re.compile(r"^[a-f0-9]{32}$", re.IGNORECASE)
_HEX40 = re.compile(r"^[a-f0-9]{40}$", re.IGNORECASE)
_HEX64 = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
_UUID = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.IGNORECASE)


def _is_platform_id_field(path: str) -> bool:
    """Check if a JSON path ends with a known platform ID field name."""
    # Path could be "field" or "parent.field" or "array[0].field"
    leaf = path.split(".")[-1]
    leaf = leaf.split("[")[0]  # strip array index
    if leaf in _PLATFORM_ID_FIELDS:
        return True
    return any(leaf.startswith(p) for p in _PLATFORM_ID_PREFIXES)


def _is_platform_id_value(value: str) -> bool:
    """Heuristic: value looks like a platform-internal ID (GUID/hex)."""
    return bool(
        _HEX32.match(value) or _HEX40.match(value) or _HEX64.match(value) or _UUID.match(value)
    )


def _is_falcon_activity_url(value: str) -> bool:
    """Check if URL matches CrowdStrike Falcon activity portal pattern."""
    return bool(_FALCON_ACTIVITY_URL.match(value))


def should_keep_finding(
    finding: Finding,
    context: dict[str, Any] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Determine if a finding should be kept or filtered out.

    Args:
        finding: The Finding to evaluate (includes provenance via source_paths).
        context: Optional context dict with keys like 'source_alert_source', 'feed_type'.

    Returns:
        (keep: bool, reason: str | None, category: str | None)
        - keep=True: finding passes, reason/category are None
        - keep=False: finding filtered, reason explains why, category groups similar filters
    """
    if context is None:
        context = {}

    # 1. Filter CrowdStrike Falcon portal activity URLs
    if finding.type_name == "url" and _is_falcon_activity_url(finding.normalized):
        return (
            False,
            "CrowdStrike Falcon portal activity URL (detection tool link, not IOC)",
            "portal_url",
        )

    # 2. Filter values from known platform ID fields
    # Check provenance paths for platform ID field names
    for path in finding.source_paths:
        if _is_platform_id_field(path):
            # Filter any observable type from platform ID fields (these are internal identifiers, not IOCs)
            # Exception: if the field name suggests it's actually an IOC field (e.g., "sha256", "md5", "ip")
            leaf = path.split(".")[-1].split("[")[0]
            ioc_field_names = {
                "sha256",
                "sha1",
                "md5",
                "ip",
                "ipv4",
                "ipv6",
                "domain",
                "fqdn",
                "hostname",
                "url",
                "filename",
                "filepath",
                "path",
            }
            if leaf.lower() in ioc_field_names:
                # This field name suggests it's an IOC field, don't filter
                continue
            return (
                False,
                f"Value from platform ID field '{path}' (likely internal ID, not IOC)",
                "platform_id",
            )

    # 3. Filter hashes that appear to be platform IDs based on value pattern alone
    # (when no field context available, be conservative - only filter if very confident)
    # This is a fallback when provenance doesn't reveal the field name
    if finding.type_name == "hash" and not finding.source_paths:
        # Only filter very obvious platform IDs (UUIDs) when we have no field context
        if _UUID.match(finding.normalized):
            return (
                False,
                "Hash value matches UUID pattern (likely platform ID, no field context)",
                "platform_id",
            )

    # 4. Future: feed-specific rules from context
    # e.g., if context.get("feed_type") == "crowdstrike": apply additional rules

    return True, None, None


def get_filter_category_reason(
    finding: Finding, context: dict[str, Any] | None = None
) -> tuple[str | None, str | None]:
    """Get filter reason and category without the boolean (for recording on suggestions)."""
    keep, reason, category = should_keep_finding(finding, context)
    if not keep:
        return category, reason
    return None, None
