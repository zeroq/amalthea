from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.models import AlertStatus
from cases.models import (
    CaseStatus,
    CaseTemplate,
    Tag,
)
from core.serializers import (
    alert_status_json,
    case_status_json,
    case_template_json,
    observable_type_json,
    tag_json,
)
from core.taxonomy import (
    TaxonomyError,
    create_case_status,
    delete_case_status,
    update_case_status,
)
from identity.audit import record_taxonomy_change
from observables.models import ObservableType

from ._common import _as_uuid, _bad, _not_found, _require_superadmin_for_write


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
    """`GET /api/v1/caseStatus` lists; `POST /api/v1/caseStatus` creates (201).

    POST requires superAdmin; GET is open to all authenticated users.
    """
    guard = _require_superadmin_for_write(request)
    if guard:
        return guard

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
    try:
        order = int(payload.get("order", 0))
    except (TypeError, ValueError):
        return _bad("order must be an integer", {"order": ["must be an integer"]})

    try:
        with transaction.atomic():
            status_obj, result = create_case_status(
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
                action=f"taxonomy.case_status.{result.action}",
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

    return Response(case_status_json(status_obj), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def case_status_detail(request: Request, status_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/caseStatus/{idOrValue}`.

    PATCH accepts `value` (rename), `order`, `description`, `hidden`. `stage` remains immutable.
    DELETE refuses a status still in use with a **400**.
    POST/PATCH/DELETE require superAdmin; GET is open to all authenticated users.
    """
    guard = _require_superadmin_for_write(request)
    if guard:
        return guard

    status_obj = _resolve_case_status(status_id)
    if status_obj is None:
        return _not_found("CaseStatus")

    if request.method == "DELETE":
        try:
            with transaction.atomic():
                result = delete_case_status(status_obj)
                record_taxonomy_change(
                    request,
                    actor=request.user,
                    action=f"taxonomy.case_status.{result.action}",
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
        return _update_case_status(status_obj, request)

    return Response(case_status_json(status_obj))


def _update_case_status(status_obj: CaseStatus, request: Request) -> Response:
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
            result = update_case_status(
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
                action=f"taxonomy.case_status.{result.action}",
                target_type=result.target_type,
                target_id=result.target_id,
                details=details,
            )
    except TaxonomyError as exc:
        fields = {exc.field: [exc.message]} if exc.field else {}
        return _bad(exc.message, fields)

    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def taxonomy(request: Request) -> Response:
    """`GET /api/v1/taxonomy` — the vocabularies a client needs in one read (Amalthea extension).

    TheHive 5.8 has no such route; the plan asks for a read model that aggregates the observable
    types, both status vocabularies and the case templates so a UI can prime its pickers without
    four round-trips. Recorded in `docs/spec/deviations.md`.
    """
    return Response(
        {
            "observableTypes": [
                observable_type_json(o) for o in ObservableType.objects.order_by("name")
            ],
            "caseStatuses": [
                case_status_json(s) for s in CaseStatus.objects.order_by("order", "value")
            ],
            "alertStatuses": [
                alert_status_json(s) for s in AlertStatus.objects.order_by("order", "value")
            ],
            "caseTemplates": [case_template_json(t) for t in CaseTemplate.objects.order_by("name")],
            "tags": [tag_json(t) for t in Tag.objects.order_by("name")],
        }
    )
