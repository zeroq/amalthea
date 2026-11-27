"""Alert status taxonomy endpoints."""

from __future__ import annotations

from uuid import UUID

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.models import AlertStatus
from core.serializers import alert_status_json
from core.taxonomy import (
    TaxonomyError,
    create_alert_status,
    delete_alert_status,
    update_alert_status,
)
from identity.audit import record_taxonomy_change

from ._common import _bad, _not_found_alert, _require_superadmin_for_write


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
    """`GET /api/v1/alertStatus` lists; `POST /api/v1/alertStatus` creates (201).

    POST requires superAdmin; GET is open to all authenticated users.
    """
    guard = _require_superadmin_for_write(request)
    if guard:
        return guard

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
    try:
        order = int(payload.get("order", 0))
    except (TypeError, ValueError):
        return _bad("order must be an integer", {"order": ["must be an integer"]})

    try:
        with transaction.atomic():
            status_obj, result = create_alert_status(
                value=str(payload.get("value") or ""),
                label=str(payload.get("label") or ""),
                stage=str(payload.get("stage") or ""),
                order=order,
                description=str(payload.get("description") or ""),
                hidden=bool(payload.get("hidden") or False),
            )
            record_taxonomy_change(
                request,
                actor=request.user,
                action=f"taxonomy.alert_status.{result.action}",
                target_type=result.target_type,
                target_id=result.target_id,
                details={
                    "changed": result.changed,
                    "old": {"value": result.old_identifier} if result.old_identifier else {},
                    "new": {"value": result.new_identifier} if result.new_identifier else {},
                },
            )
    except TaxonomyError as exc:
        fields = {exc.field: [exc.message]} if exc.field else {}
        return _bad(exc.message, fields)

    return Response(alert_status_json(status_obj), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def alert_status_detail(request: Request, status_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/alertStatus/{idOrValue}`.

    PATCH accepts `value` (rename), `order`, `description`, `hidden`. `stage` remains immutable.
    DELETE refuses a status still in use with a **400**.
    POST/PATCH/DELETE require superAdmin; GET is open to all authenticated users.
    """
    guard = _require_superadmin_for_write(request)
    if guard:
        return guard

    status_obj = _resolve_alert_status(status_id)
    if status_obj is None:
        return _not_found_alert("AlertStatus")

    if request.method == "DELETE":
        try:
            with transaction.atomic():
                result = delete_alert_status(status_obj)
                record_taxonomy_change(
                    request,
                    actor=request.user,
                    action=f"taxonomy.alert_status.{result.action}",
                    target_type=result.target_type,
                    target_id=result.target_id,
                    details={
                        "changed": result.changed,
                        "old": {"value": result.old_identifier} if result.old_identifier else {},
                        "new": {},
                    },
                )
        except TaxonomyError as exc:
            fields = {exc.field: [exc.message]} if exc.field else {}
            return _bad(exc.message, fields)
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "PATCH":
        return _update_alert_status(status_obj, request)

    return Response(alert_status_json(status_obj))


def _update_alert_status(status_obj: AlertStatus, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}

    # Extract only the fields we allow to be updated
    value = payload.get("value")
    label = payload.get("label")
    order = payload.get("order")
    description = payload.get("description")
    hidden = payload.get("hidden")

    # Convert order to int if present
    if order is not None:
        try:
            order = int(order)
        except (TypeError, ValueError):
            return _bad("order must be an integer", {"order": ["must be an integer"]})

    try:
        with transaction.atomic():
            result = update_alert_status(
                status_obj,
                value=str(value) if value is not None else None,
                label=str(label) if label is not None else None,
                order=order,
                description=str(description) if description is not None else None,
                hidden=bool(hidden) if hidden is not None else None,
            )
            details: dict[str, object] = {"changed": result.changed}
            if result.old_identifier is not None:
                details["old"] = {"value": result.old_identifier}
            if result.new_identifier is not None:
                details["new"] = {"value": result.new_identifier}
            record_taxonomy_change(
                request,
                actor=request.user,
                action=f"taxonomy.alert_status.{result.action}",
                target_type=result.target_type,
                target_id=result.target_id,
                details=details,
            )
    except TaxonomyError as exc:
        fields = {exc.field: [exc.message]} if exc.field else {}
        return _bad(exc.message, fields)

    return Response(status=status.HTTP_204_NO_CONTENT)
