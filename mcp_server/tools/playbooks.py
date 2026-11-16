"""MCP read tools for safe playbook metadata."""

from __future__ import annotations

from typing import Any

from mcp_server.auth import Principal
from mcp_server.limits import row_limit
from mcp_server.scopes import org_playbooks
from mcp_server.tools.base import ToolFailureError
from mcp_server.tools.serializers import safe_playbook


def list_playbooks(principal: Principal, limit: int | None = None) -> Any:
    cap = row_limit(limit)
    qs = org_playbooks(principal)
    total = qs.count()
    rows = [safe_playbook(playbook) for playbook in qs.order_by("name")[:cap]]
    from mcp_server.schemas import tool_result

    return tool_result(rows, total=total, truncated=total > len(rows), summary=f"{total} playbooks")


def get_playbook_meta(principal: Principal, playbook_name: str) -> Any:
    name = (playbook_name or "").strip()
    if not name:
        raise ToolFailureError("invalid_playbook_name", message="playbook_name is required")
    playbook = org_playbooks(principal).filter(name=name).first()
    if playbook is None:
        raise ToolFailureError("not_found", status="not_found")
    from mcp_server.schemas import tool_result

    return tool_result(safe_playbook(playbook), total=1, summary="playbook metadata")


def register(server: Any) -> None:
    from mcp_server.tools.base import run_read

    @server.tool(
        name="list_playbooks",
        description="List safe playbook metadata: name, description, trigger and active state.",
    )
    async def list_playbooks_tool(limit: int | None = None) -> Any:
        """List playbooks; never returns config, scripts, URLs or headers."""
        arguments = {"limit": limit} if limit is not None else {}
        return await run_read("list_playbooks", arguments, list_playbooks)

    @server.tool(
        name="get_playbook_meta",
        description="Read one playbook's safe metadata; never returns config or scripts.",
    )
    async def get_playbook_meta_tool(playbook_name: str) -> Any:
        """Read safe playbook metadata by name."""
        return await run_read(
            "get_playbook_meta", {"playbook_name": playbook_name}, get_playbook_meta
        )
