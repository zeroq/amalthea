"""MCP read tool for case tasks."""

from __future__ import annotations

from typing import Any

from mcp_server.auth import Principal
from mcp_server.limits import row_limit
from mcp_server.scopes import case_tasks, resolve_case, visible_tasks
from mcp_server.tools.base import ToolFailureError
from mcp_server.tools.serializers import safe_task


def list_tasks(
    principal: Principal,
    case_id: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
    limit: int | None = None,
) -> Any:
    cap = row_limit(limit)
    if case_id:
        case = resolve_case(principal, case_id)
        if case is None:
            raise ToolFailureError("not_found", status="not_found")
        qs = case_tasks(principal, case)
    else:
        qs = visible_tasks(principal)

    if status:
        qs = qs.filter(status__iexact=status)
    if assignee:
        qs = qs.filter(assignee__login__iexact=assignee)

    total = qs.count()
    rows = [safe_task(task) for task in qs.order_by("order", "-created_at")[:cap]]
    from mcp_server.schemas import tool_result

    return tool_result(rows, total=total, truncated=total > len(rows), summary=f"{total} tasks")


def register(server: Any) -> None:
    from mcp_server.tools.base import run_read

    @server.tool(
        name="list_tasks",
        description="List tasks for one case or all visible cases, filtered and capped.",
    )
    async def list_tasks_tool(
        case_id: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """List case tasks, optionally filtered by case, status or assignee."""
        arguments = {
            key: value
            for key, value in {
                "case_id": case_id,
                "status": status,
                "assignee": assignee,
                "limit": limit,
            }.items()
            if value is not None
        }
        return await run_read("list_tasks", arguments, list_tasks)
