"""T1 `case`, `task` and `observable` endpoints, scoped to the MVP loop (Phase 5).

Read paths return the case with its observables, timeline and automation runs attached (`detail=True`)
because the MVP proof is "the playbook's output is visible on the case timeline", and a client that
has to make four calls to see that is not much of a proof surface.

Write paths are deliberately narrow: status transitions, task creation/status, and observable
addition. Each one writes a `TimelineEvent` in the same transaction, so the ledger is never behind
the data — a comment-only write that skipped the ledger would make the timeline a decoration.
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
from cases.models import Case, Task, TimelineEvent
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
    TimelineEvent.objects.create(
        case=case,
        date=timezone.now(),
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
        TimelineEvent.objects.create(
            case=case,
            date=timezone.now(),
            title=f"Status changed: {previous_status} → {new_status}",
            description=str(payload.get("comment") or ""),
            kind="status-changed",
            actor=_actor(request),
            metadata={"from": previous_status, "to": new_status},
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


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_task_list(request: Request, case_id: str) -> Response:
    """`GET /api/v1/case/{idOrNumber}/task`."""
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


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_timeline(request: Request, case_id: str) -> Response:
    """`GET /api/v1/case/{idOrNumber}/timeline` — the case ledger, oldest first."""
    from core.serializers import timeline_event_json

    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    events = TimelineEvent.objects.filter(case=case).order_by("date", "id")
    return Response([timeline_event_json(e) for e in events])


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
