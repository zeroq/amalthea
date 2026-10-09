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

import hashlib
import re
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.escalation import (
    link_alert_from_identifier,
    link_case_from_identifier,
    resolve_case_status,
)
from alerts.models import Alert
from cases.ledger import append_timeline_event
from cases.models import (
    Attachment,
    Case,
    CaseObservable,
    CaseStatus,
    CaseTagLink,
    Comment,
    CustomField,
    Page,
    Share,
    Tag,
    Task,
    TimelineEvent,
)
from cases.tagging import tag_names_from_payload
from compat.time import parse_timestamp, to_epoch_ms
from core.enums import CASE_STAGES, TASK_STATUS_CHOICES
from core.serializers import (
    alert_json,
    attachment_json,
    case_json,
    case_status_json,
    comment_json,
    custom_event_json,
    custom_field_json,
    observable_json,
    page_json,
    share_json,
    tag_json,
    task_json,
)
from identity.models import Organisation
from observables.extractor import add_observable, extract_into_case
from observables.models import Observable, ObservableTagLink, ObservableType
from realtime.publisher import publish_case_event

_TASK_STATUSES = tuple(choice for choice, _label in TASK_STATUS_CHOICES)


def _actor(request: Request) -> Any:
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def _not_found(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"}, status=status.HTTP_404_NOT_FOUND
    )


def _bad(message: str, fields: dict[str, list[str]]) -> Response:
    return Response(
        {"type": "BadRequest", "message": message, "fields": fields},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _resolve_case(identifier: str) -> Case | None:
    try:
        return link_case_from_identifier(identifier)
    except Case.DoesNotExist:
        return None


def _as_uuid(value: Any) -> UUID | None:
    """Parse a path segment as a UUID without letting a malformed one reach the ORM.

    `filter(pk="not-a-uuid")` raises `ValidationError` out of the query compiler, which the
    exception handler does not map — a 500 where the contract promises a 404. Parsing here
    keeps "not found" and "malformed" the same honest answer at the path boundary.
    """
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce a JSON scalar to an integer, or `None` when it is not integer-shaped.

    `None` (absent) becomes `0`, matching the `order` default; booleans are rejected so that JSON
    `true` cannot silently become `1`.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _case_org_scope(request: Request) -> Q:
    """Org guard for rows reached *through* a case (deviation **P10-w**).

    A row whose case belongs to no organisation is visible to every authenticated caller —
    every API-created case today is org-NULL, so gating on `= user.org` alone would make
    every task lookup a 404 and turn the guard into a lock-out rather than a control. A row
    whose case *is* org-owned is visible only inside that organisation.
    """
    org_id = getattr(getattr(request, "user", None), "org_id", None)
    return Q(case__owner_org__isnull=True) | Q(case__owner_org_id=org_id)


def _case_access(request: Request, identifier: str, *, write: bool) -> Case | None:
    """Resolve a case the caller may reach, or `None` (which callers turn into a 404).

    This is the **share** guard (plan §6-P2, deviation **P10-w**). A case that belongs to no
    organisation stays visible to every authenticated caller — the same choice `_case_org_scope`
    makes, and the reason this cannot be `= user.org` alone. A case that *is* org-owned is visible
    only inside that organisation, or to an organisation holding a `Share`:
    `read` is enough to look, `write=True` also demands `permissions.write`. A share therefore only
    ever *adds* access for one tenant; it cannot narrow the owner's or leak across cases.
    """
    case = _resolve_case(identifier)
    if case is None:
        return None
    if case.owner_org_id is None:
        # A case that belongs to no organisation stays visible to every authenticated caller.
        return case
    org_id = getattr(getattr(request, "user", None), "org_id", None)
    if org_id is None:
        # An org-owned case with no org on the caller: deny rather than fall through to a
        # self-share lookup that could never match.
        return None
    if case.owner_org_id == org_id:
        return case
    shares = case.shares.filter(organisation_id=org_id)
    if write:
        shares = shares.filter(permissions__write=True)
    return case if shares.exists() else None


def _resolve_org(identifier: Any) -> Organisation | None:
    """A share names an organisation by UUID or by name; both are accepted, first by id."""
    if not identifier:
        return None
    pk = _as_uuid(identifier)
    if pk is not None:
        org = Organisation.objects.filter(pk=pk).first()
        if org is not None:
            return org
    return Organisation.objects.filter(name=str(identifier)).first()


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


@api_view(["GET", "PATCH", "PUT", "DELETE"])
@renderer_classes([JSONRenderer])
def case_detail(request: Request, case_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/case/{idOrNumber}` — read, change, or delete the case.

    Read returns observables, timeline and automation runs attached: the MVP proof is "the
    playbook's output is visible on the case timeline", and a client that needs four calls to see
    that is not much of a proof surface.

    The write half handles the status transition the triage queue is built on, and writes a
    `TimelineEvent` **in the same transaction** — a status change that is not on the ledger makes
    the ledger a decoration.

    DELETE is 5.8.0's "permanently delete" (plan §13 non-goals, deviation **P10-c**): the case row
    and its CASCADE children (tasks, ledger, observable links, custom-field values) go, while the
    alerts point here by `SET_NULL` — they survive, unlinked, with their own evidence intact. No
    ledger entry is written: the ledger dies with the case, and writing one first would be a
    tombstone nobody can read.
    """
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    if request.method == "DELETE":
        case.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
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


def case_task_create(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/task` — add a task to the case.

    Deliberately *not* `@api_view`-decorated: this is an inner helper that `case_task_list`
    calls with the request it already holds. Wrapping it would make it a fresh
    `WrappedAPIView.as_view()`, and calling that with a DRF `Request` runs
    `initialize_request()` on it — `Request.__init__` then asserts `isinstance(request,
    HttpRequest)` and the endpoint answers 500 for every POST. The route is registered once,
    against `case_task_list`, which dispatches GET/POST itself.
    """
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
        # Was `"Todo"`, which is not in `TASK_STATUS_CHOICES` and violates the
        # `task_status_valid` CHECK on Postgres (plan §13 bug fix, A3).
        status=str(payload.get("status") or "Waiting"),
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


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def task_detail(request: Request, task_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/task/{taskId}` — one task, T1 detail (plan §7.2).

    Org-scoped through the owning case (`_case_org_scope`, deviation **P10-w**): a task's UUID
    is guessable-shaped like any other id, and the id must not become a cross-organisation read
    path the moment `owner_org` starts being set.

    PATCH is "no field, no write" (ADR D11): only keys present in the body are touched, an
    empty body is a 400 naming that nothing arrived, and `status` is validated against
    `TASK_STATUS_CHOICES` rather than left for the database CHECK to turn into a 500.
    """
    task = _lookup_task(request, task_id)
    if task is None:
        return _not_found("Task")
    if request.method == "DELETE":
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_task(request, task)
    return Response(task_json(task))


def _lookup_task(request: Request, task_id: str) -> Task | None:
    pk = _as_uuid(task_id)
    if pk is None:
        return None
    return (
        Task.objects.select_related("case", "assignee")
        .filter(pk=pk)
        .filter(_case_org_scope(request))
        .first()
    )


@transaction.atomic
def _update_task(request: Request, task: Task) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []

    if "title" in payload:
        title = str(payload["title"] or "").strip()
        if not title:
            return _bad("title is required", {"title": ["required"]})
        task.title = title[:500]
        fields.append("title")
    for key in ("description", "group"):
        if key in payload:
            setattr(task, key, str(payload[key] or ""))
            fields.append(key)
    if "flag" in payload:
        task.flag = bool(payload["flag"])
        fields.append("flag")
    if "mandatory" in payload:
        task.mandatory = bool(payload["mandatory"])
        fields.append("mandatory")
    if "order" in payload:
        try:
            task.order = int(payload["order"])
        except (TypeError, ValueError):
            return _bad("order must be an integer", {"order": ["must be an integer"]})
        fields.append("order")
    if "status" in payload:
        value = str(payload["status"])
        if value not in _TASK_STATUSES:
            return _bad(
                f"unknown status {payload['status']!r}",
                {"status": [f"must be one of {', '.join(_TASK_STATUSES)}"]},
            )
        task.status = value
        fields.append("status")
    for key, attr in (
        ("dueDate", "due_date"),
        ("startDate", "started_at"),
        ("endDate", "ended_at"),
    ):
        if key not in payload:
            continue
        moment = parse_timestamp(payload[key])
        if moment is None and payload[key] is not None:
            return _bad(f"{key} is not a valid timestamp", {key: ["invalid timestamp"]})
        setattr(task, attr, moment)
        fields.append(attr)
    if "assignee" in payload:
        task.assignee = _resolve_login(payload["assignee"])
        fields.append("assignee")

    if not fields:
        return _bad("No updatable field supplied", {})
    task.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


def _resolve_login(value: Any) -> Any:
    """Login string → user (parity with `_update_case`); empty/`null` unassigns.

    An unknown login unassigns rather than raising: ADR D11 refuses to auto-create a user
    per unrecognised wire value, so the only safe readings of "not a user" are "nobody". The
    UUID fallback is parsed first because `filter(pk="not-a-uuid")` raises `ValidationError`
    out of the query compiler — an unassigned task must not become a 500.
    """
    if not value:
        return None
    from identity.models import User

    candidate = User.objects.filter(login=str(value)).first()
    if candidate is not None:
        return candidate
    pk = _as_uuid(value)
    return User.objects.filter(pk=pk).first() if pk is not None else None


def case_observable_add(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/observable` — attach one artifact, or re-run extraction.

    A plain helper rather than its own route (the `case_task_create` pattern): the collection
    is served by one view, `case_observable_list`, which dispatches GET/POST itself. That keeps
    both spellings — the slashless one thehive4py posts, and the slashed one — on the same
    handler, so POST can never 405 on a spelling that resolves (verifier V1).

    Deliberately *not* `@api_view`-decorated: wrapping it would make it a fresh
    `WrappedAPIView.as_view()`, and calling that with a DRF `Request` runs
    `initialize_request()` on it — `Request.__init__` then asserts `isinstance(request,
    HttpRequest)` and the endpoint answers 500 for every POST. Exactly the trap
    `case_task_create` documents; the two helpers must stay undecorated together.
    """
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


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_observable_list(request: Request, case_id: str) -> Response:
    """`GET` lists a case's observables; `POST` attaches one and lists the result.

    Both verbs share the route *and* the view, mirroring `case_task_list`. Registering GET on
    the slashless spelling and POST on the slashed one (the pre-verifier split) made
    `POST /api/v1/case/{id}/observable` — the exact spelling thehive4py 2.1.0 sends — answer
    405, because Django resolves the slashless path to the GET view before APPEND_SLASH can
    redirect. One view, both spellings: no spelling can 405.
    """
    if request.method == "POST":
        return case_observable_add(request, case_id)
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    links = case.case_observables.select_related("observable__data_type").order_by("-created_at")
    return Response([observable_json(link) for link in links])


@api_view(["DELETE"])
@renderer_classes([JSONRenderer])
def case_alert_remove(request: Request, case_id: str, alert_id: str) -> Response:
    """`DELETE /api/v1/case/{idOrNumber}/alert/{alertId}` — unlink, do not destroy (204).

    The distinction is the whole endpoint: the alert keeps its row, its `raw_payload` and its
    `source_ref`, and only the `case` FK is cleared — deleting it here would take the evidence
    with it. The removal lands in the *case's* ledger (the case outlives the link, so a reader
    of the case can still see the alert left), because a silently shrinking alert list is how a
    team loses track of what it already triaged.
    """
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    alert = _lookup_alert_for_case(case, alert_id)
    if isinstance(alert, Response):
        return alert
    alert.case = None
    alert.save(update_fields=["case", "updated_at"])
    append_timeline_event(
        case,
        title=f"Alert removed: {alert.title}",
        kind="alert-removed",
        actor=_actor(request),
        metadata={"alert_id": str(alert.id)},
    )
    return Response(status=status.HTTP_204_NO_CONTENT)


def _lookup_alert_for_case(case: Case, alert_id: str) -> Alert | Response:
    """Resolve `alert_id` inside `case`, or the 404 response to hand straight back.

    Resolved through `link_alert_from_identifier` so a UUID and an unambiguous `source_ref`
    behave exactly as they do on `/api/v1/alert/{alertId}`; "exists but linked elsewhere" and
    "does not exist" are the same 404 here, because the endpoint's contract is about *this*
    case's membership and neither answer should leak the other case's contents.
    """
    try:
        alert = link_alert_from_identifier(alert_id)
    except Alert.DoesNotExist:
        return _not_found("Alert")
    except Alert.MultipleObjectsReturned:
        return _not_found("Alert")
    if alert.case_id != case.pk:
        return _not_found("Alert")
    return alert


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_custom_event_create(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/customEvent` — write one ledger entry (201).

    `InputCustomEvent` requires `date` (epoch-ms) and `title`; the caller's `date` is the row's
    `date`, not the server clock, because an event back-dated to when the incident actually
    happened is the point of a timeline. It therefore has to be set **before** the row exists —
    `append_timeline_event` publishes the row it created, and patching the date afterwards would
    broadcast a timestamp the ledger no longer holds.
    """
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    payload = request.data if isinstance(request.data, dict) else {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return _bad("title is required", {"title": ["required"]})
    if payload.get("date") is None:
        return _bad("date is required", {"date": ["required"]})
    event_date = parse_timestamp(payload["date"])
    if event_date is None:
        return _bad("date is not a valid timestamp", {"date": ["invalid timestamp"]})
    end_date = None
    if payload.get("endDate") is not None:
        end_date = parse_timestamp(payload["endDate"])
        if end_date is None:
            return _bad("endDate is not a valid timestamp", {"endDate": ["invalid timestamp"]})
    event = append_timeline_event(
        case,
        # `TimelineEvent.title` is CharField(max_length=500): SQLite lets a longer value
        # through, Postgres raises a DataError (auditor F4), so the bound lives at the write
        # exactly where `case_task_create` puts it.
        title=title[:500],
        description=str(payload.get("description") or ""),
        kind="custom",
        actor=_actor(request),
        date=event_date,
        end_date=end_date,
    )
    return Response(custom_event_json(event), status=status.HTTP_201_CREATED)


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


@api_view(["PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def custom_event_detail(request: Request, event_id: str) -> Response:
    """`PATCH|DELETE /api/v1/customEvent/{eventId}` — one *analyst-authored* ledger entry.

    Only `kind="custom"` rows are writable. Every other kind is a system row — a status
    transition, an import, an automation result — and an API client that could delete one would
    be able to erase the audit trail the ledger exists to be. The refusal is a 400 naming the
    rule rather than a 403: the operation is invalid *for that row*, not a permission the caller
    lacks (plan §8).
    """
    event = _lookup_custom_event(request, event_id)
    if event is None:
        return _not_found("CustomEvent")
    if event.kind != "custom":
        return _bad(
            "Only custom events can be updated or deleted",
            {"_id": [f"ledger kind {event.kind!r} is system-managed"]},
        )
    if request.method == "DELETE":
        event.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    return _update_custom_event(event, request)


def _lookup_custom_event(request: Request, event_id: str) -> TimelineEvent | None:
    pk = _as_uuid(event_id)
    if pk is None:
        return None
    return (
        TimelineEvent.objects.select_related("case")
        .filter(pk=pk)
        .filter(_case_org_scope(request))
        .first()
    )


@transaction.atomic
def _update_custom_event(event: TimelineEvent, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    for key in ("title", "description"):
        if key in payload:
            value = str(payload[key] or "")
            if key == "title":
                if not value.strip():
                    return _bad("title is required", {"title": ["required"]})
                # CharField(max_length=500): bound it here like `case_task_create`, because
                # Postgres enforces the length and SQLite does not (auditor F4).
                value = value[:500]
            setattr(event, key, value)
            fields.append(key)
    for key, attr in (("date", "date"), ("endDate", "end_date")):
        if key not in payload:
            continue
        moment = parse_timestamp(payload[key])
        if moment is None:
            return _bad(f"{key} is not a valid timestamp", {key: ["invalid timestamp"]})
        setattr(event, attr, moment)
        fields.append(attr)
    if not fields:
        return _bad("No updatable field supplied", {})
    event.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def custom_field_list(request: Request) -> Response:
    """`GET /api/v1/customField` — the field definitions a client renders values against.

    Definitions are organisation-neutral by design (plan §4: one `CustomField` serves both case
    and alert values), so this is the rare list that is deliberately *not* org-scoped: a field a
    case already carries must render for whoever opens that case.
    """
    return Response(
        [custom_field_json(field) for field in CustomField.objects.all().order_by("name")]
    )


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def observable_detail(request: Request, observable_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/observable/{id}` — one artifact plus the cases it appears in.

    PATCH/DELETE are deliberately **not** org-scoped (plan §5, A5): an `Observable` is globally
    deduplicated (Module C), so the same row is shared by every case that ever saw the value and
    an org filter would let one organisation edit — or refuse to edit — evidence another owns.
    The permission check is therefore the auth-level one (authenticated, writable key), exactly
    as the GET half has always been.

    `dataType` is the interesting PATCH field: re-typing changes the case-fold rule the identity
    digest is taken under, and `Observable.save()` re-hashes `data_hash` in the same write —
    without which `(data_type, data_hash)` would still describe the old type and the unique
    constraint would admit a duplicate of a value already on file.
    """
    pk = _as_uuid(observable_id)
    if pk is None:
        return _not_found("Observable")
    observable = Observable.objects.select_related("data_type").filter(pk=pk).first()
    if observable is None:
        return _not_found("Observable")
    if request.method == "DELETE":
        observable.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_observable(observable, request)
    return _observable_detail_payload(observable)


@transaction.atomic
def _update_observable(observable: Observable, request: Request) -> Response:
    """Apply `InputUpdateObservable`'s fields. Only-present, no field, no write.

    Returns 204 (the recorded OpenAPI, and what thehive4py's `observable.update()` reads as
    `None`) rather than the echoed body the alert/case PATCHes return — the two surfaces were
    recorded from different operations and are kept as recorded.
    """
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []

    if "dataType" in payload:
        obs_type = ObservableType.objects.filter(name=str(payload["dataType"])).first()
        if obs_type is None:
            return _bad(
                f"unknown dataType {payload['dataType']!r}",
                {"dataType": ["not in the observable vocabulary"]},
            )
        observable.data_type = obs_type
        fields.append("data_type")
    if "message" in payload:
        observable.message = str(payload["message"] or "")
        fields.append("message")
    for key in ("tlp", "pap"):
        if key in payload:
            try:
                value = int(payload[key])
            except (TypeError, ValueError):
                value = -1
            if not 0 <= value <= 3:
                return _bad(
                    f"{key} must be an integer between 0 and 3",
                    {key: ["must be an integer between 0 and 3"]},
                )
            setattr(observable, key, value)
            fields.append(key)
    for key, attr in (
        ("ioc", "ioc"),
        ("sighted", "sighted"),
        ("ignoreSimilarity", "ignore_similarity"),
    ):
        if key in payload:
            setattr(observable, attr, bool(payload[key]))
            fields.append(attr)

    if not fields:
        return _bad("No updatable field supplied", {})
    observable.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


def _observable_detail_payload(observable: Observable) -> Response:
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


# --- T2: vocabularies (statuses), tags and tag links ------------------------


def _resolve_tag(identifier: str) -> Tag | None:
    """Resolve `{tagId}` as a UUID first, then as the unique `name`."""
    pk = _as_uuid(identifier)
    if pk is not None:
        found = Tag.objects.filter(pk=pk).first()
        if found is not None:
            return found
    return Tag.objects.filter(name=identifier).first()


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def tag_collection(request: Request) -> Response:
    """`GET /api/v1/tag` lists; `POST /api/v1/tag` creates (201).

    TheHive 5.8 exposes only `GET|PATCH|DELETE /tag/{tagId}` — no collection — and mints tags
    implicitly when they are attached. This REST collection is an Amalthea extension
    (`docs/spec/deviations.md`) so a tag can be described before it is used.
    """
    if request.method == "POST":
        return _create_tag(request)
    return Response([tag_json(tag) for tag in Tag.objects.order_by("name")])


def _create_tag(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    name = str(payload.get("name") or payload.get("predicate") or "").strip()
    if not name:
        return _bad("name is required", {"name": ["required"]})
    if Tag.objects.filter(name=name[:100]).exists():
        return _bad("name already exists", {"name": ["already exists"]})
    tag = Tag.objects.create(
        name=name[:100],
        colour=str(payload.get("colour") or "")[:20],
        description=str(payload.get("description") or ""),
    )
    return Response(tag_json(tag), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def tag_detail(request: Request, tag_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/tag/{tagId}`.

    DELETE is guarded: a tag still attached to a case, alert or observable is a **400**. TheHive
    cascades, but the tag *is* the classification and removing it everywhere silently would be
    the data loss this endpoint exists to refuse (recorded in `docs/spec/deviations.md`). PATCH
    accepts the recorded `InputUpdateTag` fields (`predicate`, `description`, `colour`) and our
    `name` spelling.
    """
    tag = _resolve_tag(tag_id)
    if tag is None:
        return _not_found("Tag")
    if request.method == "DELETE":
        if _tag_in_use(tag):
            return _bad(
                "Tag is in use",
                {"_id": ["a case, alert or observable still carries this tag"]},
            )
        tag.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_tag(tag, request)
    return Response(tag_json(tag))


def _tag_in_use(tag: Tag) -> bool:
    # `exists()` per link table: a tag carried by any of the three owner kinds is in use.
    return bool(
        tag.case_links.exists() or tag.alert_links.exists() or tag.observable_links.exists()
    )


def _update_tag(tag: Tag, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "predicate" in payload or "name" in payload:
        key = "predicate" if "predicate" in payload else "name"
        name = str(payload[key] or "").strip()
        if not name:
            return _bad(f"{key} is required", {key: ["required"]})
        if Tag.objects.exclude(pk=tag.pk).filter(name=name[:100]).exists():
            return _bad("name already exists", {key: ["already exists"]})
        tag.name = name[:100]
        fields.append("name")
    if "description" in payload:
        tag.description = str(payload["description"] or "")
        fields.append("description")
    if "colour" in payload:
        tag.colour = str(payload["colour"] or "")[:20]
        fields.append("colour")
    if not fields:
        return _bad("No updatable field supplied", {})
    tag.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


def _resolve_case_status(identifier: str) -> CaseStatus | None:
    """Resolve `{idOrValue}` as a UUID first, then as the unique `value`."""
    pk = _as_uuid(identifier)
    if pk is not None:
        found = CaseStatus.objects.filter(pk=pk).first()
        if found is not None:
            return found
    return CaseStatus.objects.filter(value=identifier).first()


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_status_collection(request: Request) -> Response:
    """`GET /api/v1/caseStatus` lists; `POST /api/v1/caseStatus` creates (201)."""
    if request.method == "POST":
        return _create_case_status(request)
    return Response(
        [
            case_status_json(status_obj)
            for status_obj in CaseStatus.objects.order_by("order", "value")
        ]
    )


def _create_case_status(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    value = str(payload.get("value") or "").strip()
    stage = str(payload.get("stage") or "").strip()
    missing = {key: ["required"] for key, val in (("value", value), ("stage", stage)) if not val}
    if missing:
        return _bad("value and stage are required", missing)
    if stage not in CASE_STAGES:
        return _bad(
            f"unknown stage {stage!r}",
            {"stage": [f"must be one of {', '.join(CASE_STAGES)}"]},
        )
    if CaseStatus.objects.filter(value=value[:64]).exists():
        return _bad("value already exists", {"value": ["already exists"]})
    try:
        order = int(payload.get("order", 0))
    except (TypeError, ValueError):
        return _bad("order must be an integer", {"order": ["must be an integer"]})
    status_obj = CaseStatus.objects.create(
        value=value[:64],
        stage=stage,
        order=order,
        description=str(payload.get("description") or ""),
        hidden=bool(payload.get("hidden") or False),
    )
    return Response(case_status_json(status_obj), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def case_status_detail(request: Request, status_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/caseStatus/{idOrValue}`.

    `value` and `stage` are immutable (the recorded `InputCreateCaseStatus` says so), so PATCH only
    moves `order`/`description`/`hidden`. DELETE refuses a status still in use with a **400**;
    `Case.status` is `PROTECT` and the database's 500 is not the contract a triage queue wants.
    """
    status_obj = _resolve_case_status(status_id)
    if status_obj is None:
        return _not_found("CaseStatus")
    if request.method == "DELETE":
        if status_obj.cases.exists():
            return _bad(
                "Case status is in use",
                {"_id": ["at least one case uses this status"]},
            )
        status_obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_case_status(status_obj, request)
    return Response(case_status_json(status_obj))


def _update_case_status(status_obj: CaseStatus, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "order" in payload:
        try:
            status_obj.order = int(payload["order"])
        except (TypeError, ValueError):
            return _bad("order must be an integer", {"order": ["must be an integer"]})
        fields.append("order")
    if "description" in payload:
        status_obj.description = str(payload["description"] or "")
        fields.append("description")
    if "hidden" in payload:
        status_obj.hidden = bool(payload["hidden"])
        fields.append("hidden")
    if not fields:
        return _bad("No updatable field supplied", {})
    status_obj.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


def _requested_tags(request: Request) -> list[str] | Response:
    """The tag names in the body, or a 400 response when none were supplied."""
    payload = request.data if isinstance(request.data, dict) else {}
    names = tag_names_from_payload(payload)
    if names is None:
        return _bad("tags is required", {"tags": ["required"]})
    return names


@api_view(["POST", "DELETE"])
@renderer_classes([JSONRenderer])
def case_tag_link(request: Request, case_id: str) -> Response:
    """`POST|DELETE /api/v1/case/{idOrNumber}/tag` — attach or detach tags (200 with the set).

    TheHive 5.8 has no such route; tags arrive through the case create/update body. A dedicated
    link endpoint keeps the operation explicit and idempotent — re-linking and un-linking an
    absent tag are both no-ops — and returns the case's current tag set so the caller can render
    it without a follow-up read (recorded in `docs/spec/deviations.md`).
    """
    case = _resolve_case(case_id)
    if case is None:
        return _not_found("Case")
    names = _requested_tags(request)
    if isinstance(names, Response):
        return names
    if request.method == "DELETE":
        CaseTagLink.objects.filter(case=case, tag__name__in=names).delete()
    else:
        for name in names:
            tag, _created = Tag.objects.get_or_create(name=name)
            CaseTagLink.objects.get_or_create(case=case, tag=tag)
    return Response([tag_json(tag) for tag in case.tags.order_by("name")])


@api_view(["POST", "DELETE"])
@renderer_classes([JSONRenderer])
def observable_tag_link(request: Request, observable_id: str) -> Response:
    """`POST|DELETE /api/v1/observable/{id}/tag` — attach or detach tags (200 with the set).

    Deliberately **not** org-scoped, matching `observable_detail` (plan §5/A5): an observable is
    globally deduplicated, so the same row is shared by every case that ever saw the value and an
    org filter could not apply without splitting the artifact.
    """
    pk = _as_uuid(observable_id)
    observable = Observable.objects.filter(pk=pk).first() if pk is not None else None
    if observable is None:
        return _not_found("Observable")
    names = _requested_tags(request)
    if isinstance(names, Response):
        return names
    if request.method == "DELETE":
        ObservableTagLink.objects.filter(observable=observable, tag__name__in=names).delete()
    else:
        for name in names:
            tag, _created = Tag.objects.get_or_create(name=name)
            ObservableTagLink.objects.get_or_create(observable=observable, tag=tag)
    return Response([tag_json(tag) for tag in observable.tags.order_by("name")])


# --- T2 P2 — collaboration: comments, pages, shares, flow ------------------


def _comment_message(request: Request) -> str | Response:
    """The `message` in the body, or a 400 response when it is missing or blank."""
    payload = request.data if isinstance(request.data, dict) else {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return _bad("message is required", {"message": ["required"]})
    return message


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def case_comment_list(request: Request, case_id: str) -> Response:
    """`GET|POST /api/v1/case/{idOrNumber}/comment` — list or add case comments.

    A created comment is also appended to the ledger as a `comment` event in the same transaction:
    the `Comment` row is TheHive's `OutputComment`, while the ledger is what the live case view
    renders, and an entry present in only one of the two would be invisible to half the product.
    The ledger append is what publishes the WebSocket event (AC6.1-P2-a).
    """
    case = _case_access(request, case_id, write=request.method == "POST")
    if case is None:
        return _not_found("Case")
    if request.method == "GET":
        comments = case.comments.select_related("created_by", "updated_by").order_by(
            "-created_at", "-id"
        )
        return Response([comment_json(c) for c in comments])
    message = _comment_message(request)
    if isinstance(message, Response):
        return message
    comment = Comment.objects.create(case=case, message=message, created_by=_actor(request))
    append_timeline_event(
        case,
        title="Comment",
        description=message,
        kind="comment",
        actor=_actor(request),
        metadata={"comment": str(comment.id)},
    )
    return Response(comment_json(comment), status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def comment_detail(request: Request, comment_id: str) -> Response:
    """`PATCH|DELETE /api/v1/comment/{id}` — edit or remove a comment (TheHive 5.8).

    A comment reached through a case obeys the case's share guard: a read-only share can list the
    comment but cannot edit or delete it (AC6.1-P2-b).
    """
    pk = _as_uuid(comment_id)
    comment = (
        Comment.objects.select_related("case").filter(pk=pk).first() if pk is not None else None
    )
    if comment is None:
        return _not_found("Comment")
    if comment.case_id and _case_access(request, str(comment.case_id), write=True) is None:
        return _not_found("Comment")
    if request.method == "DELETE":
        comment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    message = _comment_message(request)
    if isinstance(message, Response):
        return message
    comment.message = message
    comment.updated_by = _actor(request)
    comment.save(update_fields=["message", "updated_by", "updated_at"])
    return Response(comment_json(comment))


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def case_page_list(request: Request, case_id: str) -> Response:
    """`GET|POST /api/v1/case/{idOrNumber}/page` — list or create case pages (Markdown).

    A created page publishes a `page` WebSocket event on commit, so a second tab sees it without a
    reload (AC6.1-P2-a). It is deliberately **not** a `TimelineEvent`: a page is mutable state, and
    an immutable ledger row per edit would misrepresent it as an event.
    """
    case = _case_access(request, case_id, write=request.method == "POST")
    if case is None:
        return _not_found("Case")
    if request.method == "GET":
        pages = case.pages.select_related("created_by", "updated_by").order_by(
            "order", "created_at"
        )
        return Response([page_json(page) for page in pages])
    payload = request.data if isinstance(request.data, dict) else {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return _bad("title is required", {"title": ["required"]})
    order = _as_int(payload.get("order"))
    if order is None:
        return _bad("order must be an integer", {"order": ["invalid"]})
    page = Page.objects.create(
        case=case,
        title=title[:500],
        content=str(payload.get("content") or ""),
        order=order,
        category=str(payload.get("category") or "")[:100],
        created_by=_actor(request),
    )
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "page", {"event": page_json(page)})
    )
    return Response(page_json(page), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def case_page_detail(request: Request, case_id: str, page_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/case/{idOrNumber}/page/{pageId}` (TheHive 5.8)."""
    case = _case_access(request, case_id, write=request.method in ("PATCH", "DELETE"))
    if case is None:
        return _not_found("Case")
    page_pk = _as_uuid(page_id)
    page = Page.objects.filter(pk=page_pk, case=case).first() if page_pk is not None else None
    if page is None:
        return _not_found("Page")
    if request.method == "DELETE":
        page.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "GET":
        return Response(page_json(page))
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "title" in payload:
        title = str(payload["title"] or "").strip()
        if not title:
            return _bad("title must not be empty", {"title": ["blank"]})
        page.title = title[:500]
        fields.append("title")
    if "content" in payload:
        page.content = str(payload["content"] or "")
        fields.append("content")
    if "category" in payload:
        page.category = str(payload["category"] or "")[:100]
        fields.append("category")
    if "order" in payload:
        order = _as_int(payload["order"])
        if order is None:
            return _bad("order must be an integer", {"order": ["invalid"]})
        page.order = order
        fields.append("order")
    if not fields:
        return _bad("No updatable field supplied", {})
    page.updated_by = _actor(request)
    page.save(update_fields=[*fields, "updated_by", "updated_at"])
    return Response(page_json(page))


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_flow(request: Request, case_id: str) -> Response:
    """`GET /api/v1/case/{idOrNumber}/flow` — the case and everything linked to it.

    TheHive 5.8 has no REST `flow` route and thehive4py 2.1.0 has no `flow` module, so this is an
    **extension** shaped after the entities it links: the case, its alerts, its observables and its
    tasks in one read (recorded as deviation **P2-4**). `raw_payload` is never inlined.
    """
    case = _case_access(request, case_id, write=False)
    if case is None:
        return _not_found("Case")
    alerts = case.alerts.select_related("status", "assignee").order_by("-date")
    observables = (
        CaseObservable.objects.select_related("observable__data_type")
        .filter(case=case)
        .order_by("-created_at")
    )
    return Response(
        {
            "_type": "flow",
            "case": case_json(case),
            "alerts": [alert_json(alert) for alert in alerts],
            "observables": [observable_json(link) for link in observables],
            "tasks": [task_json(task) for task in case.tasks.select_related("assignee")],
        }
    )


@api_view(["GET", "POST", "PUT"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def case_share_list(request: Request, case_id: str) -> Response:
    """`GET|POST|PUT /api/v1/case/{idOrNumber}/shares` — read, add or replace shares.

    **Default deny** (plan §6-P2): reading the share list needs read access, mutating it needs
    write access, so an organisation the case was merely shared *read-only* with cannot widen its
    own grant. `POST` adds (TheHive's `share`); `PUT` replaces the whole set (TheHive's `set_share`).
    A share only ever adds access for one tenant and cannot touch the owner's.
    """
    case = _case_access(request, case_id, write=request.method in ("POST", "PUT"))
    if case is None:
        return _not_found("Case")
    if request.method == "GET":
        shares = case.shares.select_related("organisation").order_by("created_at")
        return Response([share_json(share) for share in shares])
    payload = request.data if isinstance(request.data, dict) else {}
    entries = payload.get("shares")
    if not isinstance(entries, list) or not entries:
        return _bad("shares is required", {"shares": ["required"]})
    resolved: list[tuple[Organisation, dict[str, Any]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return _bad("each share must be an object", {"shares": ["invalid"]})
        organisation = _resolve_org(entry.get("organisation"))
        if organisation is None:
            return _bad("unknown organisation", {"organisation": ["unknown"]})
        permissions = entry.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {"write": bool(entry.get("write"))}
        permissions = {"read": True, "write": bool(permissions.get("write"))}
        resolved.append((organisation, permissions))
    if request.method == "PUT":
        case.shares.all().delete()
    for organisation, permissions in resolved:
        if request.method == "PUT":
            Share.objects.create(
                case=case,
                organisation=organisation,
                permissions=permissions,
                created_by=_actor(request),
            )
        else:
            Share.objects.get_or_create(
                case=case,
                organisation=organisation,
                defaults={"permissions": permissions, "created_by": _actor(request)},
            )
    shares = case.shares.select_related("organisation").order_by("created_at")
    return Response([share_json(share) for share in shares])


@api_view(["DELETE"])
@renderer_classes([JSONRenderer])
def share_detail(request: Request, case_id: str, share_id: str) -> Response:
    """`DELETE /api/v1/case/{idOrNumber}/share/{shareId}` — revoke one share (TheHive 5.8)."""
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")
    share_pk = _as_uuid(share_id)
    share = Share.objects.filter(pk=share_pk, case=case).first() if share_pk is not None else None
    if share is None:
        return _not_found("Share")
    share.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# --- Attachments (T2 P3) ---------------------------------------------------
#
# The highest-risk surface of the T2 wave: client-controlled bytes and a client-controlled
# filename. Two rules hold throughout — the client's name is *display data only* and never a path
# component, and the size is checked before a byte reaches permanent storage.

# Concrete types only. A wildcard like `application/octet-stream` is deliberately absent: it would
# declare nothing and defeat the content sniff below.
_ATTACHMENT_ALLOWED_TYPES = frozenset(
    {
        "text/plain",
        "text/csv",
        "text/markdown",
        "text/x-python",
        "application/json",
        "application/x-ndjson",
        "application/pdf",
        "application/zip",
        "application/xml",
        "text/xml",
        "image/png",
        "image/jpeg",
        "image/gif",
    }
)

# Magic-byte signatures for the binary types above. A declared type that has a signature here must
# match the bytes; a text/JSON/XML type must instead decode as UTF-8 (the fallback in `_sniff`).
_ATTACHMENT_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
)


def _unsupported(message: str) -> Response:
    return Response(
        {"type": "UnsupportedMediaType", "message": message},
        status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    )


def _too_large(message: str) -> Response:
    return Response(
        {"type": "TooLarge", "message": message},
        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


def _safe_attachment_name(raw: str) -> str:
    """A display filename reduced to one harmless segment.

    The stored blob never uses this (see `_attachment_storage_path`), but it is stored and echoed in
    `Content-Disposition`, so `../../etc/passwd` or an embedded NUL must not survive into a header
    or a log line.
    """
    name = Path((raw or "").replace("\x00", "")).name.strip()
    if not name or name in {".", ".."}:
        return "attachment"
    return name[:255]


def _attachment_storage_path(name: str) -> str:
    """An opaque, collision-free storage key under the configured prefix.

    The extension comes from the client's name but is re-validated to `[a-z0-9]` and length-capped,
    so a crafted suffix cannot reintroduce a path separator. The stem is a server-side uuid4, so two
    uploads of the same name never share a blob and there is nothing for a traversal to climb.
    """
    prefix = getattr(settings, "ATTACHMENT_STORAGE_PREFIX", "attachments")
    suffix = Path(name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ""
    return f"{prefix}/{uuid4().hex}{suffix}"


def _sniff_attachment(content_type: str, data: bytes) -> bool:
    """True when `data` is consistent with the declared `content_type`.

    A file whose bytes start with a known signature must declare that signature's type (so PNG
    bytes sent as `text/plain`, or a PDF sent as `image/png`, is a 415). Anything the signature
    table does not know must at least decode as UTF-8 when it claims to be text-ish, which rejects
    a binary smuggled in under a text type. This is a sanity check, not a full MIME detector.
    """
    for signature, declared in _ATTACHMENT_SIGNATURES:
        if data.startswith(signature):
            return declared == content_type
    if content_type.startswith("text/") or content_type in {
        "application/json",
        "application/x-ndjson",
        "application/xml",
    }:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
    return True


def _find_attachment(case: Case, attachment_id: str) -> Attachment | None:
    """An attachment reached *through* its case, so a foreign case id cannot address it."""
    pk = _as_uuid(attachment_id)
    if pk is None:
        return None
    return Attachment.objects.filter(pk=pk, case=case).first()


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_attachment_list(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/attachments` — multipart upload (TheHive 5.8).

    Accepts one or more files under the repeated `attachments` field and answers with TheHive's
    wrapper `{"attachments": [...]}`, not a bare list. Every file is size-checked and
    content-checked **before** anything is written to storage, so a rejected upload leaves neither a
    blob nor a row (AC6.1-P3-a). The client's filename is stored as `name` only; the blob name is
    generated server-side.
    """
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")
    uploads = request.FILES.getlist("attachments")
    if not uploads:
        return _bad("attachments is required", {"attachments": ["required"]})
    max_bytes = int(getattr(settings, "ATTACHMENT_MAX_BYTES", 25 * 1024 * 1024))

    # Phase 1 — validate all of them before a single byte hits permanent storage.
    prepared: list[tuple[str, str, bytes]] = []
    for upload in uploads:
        content_type = (upload.content_type or "").split(";")[0].strip().lower()
        if content_type not in _ATTACHMENT_ALLOWED_TYPES:
            return _unsupported(f"content type {content_type or 'unknown'} is not allowed")
        if upload.size is None or upload.size > max_bytes:
            return _too_large(f"attachment exceeds {max_bytes} bytes")
        data = upload.read()
        if len(data) > max_bytes:
            return _too_large(f"attachment exceeds {max_bytes} bytes")
        if not data:
            return _bad("an empty file is not an attachment", {"attachments": ["empty"]})
        if not _sniff_attachment(content_type, data):
            return _unsupported(f"content does not match declared type {content_type}")
        prepared.append((_safe_attachment_name(upload.name or ""), content_type, data))

    # Phase 2 — write the blobs, then the rows. A failure mid-way deletes every blob written, so a
    # rejected request cannot strand an unreferenced file in the attachment volume.
    stored: list[str] = []
    attachments: list[Attachment] = []
    try:
        with transaction.atomic():
            for name, content_type, data in prepared:
                stored.append(
                    default_storage.save(_attachment_storage_path(name), ContentFile(data))
                )
                attachments.append(
                    Attachment.objects.create(
                        case=case,
                        name=name,
                        content_type=content_type,
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        path=stored[-1],
                        created_by=_actor(request),
                    )
                )
    except Exception:
        for path in stored:
            default_storage.delete(path)
        raise
    return Response(
        {"attachments": [attachment_json(item) for item in attachments]},
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_attachment_download(request: Request, case_id: str, attachment_id: str) -> Response:
    """`GET .../attachment/{id}/download` — stream the blob under its original name (TheHive 5.8).

    Read access is enough. The response carries the stored `name` and `content_type`; the recorded
    `sha256` is what a caller (or a test) compares the body against to prove the same bytes came
    back (AC6.1-P3-b).
    """
    case = _case_access(request, case_id, write=False)
    if case is None:
        return _not_found("Case")
    attachment = _find_attachment(case, attachment_id)
    if attachment is None or not default_storage.exists(attachment.path):
        return _not_found("Attachment")
    return FileResponse(
        default_storage.open(attachment.path, "rb"),
        as_attachment=True,
        filename=attachment.name,
        content_type=attachment.content_type,
    )


@api_view(["DELETE"])
@renderer_classes([JSONRenderer])
def case_attachment_detail(request: Request, case_id: str, attachment_id: str) -> Response:
    """`DELETE /api/v1/case/{idOrNumber}/attachment/{id}` — remove a case attachment (TheHive 5.8).

    The row goes first: a missing blob must not turn a delete into a 500, and a row without a blob
    is a broken download while a blob without a row is only wasted bytes.
    """
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")
    attachment = _find_attachment(case, attachment_id)
    if attachment is None:
        return _not_found("Attachment")
    path = attachment.path
    attachment.delete()
    default_storage.delete(path)
    return Response(status=status.HTTP_204_NO_CONTENT)


__all__ = [
    "case_alert_remove",
    "case_attachment_detail",
    "case_attachment_download",
    "case_attachment_list",
    "case_collection",
    "case_comment_list",
    "case_custom_event_create",
    "case_detail",
    "case_flow",
    "case_observable_add",
    "case_observable_list",
    "case_page_detail",
    "case_page_list",
    "case_share_list",
    "case_status_collection",
    "case_status_detail",
    "case_tag_link",
    "case_task_create",
    "case_task_list",
    "case_timeline",
    "comment_detail",
    "custom_event_detail",
    "custom_field_list",
    "observable_detail",
    "observable_tag_link",
    "share_detail",
    "tag_collection",
    "tag_detail",
    "task_detail",
]
