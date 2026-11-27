"""Alert merge/import escalation endpoints."""

from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from alerts.escalation import import_alert_to_case, merge_alert_into_case
from core.serializers import case_json

from ._common import _actor, _not_found_alert, _resolve_alert_scoped


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_merge(request: Request, alert_id: str, case_id: str) -> Response:
    """`POST /api/v1/alert/{alertId}/merge/{caseId}` — escalate into an existing case."""
    alert = _resolve_alert_scoped(request, alert_id)
    if alert is None:
        return _not_found_alert("Alert")
    case = identity.scoping.resolve_case(request.user, case_id, write=True)
    if case is None:
        return _not_found_alert("Case")
    merge_alert_into_case(alert, case, actor=_actor(request))
    return Response(case_json(case, detail=True))


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_import(request: Request, alert_id: str) -> Response:
    """`POST /api/v1/alert/{alertId}/import` — promote the alert into a new case."""
    alert = _resolve_alert_scoped(request, alert_id)
    if alert is None:
        return _not_found_alert("Alert")
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
