"""Procedure endpoints for alerts (T2 P5)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from cases.models import TTP
from cases.procedures import create_procedure
from compat.procedures import parse_procedure
from core.serializers import procedure_json

from ._common import _bad, _not_found_alert, _resolve_alert_scoped


def _resolve_alert_ttp(value: str) -> TTP | None:
    try:
        UUID(value)
    except (ValueError, TypeError):
        return TTP.objects.filter(name=value).first()
    return TTP.objects.filter(pk=value).first() or TTP.objects.filter(name=value).first()


def _parse_alert_procedure(payload: Any) -> Response | dict[str, Any]:
    kwargs, error = parse_procedure(payload, resolve_ttp=_resolve_alert_ttp)
    if error is not None:
        field, message = error
        return _bad(message, {field: [message]})
    assert kwargs is not None
    return kwargs


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_procedure_create(request: Request, alert_id: str) -> Response:
    """`POST /api/v1/alert/{id}/procedure` — attach one procedure to an alert (201)."""
    alert = _resolve_alert_scoped(request, alert_id)
    if alert is None:
        return _not_found_alert("Alert")
    kwargs = _parse_alert_procedure(request.data)
    if isinstance(kwargs, Response):
        return kwargs
    return Response(
        procedure_json(create_procedure(alert=alert, **kwargs)), status=status.HTTP_201_CREATED
    )


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def alert_procedures_create(request: Request, alert_id: str) -> Response:
    """`POST /api/v1/alert/{id}/procedures` — bulk attach `{"procedures": [...]}` (201)."""
    alert = _resolve_alert_scoped(request, alert_id)
    if alert is None:
        return _not_found_alert("Alert")
    payload = request.data if isinstance(request.data, dict) else {}
    specs = payload.get("procedures")
    if not isinstance(specs, list) or not specs:
        return _bad("procedures is required", {"procedures": ["required"]})
    created = []
    with transaction.atomic():
        for spec in specs:
            kwargs = _parse_alert_procedure(spec)
            if isinstance(kwargs, Response):
                return kwargs
            created.append(procedure_json(create_procedure(alert=alert, **kwargs)))
    return Response(created, status=status.HTTP_201_CREATED)
