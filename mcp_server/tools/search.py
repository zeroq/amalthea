"""MCP read tool: global case/alert/observable search."""

from __future__ import annotations

from typing import Any

from mcp_server.auth import Principal
from mcp_server.limits import row_limit
from mcp_server.scopes import actor_from_principal
from mcp_server.tools.base import ToolFailureError
from query.search import KINDS, parse_query, search_sections


def search(
    principal: Principal,
    query: str,
    kind: str | None = None,
    page: int = 1,
    per_page: int | None = None,
) -> Any:
    raw = (query or "").strip()[:200]
    if not raw:
        from mcp_server.schemas import tool_result

        return tool_result({"sections": [], "counts": {}}, total=0, summary="empty query")
    if kind is not None and kind not in KINDS:
        raise ToolFailureError("invalid_kind", message=f"kind must be one of {', '.join(KINDS)}")
    page = max(1, int(page or 1))
    if page > 1000:
        raise ToolFailureError("invalid_page", message="page must be 1000 or lower")
    cap = row_limit(per_page, default=50)
    payload = search_sections(
        parse_query(raw),
        actor_from_principal(principal),
        kind=kind,
        page=page,
        per_page=cap,
    )
    sections = [section.to_dict() for section in payload["sections"]]
    truncated = any(
        section["has_next"] or section["total"] > len(section["results"]) for section in sections
    )
    from mcp_server.schemas import tool_result

    return tool_result(
        {"sections": sections, "counts": payload["counts"]},
        total=payload["total"],
        truncated=truncated,
        summary=f"{payload['total']} search matches",
    )


def register(server: Any) -> None:
    from mcp_server.tools.base import run_read

    @server.tool(
        name="search",
        description="Search cases, alerts and observables with the platform's shared query parser.",
    )
    async def search_tool(
        query: str,
        kind: str | None = None,
        page: int = 1,
        per_page: int | None = None,
    ) -> Any:
        """Search by substring terms; kind is case, alert or observable."""
        arguments: dict[str, Any] = {"query": query, "page": page}
        if kind is not None:
            arguments["kind"] = kind
        if per_page is not None:
            arguments["per_page"] = per_page
        return await run_read("search", arguments, search)
