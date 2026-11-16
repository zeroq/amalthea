"""MCP read tools for alerts and alert triage context."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from cases.correlation import candidate_cases_for_alert
from mcp_server.auth import Principal
from mcp_server.limits import row_limit
from mcp_server.scopes import org_alerts, resolve_alert
from mcp_server.tools.base import ToolFailureError
from mcp_server.tools.serializers import safe_alert


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        raise ToolFailureError("invalid_since", message="since must be an ISO-8601 datetime")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def list_alerts(
    principal: Principal,
    status: str | None = None,
    severity: int | None = None,
    source: str | None = None,
    assignee: str | None = None,
    flag: bool | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> Any:
    if severity is not None and severity not in (1, 2, 3, 4):
        raise ToolFailureError("invalid_severity", message="severity must be 1..4")
    cap = row_limit(limit)
    since_value = _parse_since(since)

    qs = org_alerts(principal).select_related("status", "assignee")
    if status:
        qs = qs.filter(status__value__iexact=status)
    if severity is not None:
        qs = qs.filter(severity=severity)
    if source:
        qs = qs.filter(source__iexact=source)
    if assignee:
        qs = qs.filter(assignee__login__iexact=assignee)
    if flag is not None:
        qs = qs.filter(flag=flag)
    if since_value is not None:
        qs = qs.filter(date__gte=since_value)

    total = qs.count()
    rows = [safe_alert(alert) for alert in qs.order_by("-date", "-id")[:cap]]
    from mcp_server.schemas import tool_result

    return tool_result(rows, total=total, truncated=total > len(rows), summary=f"{total} alerts")


def get_alert(principal: Principal, alert_id: str) -> Any:
    alert = resolve_alert(principal, alert_id)
    if alert is None:
        raise ToolFailureError("not_found", status="not_found")
    from mcp_server.schemas import tool_result

    return tool_result(safe_alert(alert), total=1, summary="alert")


def get_alert_triage_context(
    principal: Principal,
    alert_id: str,
    limit: int | None = None,
) -> Any:
    alert = resolve_alert(principal, alert_id)
    if alert is None:
        raise ToolFailureError("not_found", status="not_found")

    cap = min(row_limit(limit, default=10), 10)
    # ``candidate_cases_for_alert`` applies the same org/share visibility rule
    # as ``visible_cases``, so the result is already scoped to this credential.
    similar = candidate_cases_for_alert(alert, user=_scoping_actor(principal), limit=cap)[:cap]
    candidates = [
        {
            "id": str(item.case.id),
            "number": item.case.number,
            "title": item.case.title,
            "severity": item.case.severity,
            "status": item.case.status.value if item.case.status_id else None,
            "sharedCount": item.shared_count,
            "shared": [
                {"type": data_type, "value": str(value)[:500]}
                for data_type, value in item.shared[:10]
            ],
        }
        for item in similar
    ]
    data = {"alert": safe_alert(alert), "candidateCases": candidates}
    from mcp_server.schemas import tool_result

    return tool_result(
        data,
        total=len(candidates),
        truncated=len(candidates) >= cap,
        summary=f"alert with {len(candidates)} candidate cases",
    )


def _scoping_actor(principal: Principal) -> Any:
    from mcp_server.scopes import actor_from_principal

    return actor_from_principal(principal)


def register(server: Any) -> None:
    from mcp_server.tools.base import run_read

    @server.tool(
        name="list_alerts",
        description="List alerts in the credential's organisation, filtered and capped.",
    )
    async def list_alerts_tool(
        status: str | None = None,
        severity: int | None = None,
        source: str | None = None,
        assignee: str | None = None,
        flag: bool | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """List org-scoped alerts. Severity is 1..4; since is ISO-8601."""
        arguments = {
            key: value
            for key, value in {
                "status": status,
                "severity": severity,
                "source": source,
                "assignee": assignee,
                "flag": flag,
                "since": since,
                "limit": limit,
            }.items()
            if value is not None
        }
        return await run_read("list_alerts", arguments, list_alerts)

    @server.tool(
        name="get_alert", description="Read one org-scoped alert; never returns raw payload."
    )
    async def get_alert_tool(alert_id: str) -> Any:
        """Read one alert by UUID."""
        return await run_read("get_alert", {"alert_id": alert_id}, get_alert)

    @server.tool(
        name="get_alert_triage_context",
        description="Read one alert plus a capped list of similar open cases.",
    )
    async def get_alert_triage_context_tool(alert_id: str, limit: int | None = None) -> Any:
        """Read alert triage context by alert UUID."""
        arguments: dict[str, Any] = {"alert_id": alert_id}
        if limit is not None:
            arguments["limit"] = limit
        return await run_read("get_alert_triage_context", arguments, get_alert_triage_context)
