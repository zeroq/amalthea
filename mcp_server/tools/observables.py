"""MCP read tools for observables."""

from __future__ import annotations

from typing import Any

from django.db.models import Q

from mcp_server.auth import Principal
from mcp_server.limits import row_limit
from mcp_server.scopes import (
    case_observable_links,
    org_observables,
    resolve_case,
    resolve_observable,
    visible_case_count,
)
from mcp_server.tools.base import ToolFailureError
from mcp_server.tools.serializers import safe_observable


def list_observables(
    principal: Principal,
    case_id: str | None = None,
    data_type: str | None = None,
    value: str | None = None,
    limit: int | None = None,
) -> Any:
    cap = row_limit(limit)
    if case_id:
        case = resolve_case(principal, case_id)
        if case is None:
            raise ToolFailureError("not_found", status="not_found")
        links = case_observable_links(principal, case)
        if data_type:
            links = links.filter(observable__data_type__name__iexact=data_type)
        if value:
            links = links.filter(
                Q(observable__normalized_data__icontains=value)
                | Q(observable__data__icontains=value)
            )
        total = links.count()
        rows = [safe_observable(link.observable) for link in links[:cap]]
        from mcp_server.schemas import tool_result

        return tool_result(
            rows,
            total=total,
            truncated=total > len(rows),
            summary=f"{total} case observables",
        )

    qs = org_observables(principal).select_related("data_type")
    if data_type:
        qs = qs.filter(data_type__name__iexact=data_type)
    if value:
        qs = qs.filter(Q(normalized_data__icontains=value) | Q(data__icontains=value))
    total = qs.count()
    rows = [safe_observable(item) for item in qs.order_by("-created_at", "-id")[:cap]]
    from mcp_server.schemas import tool_result

    return tool_result(
        rows, total=total, truncated=total > len(rows), summary=f"{total} observables"
    )


def get_observable(principal: Principal, observable_id: str) -> Any:
    observable = resolve_observable(principal, observable_id)
    if observable is None:
        raise ToolFailureError("not_found", status="not_found")
    count = visible_case_count(principal, observable)
    from mcp_server.schemas import tool_result

    return tool_result(
        safe_observable(observable, case_count=count, include_enrichment=True),
        total=1,
        summary=f"observable linked to {count} visible cases",
    )


def register(server: Any) -> None:
    from mcp_server.tools.base import run_read

    @server.tool(
        name="list_observables",
        description="List org-scoped observables, optionally for one case; safe enrichment only.",
    )
    async def list_observables_tool(
        case_id: str | None = None,
        data_type: str | None = None,
        value: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """List observables, optionally filtered by case, type or value."""
        arguments = {
            key: item
            for key, item in {
                "case_id": case_id,
                "data_type": data_type,
                "value": value,
                "limit": limit,
            }.items()
            if item is not None
        }
        return await run_read("list_observables", arguments, list_observables)

    @server.tool(
        name="get_observable",
        description="Read one org-scoped observable and its visible linked-case count.",
    )
    async def get_observable_tool(observable_id: str) -> Any:
        """Read one observable by UUID."""
        return await run_read("get_observable", {"observable_id": observable_id}, get_observable)
