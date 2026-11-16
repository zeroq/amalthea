"""MCP read tools for cases, digests, timelines and case lists."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from automation.models import AutomationRun
from cases.ledger import events_after
from cases.models import TimelineEvent
from mcp_server.auth import Principal
from mcp_server.limits import row_limit
from mcp_server.scopes import (
    case_observable_links,
    case_tasks,
    org_alerts,
    resolve_case,
    visible_cases,
)
from mcp_server.tools.base import ToolFailureError
from mcp_server.tools.serializers import (
    safe_alert,
    safe_automation_run,
    safe_case,
    safe_observable,
    safe_task,
    safe_timeline_event,
)


def _cursor(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ToolFailureError("invalid_cursor", message="cursor must be an event UUID") from exc


def list_cases(
    principal: Principal,
    status: str | None = None,
    severity: int | None = None,
    assignee: str | None = None,
    phase: str | None = None,
    verdict: str | None = None,
    limit: int | None = None,
) -> Any:
    if severity is not None and severity not in (1, 2, 3, 4):
        raise ToolFailureError("invalid_severity", message="severity must be 1..4")
    cap = row_limit(limit)
    qs: QuerySet[Any] = visible_cases(principal).select_related("status", "assignee")
    if status:
        qs = qs.filter(status__value__iexact=status)
    if severity is not None:
        qs = qs.filter(severity=severity)
    if assignee:
        qs = qs.filter(assignee__login__iexact=assignee)
    if phase:
        qs = qs.filter(phase__iexact=phase)
    if verdict:
        qs = qs.filter(verdict__iexact=verdict)

    total = qs.count()
    rows = [safe_case(case) for case in qs.order_by("-start_date", "-id")[:cap]]
    from mcp_server.schemas import tool_result

    return tool_result(rows, total=total, truncated=total > len(rows), summary=f"{total} cases")


def get_case(principal: Principal, case_id: str) -> Any:
    case = resolve_case(principal, case_id)
    if case is None:
        raise ToolFailureError("not_found", status="not_found")
    from mcp_server.schemas import tool_result

    return tool_result(safe_case(case), total=1, summary="case")


def get_case_digest(
    principal: Principal,
    case_id: str,
    include_timeline: bool = True,
    timeline_limit: int | None = None,
    include_tasks: bool = True,
    include_pending_runs: bool = True,
) -> Any:
    case = resolve_case(principal, case_id)
    if case is None:
        raise ToolFailureError("not_found", status="not_found")

    timeline_cap = row_limit(timeline_limit, default=20)
    alert_qs = org_alerts(principal).filter(case=case).select_related("status", "assignee")
    observable_links = case_observable_links(principal, case)
    task_qs = case_tasks(principal, case)
    run_qs = AutomationRun.objects.filter(case=case, status__in=("Pending", "Running")).order_by(
        "-created_at"
    )

    alerts = [safe_alert(alert) for alert in alert_qs.order_by("-date", "-id")[:5]]
    observables = [safe_observable(link.observable) for link in observable_links[:10]]
    tasks = [safe_task(task) for task in task_qs.order_by("order", "-created_at")[:10]]
    runs = [safe_automation_run(run) for run in run_qs[:5]]

    timeline: list[dict[str, Any]] = []
    timeline_more = False
    if include_timeline:
        # The digest is a summarisation bundle, so it carries the *most
        # recent* events in chronological order, not the first page.
        recent = list(
            TimelineEvent.objects.filter(case_id=case.id)
            .select_related("actor")
            .order_by("-date", "-id")[: timeline_cap + 1]
        )
        timeline_more = len(recent) > timeline_cap
        timeline = [safe_timeline_event(event) for event in reversed(recent[:timeline_cap])]

    counts: dict[str, int] = {
        "alerts": alert_qs.count(),
        "observables": observable_links.count(),
        "tasks": task_qs.count(),
        "pendingRuns": run_qs.count(),
    }
    data = {
        "case": safe_case(case),
        "counts": counts,
        "alerts": alerts,
        "observables": observables,
        "tasks": tasks if include_tasks else [],
        "timeline": timeline,
        "pendingRuns": runs if include_pending_runs else [],
    }
    from mcp_server.schemas import tool_result

    return tool_result(
        data,
        total=1,
        truncated=timeline_more,
        summary=f"case digest: {counts['alerts']} alerts, {len(observables)} observables",
    )


def get_case_timeline(
    principal: Principal,
    case_id: str,
    cursor: str | None = None,
    limit: int | None = None,
) -> Any:
    case = resolve_case(principal, case_id)
    if case is None:
        raise ToolFailureError("not_found", status="not_found")
    cap = row_limit(limit, default=50)
    anchor = _cursor(cursor)
    if anchor is not None and not TimelineEvent.objects.filter(case_id=case.id, pk=anchor).exists():
        # A cursor from another case (or a deleted row) must not silently replay
        # this case's whole ledger: that would make a cross-case anchor look valid.
        raise ToolFailureError("not_found", status="not_found")
    events = list(events_after(str(case.id), anchor)[: cap + 1])
    has_more = len(events) > cap
    events = events[:cap]
    next_cursor = str(events[-1].id) if has_more and events else None
    from mcp_server.schemas import tool_result

    return tool_result(
        [safe_timeline_event(event) for event in events],
        total=len(events),
        truncated=has_more,
        next_cursor=next_cursor,
        summary=f"{len(events)} timeline events",
    )


def register(server: Any) -> None:
    from mcp_server.tools.base import run_read

    @server.tool(
        name="list_cases", description="List cases visible to the credential, filtered and capped."
    )
    async def list_cases_tool(
        status: str | None = None,
        severity: int | None = None,
        assignee: str | None = None,
        phase: str | None = None,
        verdict: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """List org-visible cases. Severity is 1..4."""
        arguments = {
            key: value
            for key, value in {
                "status": status,
                "severity": severity,
                "assignee": assignee,
                "phase": phase,
                "verdict": verdict,
                "limit": limit,
            }.items()
            if value is not None
        }
        return await run_read("list_cases", arguments, list_cases)

    @server.tool(
        name="get_case", description="Read one visible case header; never returns automation logs."
    )
    async def get_case_tool(case_id: str) -> Any:
        """Read one case by UUID."""
        return await run_read("get_case", {"case_id": case_id}, get_case)

    @server.tool(
        name="get_case_digest",
        description="Bounded case digest: header, counts, recent alerts, observables, tasks, timeline and pending runs.",
    )
    async def get_case_digest_tool(
        case_id: str,
        include_timeline: bool = True,
        timeline_limit: int | None = None,
        include_tasks: bool = True,
        include_pending_runs: bool = True,
    ) -> Any:
        """Read a bounded case digest by UUID."""
        arguments = {
            "case_id": case_id,
            "include_timeline": include_timeline,
            "include_tasks": include_tasks,
            "include_pending_runs": include_pending_runs,
        }
        if timeline_limit is not None:
            arguments["timeline_limit"] = timeline_limit
        return await run_read("get_case_digest", arguments, get_case_digest)

    @server.tool(
        name="get_case_timeline",
        description="Read a case's ledger in keyset order with a next cursor.",
    )
    async def get_case_timeline_tool(
        case_id: str,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """Read case timeline events by UUID, optional cursor and limit."""
        arguments: dict[str, Any] = {"case_id": case_id}
        if cursor is not None:
            arguments["cursor"] = cursor
        if limit is not None:
            arguments["limit"] = limit
        return await run_read("get_case_timeline", arguments, get_case_timeline)
