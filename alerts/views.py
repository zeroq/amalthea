"""T1 `alert` endpoints, scoped to the MVP loop (Phase 4/5).

The full T1 surface (query filters, bulk ops, custom fields) is Phase 8/10 work. What the loop
needs is: read an alert, read its raw payload, and escalate it. Those are here, and they speak the
TheHive-ish envelope the rest of the codebase already documents (`_id` for the id, `type`/`message`
for errors) so a client written against the recorded 5.8.0 examples keeps working as the surface
grows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import UUID

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.escalation import (
    import_alert_to_case,
    link_alert_from_identifier,
    link_case_from_identifier,
    merge_alert_into_case,
    resolve_alert_status,
)
from alerts.models import Alert, AlertObservable, AlertStatus, AlertTagLink
from cases.models import Case, Comment, Tag
from cases.tagging import tag_names_from_payload
from core.enums import ALERT_STAGES
from core.serializers import (
    alert_json,
    alert_status_json,
    case_json,
    comment_json,
    observable_json,
    tag_json,
)
from observables.extractor import resolve_observable
from observables.models import ObservableType
from realtime.publisher import publish_case_event


def _actor(request: Request) -> Any:
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


@api_view(["GET", "PATCH", "PUT", "DELETE"])
@renderer_classes([JSONRenderer])
def alert_detail(request: Request, alert_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/alert/{alertId}` — read, triage or delete one alert.

    The identifier is a UUID or an unambiguous `source_ref`; an ambiguous `source_ref` is a 400
    naming the ambiguity, not a 404 and not a silent pick of the first row.

    DELETE is the recorded 5.8.0 "permanently delete": the row goes, its `AlertObservable`
    children go with it (CASCADE), and there is no recycle bin to appeal to (plan §13 non-goals).
    """
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert
    if request.method == "DELETE":
        alert.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method in ("PATCH", "PUT"):
        return _update_alert(request, alert)
    return Response(alert_json(alert))


def _lookup_alert(alert_id: str) -> Alert | Response:
    """Resolve the identifier, or return the error response to hand straight back."""
    try:
        return link_alert_from_identifier(alert_id)
    except Alert.DoesNotExist:
        return Response(
            {"type": "NotFoundError", "message": "Alert not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Alert.MultipleObjectsReturned as exc:
        return Response(
            {"type": "BadRequest", "message": str(exc), "fields": {"alertId": [str(exc)]}},
            status=status.HTTP_400_BAD_REQUEST,
        )


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def alert_raw(request: Request, alert_id: str) -> Response:
    """`GET /api/v1/alert/{alertId}/raw` — the byte-preserved ingestion payload (D9).

    D9 promised this returns what the sender sent, not our normalized view of it. That is why it is
    a separate endpoint instead of a flag on `alert_detail`: re-mapped fields would defeat it.
    """
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert
    return Response(alert.raw_payload)


def _bad(message: str, fields: dict[str, list[str]]) -> Response:
    return Response(
        {"type": "BadRequest", "message": message, "fields": fields},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_observable_add(request: Request, alert_id: str) -> Response:
    """`POST /api/v1/alert/{alertId}/observable` — attach artifacts to the alert itself (201).

    Two decisions worth the docstring, both recorded as deviations:

    * **No automation dispatch (P10-b).** `dispatch_observable_linked` needs a case, and an
      alert-only observable has none: it is a *candidate* artifact TheHive records against the
      alert until an import/merge gives it a case to fire playbooks for. Inventing a case id
      here would run the case's playbooks against evidence no case has claimed yet.
    * **Create-time options only.** `tlp`/`pap`/`ioc`/`sighted`/`message` are written when the
      row is *born* and never rewrite an existing row: `Observable` is globally deduplicated
      (Module C), so patching a shared artifact from one alert's payload would mutate evidence
      three other cases are reading. An unchanged value is reported honestly by the response.

    The body follows `InputCreateObservable`: `data` is a string or an array of strings; both
    spellings produce the same array-shaped 201, which is what `OutputObservable[]` promises.
    """
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert

    payload = request.data if isinstance(request.data, dict) else {}
    type_name = str(payload.get("dataType") or "")
    if not ObservableType.objects.filter(name=type_name).exists():
        return _bad(
            f"unknown dataType {payload.get('dataType')!r}",
            {"dataType": ["not in the observable vocabulary"]},
        )

    raw = payload.get("data")
    if isinstance(raw, list):
        items: list[Any] = list(raw)
    elif raw is None:
        items = []
    else:
        items = [raw]
    if not items:
        return _bad("data is required", {"data": ["required"]})
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            return _bad(
                "data must be a string or an array of strings",
                {"data": ["must be a string or an array of strings"]},
            )

    defaults: dict[str, Any] = {}
    if payload.get("message") is not None:
        defaults["message"] = str(payload["message"])
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
            defaults[key] = value
    for key in ("ioc", "sighted"):
        if key in payload:
            defaults[key] = bool(payload[key])

    with transaction.atomic():
        for item in items:
            resolved = resolve_observable(type_name, str(item), defaults=defaults)
            if resolved is None:  # pragma: no cover — vocabulary checked above
                return _bad(
                    f"unknown dataType {type_name!r}",
                    {"dataType": ["not in the observable vocabulary"]},
                )
            observable, _created = resolved
            AlertObservable.objects.get_or_create(
                alert=alert, observable=observable, defaults={"added_by": _actor(request)}
            )

    links = alert.alert_observables.select_related("observable__data_type").order_by("-created_at")
    return Response([observable_json(link) for link in links], status=status.HTTP_201_CREATED)


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def alert_list(request: Request) -> Response:
    """`GET /api/v1/alert` lists; `POST /api/v1/alert` creates (T1, plan §7.2).

    The create half is the AC8.4 carrier: thehive4py `alert.create()` POSTs an `InputCreateAlert`
    here and the MVP loop has so far created alerts by webhook — an API client that can only read
    could not start a loop. Fields follow the recorded 5.8.0 `InputCreateAlert` (`type`, `source`,
    `sourceRef`, `title` required; the rest tolerant, matching how `_create_case` reads its
    payload). An unknown `status` string is auto-created at its own stage and recorded as a
    warning, exactly like the ingest path (ADR-002 §D11).
    """
    if request.method == "POST":
        return _create_alert(request)
    queryset = Alert.objects.select_related("status", "case").order_by("-date")
    case_filter = request.query_params.get("case")
    if case_filter:
        try:
            case = link_case_from_identifier(case_filter)
        except Case.DoesNotExist:
            return Response(
                {"type": "NotFoundError", "message": "Case not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        queryset = queryset.filter(case=case)
    status_filter = request.query_params.get("status")
    if status_filter:
        queryset = queryset.filter(status__value=status_filter)
    return Response([alert_json(a) for a in queryset[:200]])


def _create_alert(request: Request) -> Response:
    """Create an alert directly (no webhook in front of it)."""
    payload = request.data if isinstance(request.data, dict) else {}
    title = str(payload.get("title") or "").strip()
    alert_type = str(payload.get("type") or "").strip()
    source = str(payload.get("source") or "").strip()
    source_ref = str(payload.get("sourceRef") or str(payload.get("source_ref") or "")).strip()
    missing = [
        key
        for key, value in (("title", title), ("type", alert_type), ("source", source))
        if not value
    ]
    if missing or not source_ref:
        if not source_ref:
            missing.append("sourceRef")
        return Response(
            {
                "type": "BadRequest",
                "message": f"Missing required field(s): {', '.join(missing)}",
                "fields": {key: ["required"] for key in missing},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    warnings: list[str] = []
    with transaction.atomic():
        alert = Alert.objects.create(
            type=alert_type[:100],
            source=source[:100],
            source_ref=source_ref[:255],
            external_link=str(payload.get("externalLink") or "")[:500],
            title=title[:500],
            description=str(payload.get("description") or ""),
            summary=str(payload.get("summary") or ""),
            severity=int(payload.get("severity") or 2),
            tlp=int(payload.get("tlp") or 2),
            pap=int(payload.get("pap") or 2),
            flag=bool(payload.get("flag") or False),
            status=resolve_alert_status(str(payload.get("status") or "New"), warnings=warnings),
        )
        # `date` is ms-epoch on the wire (5.8.0 format); keep the ingest path's tolerance.
        date_value = payload.get("date")
        if date_value is not None:
            alert.date = datetime.fromtimestamp(int(date_value) / 1000, tz=UTC)
            alert.save(update_fields=["date"])
    payload_warnings = {
        name: value
        for name, value in payload.items()
        if name
        not in (
            "title",
            "type",
            "source",
            "sourceRef",
            "description",
            "severity",
            "tlp",
            "pap",
            "flag",
            "date",
            "status",
            "summary",
        )
    }
    if warnings or payload_warnings:
        alert.ingestion_warnings = {"warnings": warnings, "unmapped_fields": list(payload_warnings)}
        alert.save(update_fields=["ingestion_warnings"])
    return Response(alert_json(alert), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_merge(request: Request, alert_id: str, case_id: str) -> Response:
    """`POST /api/v1/alert/{alertId}/merge/{caseId}` — escalate into an existing case."""
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert
    try:
        case = link_case_from_identifier(case_id)
    except Case.DoesNotExist:
        return Response(
            {"type": "NotFoundError", "message": "Case not found"}, status=status.HTTP_404_NOT_FOUND
        )
    merge_alert_into_case(alert, case, actor=_actor(request))
    return Response(case_json(case, detail=True))


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_import(request: Request, alert_id: str) -> Response:
    """`POST /api/v1/alert/{alertId}/import` — promote the alert into a new case."""
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert
    if alert.case_id is not None:
        return Response(
            {
                "type": "BadRequest",
                "message": "Alert is already attached to a case; merge instead",
                "fields": {"alertId": [f"case is {alert.case_id}"]},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    payload = request.data if isinstance(request.data, dict) else {}
    assignee = None
    assignee_id = payload.get("assignee") or payload.get("owner")
    if assignee_id:
        from identity.models import User

        assignee = (
            User.objects.filter(login=assignee_id).first()
            or User.objects.filter(pk=assignee_id).first()
        )
    case, _warnings = import_alert_to_case(
        alert,
        title=payload.get("title"),
        description=payload.get("description"),
        assignee=assignee,
        actor=_actor(request),
    )
    case.refresh_from_db()
    return Response(case_json(case), status=status.HTTP_201_CREATED)


@transaction.atomic
def _update_alert(request: Request, alert: Alert) -> Response:
    """Apply the fields the triage loop actually changes. No field, no write."""
    fields: list[str] = []
    payload = request.data if isinstance(request.data, dict) else {}
    for key in ("title", "description", "summary", "severity", "tlp", "pap", "flag", "follow"):
        if key in payload:
            setattr(alert, key, payload[key])
            fields.append(key)
    if "status" in payload:
        from alerts.escalation import resolve_alert_status

        warnings: list[str] = []
        alert.status = resolve_alert_status(str(payload["status"]), warnings=warnings)
        fields.append("status")
    if not fields:
        return Response(
            {"type": "BadRequest", "message": "No updatable field supplied", "fields": {}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    alert.save(update_fields=[*fields, "updated_at"])
    alert.refresh_from_db()
    return Response(alert_json(alert))


# --- T2: alert statuses and tag links ---------------------------------------


def _as_uuid(value: object) -> UUID | None:
    """Parse a path segment as a UUID; a malformed one must be a 404, never a 500."""
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _resolve_alert_status(identifier: str) -> AlertStatus | None:
    """Resolve `{idOrValue}` as a UUID first, then as the unique `value`."""
    pk = _as_uuid(identifier)
    if pk is not None:
        found = AlertStatus.objects.filter(pk=pk).first()
        if found is not None:
            return found
    return AlertStatus.objects.filter(value=identifier).first()


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def alert_status_collection(request: Request) -> Response:
    """`GET /api/v1/alertStatus` lists; `POST /api/v1/alertStatus` creates (201)."""
    if request.method == "POST":
        return _create_alert_status(request)
    return Response(
        [
            alert_status_json(status_obj)
            for status_obj in AlertStatus.objects.order_by("order", "value")
        ]
    )


def _create_alert_status(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    value = str(payload.get("value") or "").strip()
    stage = str(payload.get("stage") or "").strip()
    missing = {key: ["required"] for key, val in (("value", value), ("stage", stage)) if not val}
    if missing:
        return _bad("value and stage are required", missing)
    if stage not in ALERT_STAGES:
        return _bad(
            f"unknown stage {stage!r}",
            {"stage": [f"must be one of {', '.join(ALERT_STAGES)}"]},
        )
    if AlertStatus.objects.filter(value=value[:64]).exists():
        return _bad("value already exists", {"value": ["already exists"]})
    try:
        order = int(payload.get("order", 0))
    except (TypeError, ValueError):
        return _bad("order must be an integer", {"order": ["must be an integer"]})
    status_obj = AlertStatus.objects.create(
        value=value[:64],
        stage=stage,
        order=order,
        description=str(payload.get("description") or ""),
        hidden=bool(payload.get("hidden") or False),
    )
    return Response(alert_status_json(status_obj), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def alert_status_detail(request: Request, status_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/alertStatus/{idOrValue}`.

    Same rules as `cases.views.case_status_detail`: `value`/`stage` are immutable, and DELETE
    refuses a status still used by an alert with a **400** rather than the `PROTECT` 500.
    """
    status_obj = _resolve_alert_status(status_id)
    if status_obj is None:
        return _not_found_alert("AlertStatus")
    if request.method == "DELETE":
        if status_obj.alerts.exists():
            return _bad(
                "Alert status is in use",
                {"_id": ["at least one alert uses this status"]},
            )
        status_obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_alert_status(status_obj, request)
    return Response(alert_status_json(status_obj))


def _not_found_alert(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"},
        status=status.HTTP_404_NOT_FOUND,
    )


def _update_alert_status(status_obj: AlertStatus, request: Request) -> Response:
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
def alert_tag_link(request: Request, alert_id: str) -> Response:
    """`POST|DELETE /api/v1/alert/{alertId}/tag` — attach or detach tags (200 with the set).

    TheHive 5.8 has no such route (tags arrive in the create/update body); recorded in
    `docs/spec/deviations.md` with its case/observable siblings. Idempotent in both directions.
    """
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert
    names = _requested_tags(request)
    if isinstance(names, Response):
        return names
    if request.method == "DELETE":
        AlertTagLink.objects.filter(alert=alert, tag__name__in=names).delete()
    else:
        for name in names:
            tag, _created = Tag.objects.get_or_create(name=name)
            AlertTagLink.objects.get_or_create(alert=alert, tag=tag)
    return Response([tag_json(tag) for tag in alert.tags.order_by("name")])


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def alert_comment_list(request: Request, alert_id: str) -> Response:
    """`GET|POST /api/v1/alert/{alertId}/comment` — list or add alert comments (TheHive 5.8).

    An alert already promoted to a case mirrors a new comment onto that case's WebSocket so an
    open case view updates without a reload; an un-promoted alert has no socket and is fetched on
    navigation. The comment itself always hangs off the alert's stable `_id`.
    """
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert
    if request.method == "GET":
        comments = alert.comments.select_related("created_by", "updated_by").order_by(
            "-created_at", "-id"
        )
        return Response([comment_json(c) for c in comments])
    payload = request.data if isinstance(request.data, dict) else {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return _bad("message is required", {"message": ["required"]})
    comment = Comment.objects.create(alert=alert, message=message, created_by=_actor(request))
    if alert.case_id:
        transaction.on_commit(
            partial(
                publish_case_event,
                str(alert.case_id),
                "comment",
                {"event": comment_json(comment)},
            )
        )
    return Response(comment_json(comment), status=status.HTTP_201_CREATED)


__all__ = [
    "alert_comment_list",
    "alert_detail",
    "alert_import",
    "alert_list",
    "alert_merge",
    "alert_observable_add",
    "alert_raw",
    "alert_status_collection",
    "alert_status_detail",
    "alert_tag_link",
]
