"""T1 `case`, `task` and `observable` endpoints, scoped to the MVP loop (Phase 5).

Read paths return the case with its observables, timeline and automation runs attached (`detail=True`)
because the MVP proof is "the playbook's output is visible on the case timeline", and a client that
has to make four calls to see that is not much of a proof surface.

Write paths are deliberately narrow: status transitions, task creation/status, and observable
addition. Each one writes a ledger entry through `cases.ledger` in the same transaction, so the
ledger is never behind the data — a comment-only write that skipped the ledger would make the
timeline a decoration, and the choke point is what keeps it published as well as persisted.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.escalation import link_case_from_identifier, resolve_case_status
from cases.ledger import append_timeline_event
from cases.models import Case, Task, TimelineEvent
from compat.time import to_epoch_ms
from core.serializers import case_json, observable_json
from observables.extractor import add_observable, extract_into_case
from observables.models import Observable


def _actor(request: Request) -> Any:
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def _not_found(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"}, status=status.HTTP_404_NOT_FOUND
    )


def _resolve_case(identifier: str) -> Case | None:
    try:
        return link_case_from_identifier(identifier)
    except Case.DoesNotExist:
        return None


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_collection(request: Request) -> Response:
    """`GET /api/v1/case` lists; `POST /api/v1/case` creates.

    Both verbs live in one view because they share the filter semantics, and two views would let
    those drift: a `?status=` that the list honours and the create path ignores is a quiet bug.
    """
    if request.method == "POST":
        return _create_case(request)
    queryset = Case.objects.select_related("status", "assignee").order_by("-start_date")
    status_filter = request.query_params.get("status")
    if status_filter:
        queryset = queryset.filter(status__value=status_filter)
    severity = request.query_params.get("severity")
    if severity and severity.lstrip("-").isdigit():
        queryset = queryset.filter(severity=int(severity))
    return Response([case_json(c) for c in queryset[:200]])


@api_view(["GET", "PATCH", "PUT"])
@renderer_classes([JSONRenderer])
def case_detail(request: Request, case_id: str) -> Response:
    """`GET|PATCH /api/v1/case/{idOrNumber}` — read the case, or change it.

    Read returns observables, timeline and automation runs attached: the MVP proof is "the
    playbook's output is visible on the case timeline", and a client that needs four calls to see
    that is not much of a proof surface.

    The write half handles the status transition the triage queue is built on, and writes a
    `TimelineEvent` **in the same transaction** — a status change that is not on the ledger makes
    the ledger a decoration.
    """
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    if request.method in ("PATCH", "PUT"):
        return _update_case(request, case)
    return Response(case_json(case, detail=True))


def _create_case(request: Request) -> Response:
    """Create a case directly (no alert behind it)."""
    payload = request.data if isinstance(request.data, dict) else {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return Response(
            {
                "type": "BadRequest",
                "message": "title is required",
                "fields": {"title": ["required"]},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    warnings: list[str] = []
    case = Case.objects.create(
        title=title[:500],
        description=str(payload.get("description") or ""),
        severity=int(payload.get("severity") or 2),
        status=resolve_case_status(str(payload.get("status") or "InProgress"), warnings=warnings),
        start_date=timezone.now(),
    )
    texts = [case.title, case.description]
    if payload.get("extract") or payload.get("observables"):
        texts.extend(str(v) for v in (payload.get("observables") or []))
    extract_into_case(case, *texts)
    append_timeline_event(
        case,
        title="Case created",
        description=case.description[:500],
        kind="case-created",
        actor=_actor(request),
    )
    case.refresh_from_db()
    return Response(case_json(case, detail=True), status=status.HTTP_201_CREATED)


@transaction.atomic
def _update_case(request: Request, case: Case) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    previous_status = case.status.value if case.status_id else None
    # Captured before the payload loop mutates `case`, so the assignment event below can tell an
    # assignee that actually changed from a PATCH that merely restated the same one.
    previous_assignee_id = case.assignee_id
    for key in ("title", "description", "summary", "severity", "tlp", "pap", "flag"):
        if key in payload:
            setattr(case, key, payload[key])
            fields.append(key)
    if "status" in payload:
        warnings: list[str] = []
        case.status = resolve_case_status(str(payload["status"]), warnings=warnings)
        fields.append("status")
    if "assignee" in payload:
        from identity.models import User

        login = payload["assignee"]
        case.assignee = (
            User.objects.filter(login=login).first() if login else None
        ) or User.objects.filter(pk=login).first()
        fields.append("assignee")
    if not fields:
        return Response(
            {"type": "BadRequest", "message": "No updatable field supplied", "fields": {}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    case.save(update_fields=[*fields, "updated_at"])
    case.refresh_from_db()

    new_status = case.status.value if case.status_id else None
    if new_status != previous_status:
        append_timeline_event(
            case,
            title=f"Status changed: {previous_status} → {new_status}",
            description=str(payload.get("comment") or ""),
            kind="status-changed",
            actor=_actor(request),
            metadata={"from": previous_status, "to": new_status},
        )
    if case.assignee_id != previous_assignee_id:
        login = case.assignee.login if case.assignee else None
        append_timeline_event(
            case,
            title=f"Assigned to {login}" if login else "Unassigned",
            kind="assigned",
            actor=_actor(request),
            metadata={"assignee": login},
        )
    return Response(case_json(case, detail=True))


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_task_create(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/task` — add a task to the case."""
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    payload = request.data if isinstance(request.data, dict) else {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return Response(
            {
                "type": "BadRequest",
                "message": "title is required",
                "fields": {"title": ["required"]},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    task = Task.objects.create(
        case=case,
        title=title[:500],
        status=str(payload.get("status") or "Todo"),
    )
    return Response(
        {"_id": str(task.id), "id": str(task.id), "title": task.title, "status": task.status},
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_task_list(request: Request, case_id: str) -> Response:
    """`GET` lists a case's tasks; `POST` adds one.

    Both verbs share the route *and* the view, so `?status=` filtering on the list and task creation
    cannot drift apart, and so a single `path()` entry serves the collection.
    """
    if request.method == "POST":
        return case_task_create(request, case_id)
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    return Response(
        [
            {
                "_id": str(t.id),
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "assignee": getattr(t.assignee, "login", None),
            }
            for t in case.tasks.all()
        ]
    )


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_observable_add(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/observable` — attach one artifact, or re-run extraction."""
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    payload = request.data if isinstance(request.data, dict) else {}
    with transaction.atomic():
        if payload.get("extract") or payload.get("data") is None:
            extract_into_case(case, case.title, case.description)
            case.refresh_from_db()
        if payload.get("data"):
            created = add_observable(case, str(payload.get("dataType") or ""), str(payload["data"]))
            if created is None:
                return Response(
                    {
                        "type": "BadRequest",
                        "message": f"unknown dataType {payload.get('dataType')!r}",
                        "fields": {"dataType": ["not in the observable vocabulary"]},
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
    links = case.case_observables.select_related("observable__data_type").order_by("-created_at")
    return Response([observable_json(link) for link in links])


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_observable_list(request: Request, case_id: str) -> Response:
    """`GET /api/v1/case/{idOrNumber}/observable`."""
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    links = case.case_observables.select_related("observable__data_type").order_by("-created_at")
    return Response([observable_json(link) for link in links])


# Ledger kind → TheHive `OutputTimelineEvent.kind`. The 5.8.0 vocabulary has no spelling for
# our status/assignment/automation kinds, and `custom` is what thehive4py's own
# `InputCustomEvent` renders — mapping them onto a *wrong* named kind (`case.new` for an
# assignment) would be worse than the honest bucket. Unknown kinds also fall back to `custom`.
_TIMELINE_KIND_MAP = {
    "case-created": "case.created",
    "comment": "log.created",
    "alert-imported": "alert.occurred",
    "alert-merged": "alert.occurred",
    "status-change": "custom",
    "status-changed": "custom",
    "automation-run": "custom",
    "assigned": "custom",
}


def _timeline_event_wire(event: TimelineEvent) -> dict[str, Any]:
    """One ledger row as the 5.8.0 `OutputTimelineEvent` (plan deviation **P8-1**).

    Deliberately *not* `timeline_event_json`: that is the internal ledger shape the WS `sync`
    protocol and the UI templates consume, and Phase 7 owns it. Only this API serialization is
    TheHive-shaped — ms-epoch dates, the mapped `kind`, and the `Case`/`Alert` entity reference
    an alert event carries in its metadata.
    """
    metadata = event.metadata or {}
    alert_ref = metadata.get("alert_id")
    is_alert_event = event.kind in ("alert-imported", "alert-merged") and alert_ref
    return {
        "date": to_epoch_ms(event.date),
        "kind": _TIMELINE_KIND_MAP.get(event.kind, "custom"),
        "entity": "Alert" if is_alert_event else "Case",
        "entityId": f"~{alert_ref}" if is_alert_event else f"~{event.case_id}",
        "details": metadata,
        # A point-in-time event has no end; `null` is the schema's "not a range", not a gap.
        "endDate": to_epoch_ms(event.end_date) if event.end_date else None,
    }


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_timeline(request: Request, case_id: str) -> Response:
    """`GET /api/v1/case/{idOrNumber}/timeline` — the ledger as TheHive's `OutputTimeline`.

    The `{"events": [...]}` envelope (not a bare array) is what 5.8.0's OpenAPI and thehive4py's
    `case.get_timeline()` consume — recorded as plan deviation P8-1 in §13.
    """
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    events = TimelineEvent.objects.filter(case=case).order_by("date", "id")
    return Response({"events": [_timeline_event_wire(event) for event in events]})


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def observable_detail(request: Request, observable_id: str) -> Response:
    """`GET /api/v1/observable/{id}` — one artifact plus the cases it appears in (Module C)."""
    observable = Observable.objects.select_related("data_type").filter(pk=observable_id).first()
    if observable is None:
        return _not_found("Observable")
    payload = {
        "_id": str(observable.id),
        "id": str(observable.id),
        "_type": "observable",
        "dataType": observable.data_type.name,
        "data": observable.data,
        "normalizedData": observable.normalized_data,
        "enrichmentData": observable.enrichment_data or {},
        "tlp": observable.tlp,
        "pap": observable.pap,
        "cases": [
            {
                "_id": str(link.case_id),
                "number": link.case.number,
                "title": link.case.title,
            }
            for link in observable.case_observables.select_related("case").order_by("-created_at")
        ],
    }
    return Response(payload)


__all__ = [
    "case_collection",
    "case_detail",
    "case_observable_add",
    "case_observable_list",
    "case_task_create",
    "case_task_list",
    "case_timeline",
    "observable_detail",
]
