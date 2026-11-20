"""Central row, body, timeout and rate limits for the MCP gateway."""

from __future__ import annotations

import time
from typing import Any

from django.conf import settings
from django.core.cache import cache

from mcp_server.auth import Principal


def row_limit(requested: int | None, *, default: int | None = None) -> int:
    """Clamp a caller-supplied row limit to the configured maximum."""
    maximum = max(1, int(getattr(settings, "MCP_MAX_ROWS", 50)))
    if requested is None:
        requested = default if default is not None else maximum
    return min(max(1, int(requested)), maximum)


def timeout_seconds() -> int:
    return max(1, int(getattr(settings, "MCP_TIMEOUT_SECONDS", 15)))


def max_body_bytes() -> int:
    return max(1024, int(getattr(settings, "MCP_MAX_BODY_BYTES", 4 * 1024 * 1024)))


def rate_per_minute() -> int:
    return max(1, int(getattr(settings, "MCP_RATE_PER_MINUTE", 60)))


def is_rate_limited(principal: Principal, client_ip: str | None) -> bool:
    """Fixed-window per-credential and per-IP limiter.

    Uses the same cache primitive as the ingestion endpoint: ``cache.add`` is
    atomic, so concurrent first requests cannot both believe they created the
    counter.  A cache backend that cannot ``incr`` falls back to a fresh add,
    never to an unbounded allow.
    """
    window = int(time.time() // 60)
    identity = principal.credential_id or principal.oauth_binding_id or principal.actor_id
    keys = [f"mcp:identity:{identity}:{window}"]
    if client_ip:
        keys.append(f"mcp:ip:{client_ip}:{window}")

    limit = rate_per_minute()
    for key in keys:
        if cache.add(key, 1, timeout=120):
            continue
        try:
            current = cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=120)
            current = 1
        if current > limit:
            return True
    return False


def audit_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Return an allowlisted, bounded copy of tool arguments for the audit row."""
    if not arguments:
        return {}
    allowed = {
        "query",
        "kind",
        "page",
        "per_page",
        "status",
        "severity",
        "source",
        "assignee",
        "flag",
        "since",
        "phase",
        "verdict",
        "limit",
        "cursor",
        "include_timeline",
        "include_tasks",
        "include_pending_runs",
        "case_id",
        "alert_id",
        "observable_id",
        "playbook_name",
        "case_type",
        "data_type",
        "value",
    }
    safe: dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in allowed:
            continue
        if isinstance(value, str):
            safe[key] = value[:200]
        elif isinstance(value, (bool, int, float)) or value is None:
            safe[key] = value
    return safe
