"""Alert CRUD/list endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from alerts.escalation import resolve_alert_status
from alerts.hashing import compute_payload_ssdeep
from alerts.models import Alert
from core.enums import SUPER_ADMIN_ROLE
from core.serializers import alert_json
from identity.audit import maybe_record_cross_org_read
from realtime.publisher import publish_org_event

from ._common import _actor


@api_view(["GET", "PATCH", "PUT", "DELETE"])
@renderer_classes([JSONRenderer])
def alert_detail(request: Request, alert_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/alert/{alertId}` — read, triage or delete one alert.

    The identifier is a UUID or an unambiguous `source_ref`; an ambiguous `source_ref` is a 400
    naming the ambiguity, not a 404 and not a silent pick of the first row.

    DELETE is the recorded 5.8.0 "permanently delete": the row goes, its `AlertObservable`
    children go with it (CASCADE), and there is no recycle bin to appeal to (plan §13 non-goals).
    """
    alert = identity.scoping.resolve_alert(request.user, alert_id)
    if alert is None:
        return Response(
            {"type": "NotFoundError", "message": "Alert not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if request.method == "DELETE":
        # DELETE is administrative: admin (own org only) or superAdmin (any org). Alerts have no
        # shares, so org ownership is the only cross-org path besides superAdmin.
        if not identity.scoping.is_admin(request.user):
            return Response(
                {"type": "NotFoundError", "message": "Alert not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        alert = identity.scoping.resolve_alert(request.user, alert_id, write=True)
        if alert is None:
            return Response(
                {"type": "NotFoundError", "message": "Alert not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if request.user.role != SUPER_ADMIN_ROLE and alert.owner_org_id != request.user.org_id:
            return Response(
                {"type": "NotFoundError", "message": "Alert not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        alert.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method in ("PATCH", "PUT"):
        return _update_alert(request, alert)

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=alert,
        target_type="Alert",
        target_id=str(alert.id),
        target_org_field="owner_org",
    )

    return Response(alert_json(alert))


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def alert_raw(request: Request, alert_id: str) -> Response:
    """`GET /api/v1/alert/{alertId}/raw` — the byte-preserved ingestion payload (D9).

    D9 promised this returns what the sender sent, not our normalized view of it. That is why it is
    a separate endpoint instead of a flag on `alert_detail`: re-mapped fields would defeat it.
    """
    alert = identity.scoping.resolve_alert(request.user, alert_id)
    if alert is None:
        return Response(
            {"type": "NotFoundError", "message": "Alert not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=alert,
        target_type="Alert",
        target_id=str(alert.id),
        target_org_field="owner_org",
    )

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
    queryset = queryset.filter(identity.scoping.org_q("owner_org", request.user))
    case_filter = request.query_params.get("case")
    if case_filter:
        case = identity.scoping.resolve_case(request.user, case_filter, write=False)
        if case is None:
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

    user = _actor(request)
    org = getattr(user, "org", None) if user else None
    if org is None:
        return Response(
            {
                "type": "AuthorizationError",
                "message": "User must belong to an organisation to create alerts",
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    warnings: list[str] = []
    # Compute SSDEEP hash for fuzzy similarity
    raw_payload = payload if isinstance(payload, dict) else {}
    payload_ssdeep = compute_payload_ssdeep(str(payload.get("summary") or ""), raw_payload)
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
            owner_org=org,
            payload_ssdeep=payload_ssdeep,
        )
        # `date` is ms-epoch on the wire (5.8.0 format); keep the ingest path's tolerance.
        date_value = payload.get("date")
        if date_value is not None:
            alert.date = datetime.fromtimestamp(int(date_value) / 1000, tz=UTC)
            alert.save(update_fields=["date"])
        # Emit org refresh event after commit so a rolled-back create leaves no live signal.
        transaction.on_commit(
            lambda: publish_org_event(
                str(alert.owner_org_id),
                "refresh",
                {
                    "surface": "alerts",
                    "reason": "alert-created",
                    "object_id": str(alert.id),
                    "occurred_at": timezone.now().isoformat(),
                },
            )
        )
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


def _set_alert_fields(alert: Alert, payload: Mapping[str, Any]) -> list[str]:
    """Apply the triage fields `alert_detail` PATCHes to `alert`; shared with `alert/_bulk`.

    There is no `startedAt`/`endedAt`/`tags` here: `Alert` has no such columns, and its tags move
    through the dedicated `alert/<id>/tag` link endpoint (`_set_alert_fields` mirrors exactly what
    `_update_alert` always accepted, so bulk and single PATCH cannot drift apart).
    """
    fields: list[str] = []
    for key in ("title", "description", "summary", "severity", "tlp", "pap", "flag", "follow"):
        if key in payload:
            setattr(alert, key, payload[key])
            fields.append(key)
    if "status" in payload:
        from alerts.escalation import resolve_alert_status

        warnings: list[str] = []
        alert.status = resolve_alert_status(str(payload["status"]), warnings=warnings)
        fields.append("status")
    return fields


@transaction.atomic
def _update_alert(request: Request, alert: Alert) -> Response:
    """Apply the fields the triage loop actually changes. No field, no write."""
    payload = request.data if isinstance(request.data, dict) else {}
    fields = _set_alert_fields(alert, payload)
    if not fields:
        return Response(
            {"type": "BadRequest", "message": "No updatable field supplied", "fields": {}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    alert.save(update_fields=[*fields, "updated_at"])
    alert.refresh_from_db()
    return Response(alert_json(alert))
