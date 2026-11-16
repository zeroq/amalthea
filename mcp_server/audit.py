"""MCP invocation audit rows.

Only authenticated tool calls are recorded.  Arguments are reduced to an
allowlist (``mcp_server.limits.audit_arguments``) and the full argument set is
hashed, so the audit trail never becomes a second copy of case data.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from asgiref.sync import sync_to_async

from mcp_server.auth import Principal
from mcp_server.context import current_request
from mcp_server.limits import audit_arguments
from mcp_server.models import McpInvocation


def arguments_hash(arguments: dict[str, Any] | None) -> str:
    canonical = json.dumps(arguments or {}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@sync_to_async
def record_invocation(
    *,
    principal: Principal,
    tool_name: str,
    arguments: dict[str, Any] | None,
    status: str,
    duration_ms: int,
    error_code: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> McpInvocation:
    """Persist exactly one audit row for an authenticated tool call."""
    request = current_request()
    return McpInvocation.objects.create(
        request_id=request.request_id or uuid.uuid4().hex,
        credential_id=principal.credential_id,
        actor_id=principal.actor_id,
        organisation_id=principal.organisation_id,
        tool_name=tool_name[:100],
        arguments_hash=arguments_hash(arguments),
        arguments_redacted=audit_arguments(arguments),
        target_type=(target_type or "")[:50],
        target_id=(str(target_id)[:100] if target_id else ""),
        status=status,
        error_code=(error_code or "")[:100],
        duration_ms=max(0, int(duration_ms)),
    )
