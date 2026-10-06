"""T1 `alert` endpoints, scoped to the MVP loop (Phase 4/5).

The full T1 surface (query filters, bulk ops, custom fields) is Phase 8/10 work. What the loop
needs is: read an alert, read its raw payload, and escalate it. Those are here, and they speak the
TheHive-ish envelope the rest of the codebase already documents (`_id` for the id, `type`/`message`
for errors) so a client written against the recorded 5.8.0 examples keeps working as the surface
grows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
from alerts.models import Alert
from cases.models import Case
from core.serializers import alert_json, case_json


def _actor(request: Request) -> Any:
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


@api_view(["GET", "PATCH", "PUT"])
@renderer_classes([JSONRenderer])
def alert_detail(request: Request, alert_id: str) -> Response:
    """`GET|PATCH /api/v1/alert/{alertId}` — read or triage one alert.

    The identifier is a UUID or an unambiguous `source_ref`; an ambiguous `source_ref` is a 400
    naming the ambiguity, not a 404 and not a silent pick of the first row.
    """
    alert = _lookup_alert(alert_id)
    if isinstance(alert, Response):
        return alert
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


__all__ = [
    "alert_detail",
    "alert_import",
    "alert_list",
    "alert_merge",
    "alert_raw",
]
