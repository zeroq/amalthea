from __future__ import annotations

from typing import Any

from django.utils import timezone
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.models import Alert
from cases.models import (
    Case,
)
from core.serializers import (
    case_json,
)
from identity.audit import maybe_record_cross_org_read

from ._common import _case_access, _not_found


def _case_export_document(case: Case) -> dict[str, Any]:
    """Export v2 document (TODO §18.3).

    Reuses the shared wire serializers so the export and the API never drift. ``case_json`` is
    called **without** ``detail=True``: its timeline path is capped at 50, which is right for an
    API page and wrong for an investigation export. The full child lists below are the export.

    Bounds are the §18.7 caps; anything beyond them is counted in ``truncated`` rather than
    silently dropped. Secrets, tokens, CSRF material and ``payload_ssdeep`` are never emitted.
    """
    from alerts.export import alert_export_document
    from automation.models import AutomationRun
    from cases.ledger import events_after
    from core.serializers import observable_json, procedure_json, timeline_event_json

    truncation: dict[str, int] = {}

    def bounded(name: str, rows: Any, limit: int | None) -> list[Any]:
        items = list(rows)
        if limit is not None and len(items) > limit:
            truncation[name] = len(items) - limit
            return items[:limit]
        return items

    tags = [
        {"name": link.tag.name}
        for link in case.tag_links.select_related("tag").order_by("tag__name")
    ]
    custom_fields = [
        {"name": value.custom_field.name, "value": value.value}
        for value in case.custom_field_values.select_related("custom_field").order_by(
            "order", "custom_field__name"
        )
    ]

    task_rows: list[dict[str, Any]] = []
    note_total = 0
    for task in case.tasks.select_related("assignee").order_by("order", "id")[:5_000]:
        notes: list[dict[str, Any]] = []
        for note in task.notes.select_related("created_by").order_by("created_at", "id"):
            if note_total >= 5_000:
                break
            notes.append(
                {
                    "message": note.message,
                    "createdAt": note.created_at.isoformat() if note.created_at else None,
                    "createdBy": note.created_by.login if note.created_by else None,
                }
            )
            note_total += 1
        task_rows.append(
            {
                "title": task.title,
                "description": task.description,
                "group": task.group,
                "status": task.status,
                "flag": task.flag,
                "assignee": task.assignee.login if task.assignee else None,
                "order": task.order,
                "dueDate": task.due_date.isoformat() if task.due_date else None,
                "startedAt": task.started_at.isoformat() if task.started_at else None,
                "endedAt": task.ended_at.isoformat() if task.ended_at else None,
                "mandatory": task.mandatory,
                "notes": notes,
            }
        )

    timeline_rows = bounded("timeline", events_after(str(case.id)), 10_000)
    observable_rows = bounded(
        "observables",
        case.case_observables.select_related("observable__data_type").order_by(
            "-created_at", "-id"
        ),
        2_000,
    )
    alert_rows = bounded(
        "alerts",
        Alert.objects.filter(case=case).select_related("status").order_by("date", "id"),
        1_000,
    )

    document: dict[str, Any] = {
        "_type": "CaseExport",
        "version": 2,
        "exportedAt": timezone.now().isoformat(),
        "case": {
            **case_json(case),
            "ownerOrg": case.owner_org.name if case.owner_org else None,
            "tags": tags,
            "customFields": custom_fields,
            "sourceTemplate": case.source_template.name
            if case.source_template is not None
            else None,
            "ingestionWarnings": case.ingestion_warnings or {},
        },
        "observables": [observable_json(link) for link in observable_rows],
        "tasks": task_rows,
        "timeline": [timeline_event_json(event) for event in timeline_rows],
        "comments": [
            {
                "message": comment.message,
                "createdAt": comment.created_at.isoformat() if comment.created_at else None,
                "createdBy": comment.created_by.login if comment.created_by else None,
            }
            for comment in bounded(
                "comments",
                case.comments.select_related("created_by").order_by("created_at", "id"),
                5_000,
            )
        ],
        "pages": [
            {
                "title": page.title,
                "content": page.content,
                "order": page.order,
                "category": page.category,
            }
            for page in bounded("pages", case.pages.order_by("order", "id"), 5_000)
        ],
        "procedures": [
            procedure_json(procedure)
            for procedure in bounded(
                "procedures",
                case.procedures.select_related("ttp").order_by("occur_date", "id"),
                5_000,
            )
        ],
        "shares": [
            {"organisation": share.organisation.name, "permissions": share.permissions}
            for share in case.shares.select_related("organisation").order_by("created_at", "id")
        ],
        "attachments": [
            {
                "name": attachment.name,
                "contentType": attachment.content_type,
                "size": attachment.size,
                "sha256": attachment.sha256,
                "external": attachment.external,
            }
            for attachment in case.attachments.order_by("created_at", "id")
        ],
        "alerts": [alert_export_document(alert) for alert in alert_rows],
        "automationRuns": [
            {
                "playbookName": run.playbook_name,
                "status": run.status,
                "outputLog": run.output_log,
                "triggeredByObservableId": (
                    str(run.triggered_by_observable_id) if run.triggered_by_observable_id else None
                ),
                "startedAt": run.started_at.isoformat() if run.started_at else None,
                "endedAt": run.finished_at.isoformat() if run.finished_at else None,
            }
            for run in AutomationRun.objects.filter(case=case).order_by("-created_at", "-id")
        ],
    }
    if truncation:
        document["truncated"] = truncation
    return document


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_export(request: Request, case_id: str) -> Response:
    """`GET /api/v1/case/{id}/export` — a self-contained JSON export of the case (AC6.1-P5-a).

    Read access is enough (a share with `read` may export), and a foreign organisation gets a 404.
    The optional `password` query parameter is accepted for TheHive client compatibility but the
    payload is **not** encrypted — a recorded deviation, not an unstated one.
    """
    case = _case_access(request, case_id, write=False)
    if case is None:
        return _not_found("Case")

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=case,
        target_type="Case",
        target_id=str(case.id),
        target_org_field="owner_org",
    )

    return Response(_case_export_document(case))
