"""Output envelope helpers for MCP Phase 1 tools."""

from __future__ import annotations

from typing import Any

from mcp_types import CallToolResult, TextContent


def tool_result(
    data: Any,
    *,
    total: int,
    truncated: bool = False,
    next_cursor: str | None = None,
    summary: str | None = None,
) -> CallToolResult:
    """Build a structured result plus a short, untrusted-data-free text summary."""
    meta: dict[str, Any] = {"total": int(total), "truncated": bool(truncated)}
    if next_cursor:
        meta["nextCursor"] = next_cursor
    if summary is None:
        summary = f"{total} result{'s' if total != 1 else ''}"
    return CallToolResult(
        content=[TextContent(type="text", text=summary[:500])],
        structured_content={"data": data, "meta": meta},
    )
