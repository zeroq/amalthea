from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from alerts.escalation import link_alert_from_identifier
from alerts.models import Alert
from cases.create import create_case
from cases.ledger import append_timeline_event
from cases.models import (
    Case,
    CaseObservable,
    CustomField,
    TimelineEvent,
)
from cases.update import (
    CUSTOM_FIELD_ERROR_FIELD,
    CUSTOM_FIELD_UPDATES_PAYLOAD_KEY,
    CaseUpdateError,
    apply_case_update,
)
from compat.time import parse_timestamp, to_epoch_ms
from core.enums import (
    SUPER_ADMIN_ROLE,
)
from core.serializers import (
    CustomFieldWireError,
    alert_json,
    case_json,
    custom_event_json,
    custom_field_json,
    custom_field_updates_from_wire,
    observable_json,
    task_json,
)
from identity.audit import maybe_record_cross_org_read

from ._common import _actor, _as_uuid, _bad, _case_access, _case_org_scope, _not_found


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_collection(request: Request) -> Response:
    """`GET /api/v1/case` lists; `POST /api/v1/case` creates.

    Both verbs live in one view because they share the filter semantics, and two views would let
    those drift: a `?status=` that the list honours and the create path ignores is a quiet bug.
    """
    if request.method == "POST":
        return _create_case(request)
    # Scope to user's org (and shares) — uniform 404 for cross-tenant handled by case_visible_q
    queryset = Case.objects.select_related("status", "assignee").order_by("-start_date")
    queryset = queryset.filter(identity.scoping.case_visible_q(request.user))
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

    **Query Parameters (GET only):**
    - `timelineAfter` (UUID, optional): Cursor for timeline pagination (event ID to fetch after)
    - `timelineLimit` (int, optional): Page size for timeline (default 50, max 200)
    """
    case = identity.scoping.resolve_case(
        request.user, case_id, write=request.method in ("PATCH", "PUT", "DELETE")
    )
    if case is None:
        return _not_found("Case")
    if request.method == "DELETE":
        # DELETE is administrative: admin (own org only) or superAdmin (any org). A shared-org
        # case grants access, not ownership, so an admin cannot delete another org's case.
        if not identity.scoping.is_admin(request.user):
            return _not_found("Case")
        case = identity.scoping.resolve_case(request.user, case_id, write=True)
        if case is None:
            return _not_found("Case")
        if request.user.role != SUPER_ADMIN_ROLE and case.owner_org_id != request.user.org_id:
            return _not_found("Case")
        case.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method in ("PATCH", "PUT"):
        return _update_case(request, case)

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=case,
        target_type="Case",
        target_id=str(case.id),
        target_org_field="owner_org",
    )

    # GET with timeline pagination
    timeline_after = request.query_params.get("timelineAfter")
    timeline_limit = request.query_params.get("timelineLimit")
    limit = 50
    if timeline_limit:
        try:
            limit = max(1, min(int(timeline_limit), 200))  # cap at 200
        except ValueError:
            pass
    return Response(
        case_json(case, detail=True, timeline_after=timeline_after, timeline_limit=limit)
    )


def _create_case(request: Request) -> Response:
    """Create a case directly (no alert behind it) — the shared `cases.create.create_case` (P1).

    Thin wrapper in the `_update_case` style: the construction lives in the service, so the UI's
    create form posts to the exact same field set. The recorded API behaviour is preserved — a
    missing title is the same 400 envelope, an actor without an organisation is the same 403 body
    (two keys, as before the service existed), a success is the same 201 `case_json(detail=True)`
    with the same "Case created" ledger entry.
    """
    # Build a mutable copy first — request.data may be an immutable QueryDict (form-encoded body)
    # QueryDict.dict() returns last-value-per-key; plain dict is already fine.
    from django.http import QueryDict

    if isinstance(request.data, QueryDict):
        payload = request.data.dict()
    elif isinstance(request.data, dict):
        payload = dict(request.data)
    else:
        payload = {}
    # Map wire names (camelCase) to model field names (snake_case) for the service layer
    if "caseType" in payload:
        payload["case_type"] = payload.pop("caseType")
    if "dataClassification" in payload:
        payload["data_classification"] = payload.pop("dataClassification")
    user = _actor(request)
    try:
        case, _warnings = create_case(
            payload, actor=user, org=getattr(user, "org", None) if user else None
        )
    except CaseUpdateError as exc:
        if exc.error_type == "AuthorizationError":
            return Response(
                {"type": exc.error_type, "message": exc.message},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(exc.to_response_payload(), status=status.HTTP_400_BAD_REQUEST)
    return Response(case_json(case, detail=True), status=status.HTTP_201_CREATED)


@transaction.atomic
def _update_case(request: Request, case: Case) -> Response:
    """Thin wrapper delegating to the shared `apply_case_update` (P2 C2).

    API behaviour must not change; this preserves the exact 200/400 shapes and
    timeline kinds emitted by the original implementation.
    """
    # Build a mutable copy first — request.data may be an immutable QueryDict (form-encoded body)
    # QueryDict.dict() returns last-value-per-key; plain dict is already fine.
    from django.http import QueryDict

    payload: dict[str, Any]
    if isinstance(request.data, QueryDict):
        payload = dict(request.data.dict())
    elif isinstance(request.data, dict):
        payload = dict(request.data)
    else:
        payload = {}
    # Map wire names (camelCase) to model field names (snake_case) for the service layer
    if "caseType" in payload:
        payload["case_type"] = payload.pop("caseType")
    if "dataClassification" in payload:
        payload["data_classification"] = payload.pop("dataClassification")
    # Decode external custom-field names here; services receive only internal keys.
    try:
        custom_field_updates = custom_field_updates_from_wire(payload)
    except CustomFieldWireError as exc:
        return Response(
            {
                "type": "BadRequest",
                "message": exc.message,
                "fields": {"customFields": [exc.message]},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    if custom_field_updates is not None:
        payload.pop("customFields")
        payload[CUSTOM_FIELD_UPDATES_PAYLOAD_KEY] = custom_field_updates
    try:
        apply_case_update(case, payload, actor=_actor(request))
    except CaseUpdateError as exc:
        if exc.error_type == "AuthorizationError":
            return Response(
                {"type": exc.error_type, "message": exc.message},
                status=status.HTTP_403_FORBIDDEN,
            )
        response_payload = exc.to_response_payload()
        if CUSTOM_FIELD_ERROR_FIELD in response_payload["fields"]:
            response_payload["fields"]["customFields"] = response_payload["fields"].pop(
                CUSTOM_FIELD_ERROR_FIELD
            )
        return Response(response_payload, status=status.HTTP_400_BAD_REQUEST)
    return Response(case_json(case, detail=True))


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
    case = identity.scoping.resolve_case(request.user, case_id, write=True)
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
    case = identity.scoping.resolve_case(request.user, case_id, write=True)
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
    case = identity.scoping.resolve_case(request.user, case_id, write=False)
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

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=case,
        target_type="Case",
        target_id=str(case.id),
        target_org_field="owner_org",
    )

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
