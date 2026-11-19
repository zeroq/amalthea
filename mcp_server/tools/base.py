"""Shared execution wrapper for authenticated, audited MCP tools."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from asgiref.sync import sync_to_async
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server.audit import record_invocation
from mcp_server.auth import Principal, principal_from_token
from mcp_server.context import current_request
from mcp_server.limits import is_rate_limited, timeout_seconds


class ToolFailureError(Exception):
    """A deliberate, client-safe failure (recorded in the audit row)."""

    def __init__(self, code: str, status: str = "invalid", message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status


def require_principal() -> Principal:
    principal = principal_from_token(get_access_token())
    if principal is None:
        raise ToolError("authentication required")
    if not principal.scopes:
        raise ToolError("insufficient scope")
    return principal


def require_scope(scope: str) -> Principal:
    """Require a specific MCP scope for the tool being invoked."""
    principal = require_principal()
    if not principal.has_scope(scope):
        raise ToolError("insufficient_scope")
    return principal


def _target(arguments: dict[str, Any]) -> tuple[str | None, str | None]:
    for key, label in (
        ("case_id", "case"),
        ("alert_id", "alert"),
        ("observable_id", "observable"),
        ("playbook_name", "playbook"),
    ):
        value = arguments.get(key)
        if value:
            return label, str(value)
    return None, None


async def run_read_data(
    tool_name: str,
    arguments: dict[str, Any],
    fn: Callable[..., Any],
    *,
    scope: str | None = None,
    call_style: str = "positional",
    target_type: str | None = None,
    target_id: str | None = None,
) -> Any:
    """Run one audited tool call with auth, rate limiting and timeout."""
    principal = require_scope(scope) if scope else require_principal()
    request = current_request()

    if is_rate_limited(principal, request.client_ip):
        await record_invocation(
            principal=principal,
            tool_name=tool_name,
            arguments=arguments,
            status="rate_limited",
            error_code="rate_limited",
            duration_ms=0,
            target_type=target_type,
            target_id=target_id,
        )
        raise ToolError("rate_limited")

    if target_type is None and target_id is None:
        target_type, target_id = _target(arguments)

    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout_seconds()):
            if call_style == "dict":
                result = await sync_to_async(fn, thread_sensitive=True)(
                    arguments=arguments, principal=principal
                )
            else:
                result = await sync_to_async(fn, thread_sensitive=True)(principal, **arguments)
    except ToolFailureError as exc:
        duration = int((time.monotonic() - started) * 1000)
        await record_invocation(
            principal=principal,
            tool_name=tool_name,
            arguments=arguments,
            status=exc.status,
            error_code=exc.code,
            duration_ms=duration,
            target_type=target_type,
            target_id=target_id,
        )
        raise ToolError(exc.code) from exc
    except TimeoutError as exc:
        duration = int((time.monotonic() - started) * 1000)
        await record_invocation(
            principal=principal,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_code="timeout",
            duration_ms=duration,
            target_type=target_type,
            target_id=target_id,
        )
        raise ToolError("timeout") from exc
    except Exception:
        duration = int((time.monotonic() - started) * 1000)
        await record_invocation(
            principal=principal,
            tool_name=tool_name,
            arguments=arguments,
            status="error",
            error_code="internal_error",
            duration_ms=duration,
            target_type=target_type,
            target_id=target_id,
        )
        raise ToolError("internal_error") from None

    duration = int((time.monotonic() - started) * 1000)
    await record_invocation(
        principal=principal,
        tool_name=tool_name,
        arguments=arguments,
        status="ok",
        duration_ms=duration,
        target_type=target_type,
        target_id=target_id,
    )
    return result


async def run_read(
    tool_name: str,
    arguments: dict[str, Any],
    fn: Callable[..., Any],
    *,
    target_type: str | None = None,
    target_id: str | None = None,
) -> Any:
    """Run a tool body that returns an already-built CallToolResult."""
    return await run_read_data(
        tool_name,
        arguments,
        fn,
        target_type=target_type,
        target_id=target_id,
    )


async def run_write_data(
    tool_name: str,
    arguments: dict[str, Any],
    fn: Callable[..., Any],
    *,
    scope: str,
    target_type: str | None = None,
    target_id: str | None = None,
) -> Any:
    """Run a Phase 2 tool body with `(arguments=..., principal=...)` call style."""
    return await run_read_data(
        tool_name,
        arguments,
        fn,
        scope=scope,
        call_style="dict",
        target_type=target_type,
        target_id=target_id,
    )


async def run_write(
    tool_name: str,
    arguments: dict[str, Any],
    fn: Callable[..., Any],
    *,
    scope: str,
    target_type: str | None = None,
    target_id: str | None = None,
) -> Any:
    """Run a write-adjacent tool body; `fn` returns plain data."""
    return await run_write_data(
        tool_name,
        arguments,
        fn,
        scope=scope,
        target_type=target_type,
        target_id=target_id,
    )
