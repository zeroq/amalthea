"""Read-only MCP resources.

Resources are authenticated and audited exactly like tools, but are addressed by
URI rather than called as JSON tools.
"""

from __future__ import annotations

from typing import Any

from cases.ledger import events_after
from mcp_server.scopes import resolve_alert, resolve_case, resolve_observable, visible_case_count
from mcp_server.tools.base import ToolFailureError, run_read_data
from mcp_server.tools.serializers import (
    safe_alert,
    safe_case,
    safe_observable,
    safe_timeline_event,
)


def _case(principal: Any, case_id: str) -> dict[str, Any]:
    case = resolve_case(principal, case_id)
    if case is None:
        raise ToolFailureError("not_found", status="not_found")
    return safe_case(case)


def _alert(principal: Any, alert_id: str) -> dict[str, Any]:
    alert = resolve_alert(principal, alert_id)
    if alert is None:
        raise ToolFailureError("not_found", status="not_found")
    return safe_alert(alert)


def _observable(principal: Any, observable_id: str) -> dict[str, Any]:
    observable = resolve_observable(principal, observable_id)
    if observable is None:
        raise ToolFailureError("not_found", status="not_found")
    return safe_observable(
        observable,
        case_count=visible_case_count(principal, observable),
        include_enrichment=True,
    )


def _timeline(principal: Any, case_id: str) -> list[dict[str, Any]]:
    case = resolve_case(principal, case_id)
    if case is None:
        raise ToolFailureError("not_found", status="not_found")
    return [safe_timeline_event(event) for event in events_after(str(case.id), None)[:50]]


def register(server: Any) -> None:
    @server.resource(
        "amalthea://case/{case_id}",
        name="case",
        title="Case header",
        description="Org-scoped case header as JSON.",
        mime_type="application/json",
    )
    async def case_resource(case_id: str) -> Any:
        """Read a case header by UUID."""
        return await run_read_data("resource:case", {"case_id": case_id}, _case)

    @server.resource(
        "amalthea://case/{case_id}/timeline",
        name="timeline",
        title="Case timeline",
        description="First 50 org-scoped ledger events for a case as JSON.",
        mime_type="application/json",
    )
    async def timeline_resource(case_id: str) -> Any:
        """Read the first page of a case timeline by UUID."""
        return await run_read_data("resource:timeline", {"case_id": case_id}, _timeline)

    @server.resource(
        "amalthea://alert/{alert_id}",
        name="alert",
        title="Alert context",
        description="Org-scoped alert without its raw payload, as JSON.",
        mime_type="application/json",
    )
    async def alert_resource(alert_id: str) -> Any:
        """Read one alert by UUID."""
        return await run_read_data("resource:alert", {"alert_id": alert_id}, _alert)

    @server.resource(
        "amalthea://observable/{observable_id}",
        name="observable",
        title="Observable",
        description="Org-scoped observable with safe enrichment and visible case count.",
        mime_type="application/json",
    )
    async def observable_resource(observable_id: str) -> Any:
        """Read one observable by UUID."""
        return await run_read_data(
            "resource:observable", {"observable_id": observable_id}, _observable
        )
