from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from alerts.models import Alert
from cases.models import (
    TTP,
    Case,
    Procedure,
)
from cases.procedures import create_procedure
from compat.procedures import parse_procedure
from compat.time import parse_timestamp
from core.serializers import (
    procedure_json,
    ttp_json,
)
from identity.audit import maybe_record_cross_org_read

from ._common import _as_uuid, _bad, _case_access, _not_found


def _resolve_ttp_value(value: str) -> TTP | None:
    """Resolve a `ttpId` as a UUID first, then as the unique name."""
    pk = _as_uuid(value)
    if pk is not None:
        found = TTP.objects.filter(pk=pk).first()
        if found is not None:
            return found
    return TTP.objects.filter(name=value).first()


def _parse_procedure_payload(
    payload: Any, *, case: Case | None = None, alert: Alert | None = None
) -> Response | dict[str, Any]:
    """Translate TheHive's `InputProcedure` into `create_procedure` kwargs, or a 400."""
    kwargs, error = parse_procedure(payload, resolve_ttp=_resolve_ttp_value)
    if error is not None:
        field, message = error
        return _bad(message, {field: [message]})
    assert kwargs is not None  # parse_procedure returns kwargs exactly when error is None
    return {**kwargs, "case": case, "alert": alert}


@transaction.atomic
@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_procedure_create(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{id}/procedure` — attach one procedure (201)."""
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")
    kwargs = _parse_procedure_payload(request.data, case=case)
    if isinstance(kwargs, Response):
        return kwargs
    return Response(procedure_json(create_procedure(**kwargs)), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_procedures_create(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{id}/procedures` — bulk attach `{"procedures": [...]}` (201)."""
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")
    return _create_procedures(request, case=case)


def _create_procedures(
    request: Request, *, case: Case | None = None, alert: Alert | None = None
) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    specs = payload.get("procedures")
    if not isinstance(specs, list) or not specs:
        return _bad("procedures is required", {"procedures": ["required"]})
    created = []
    with transaction.atomic():
        for spec in specs:
            kwargs = _parse_procedure_payload(spec, case=case, alert=alert)
            if isinstance(kwargs, Response):
                return kwargs
            created.append(procedure_json(create_procedure(**kwargs)))
    return Response(created, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def procedure_detail(request: Request, procedure_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/procedure/{id}`."""
    write = request.method in ("PATCH", "DELETE")
    procedure = _resolve_procedure(request, procedure_id, write=write)
    if procedure is None:
        return _not_found("Procedure")
    if request.method == "DELETE":
        procedure.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_procedure(procedure, request)

    # Audit superAdmin cross-org read
    target_obj = procedure.case if procedure.case_id else procedure.alert
    target_type = "Procedure"
    target_org_field = "owner_org"
    if target_obj is not None:
        maybe_record_cross_org_read(
            request,
            actor=request.user,
            target_obj=target_obj,
            target_type=target_type,
            target_id=str(procedure.id),
            target_org_field=target_org_field,
        )

    return Response(procedure_json(procedure))


def _resolve_procedure(
    request: Request, procedure_id: str, *, write: bool = False
) -> Procedure | None:
    """Resolve a procedure the caller may reach (via case or alert org scoping)."""
    pk = _as_uuid(procedure_id)
    if pk is None:
        return None
    procedure = Procedure.objects.select_related("case", "alert").filter(pk=pk).first()
    if procedure is None:
        return None
    # Check access via parent case or alert
    if procedure.case_id:
        case = identity.scoping.resolve_case(request.user, str(procedure.case_id), write=write)
        return procedure if case is not None else None
    if procedure.alert_id:
        alert = identity.scoping.resolve_alert(request.user, str(procedure.alert_id))
        return procedure if alert is not None else None
    return None


def _update_procedure(procedure: Procedure, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "occurDate" in payload:
        if payload["occurDate"] is None:
            procedure.occur_date = None
        else:
            moment = parse_timestamp(payload["occurDate"])
            if moment is None:
                return _bad(
                    "occurDate is not a valid timestamp", {"occurDate": ["invalid timestamp"]}
                )
            procedure.occur_date = moment
        fields.append("occur_date")
    for key, attr, limit in (
        ("patternId", "pattern_id", 255),
        ("patternName", "pattern_name", 255),
        ("tactic", "tactic", 100),
    ):
        if key in payload:
            setattr(procedure, attr, str(payload[key] or "")[:limit])
            fields.append(attr)
    if "description" in payload:
        procedure.description = str(payload["description"] or "")
        fields.append("description")
    if not fields:
        return _bad("No updatable field supplied", {})
    procedure.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def procedure_bulk_delete(request: Request) -> Response:
    """`POST /api/v1/procedure/delete/_bulk` — delete `{"ids": [...]}` in one call."""
    payload = request.data if isinstance(request.data, dict) else {}
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        return _bad("ids is required", {"ids": ["required"]})
    # Resolve each procedure with write access to enforce tenant isolation
    procedures = []
    for raw in ids:
        proc = _resolve_procedure(request, str(raw), write=True)
        if proc is not None:
            procedures.append(proc)
    if procedures:
        Procedure.objects.filter(pk__in=[p.pk for p in procedures]).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def ttp_collection(request: Request) -> Response:
    """`GET|POST /api/v1/ttp` — the editable TTP vocabulary (Amalthea extension)."""
    if request.method == "POST":
        payload = request.data if isinstance(request.data, dict) else {}
        name = str(payload.get("name") or "").strip()
        if not name:
            return _bad("name is required", {"name": ["required"]})
        if TTP.objects.filter(name=name[:255]).exists():
            return _bad("name already exists", {"name": ["already exists"]})
        ttp = TTP.objects.create(
            name=name[:255],
            ttp_code=str(payload.get("ttpCode") or "")[:64],
            tactic=str(payload.get("tactic") or "")[:100],
            description=str(payload.get("description") or ""),
        )
        return Response(ttp_json(ttp), status=status.HTTP_201_CREATED)
    return Response([ttp_json(ttp) for ttp in TTP.objects.order_by("name")])


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def ttp_detail(request: Request, ttp_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/ttp/{idOrName}`; DELETE refuses an in-use technique (AC6.1-P5-b)."""
    pk = _as_uuid(ttp_id)
    ttp = (TTP.objects.filter(pk=pk).first() if pk is not None else None) or TTP.objects.filter(
        name=ttp_id
    ).first()
    if ttp is None:
        return _not_found("TTP")
    if request.method == "DELETE":
        if ttp.procedures.exists():
            return _bad("TTP is in use", {"_id": ["at least one procedure uses this technique"]})
        ttp.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_ttp(ttp, request)
    return Response(ttp_json(ttp))


def _update_ttp(ttp: TTP, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            return _bad("name is required", {"name": ["required"]})
        if TTP.objects.exclude(pk=ttp.pk).filter(name=name[:255]).exists():
            return _bad("name already exists", {"name": ["already exists"]})
        ttp.name = name[:255]
        fields.append("name")
    for key, attr, limit in (
        ("ttpCode", "ttp_code", 64),
        ("tactic", "tactic", 100),
    ):
        if key in payload:
            setattr(ttp, attr, str(payload[key] or "")[:limit])
            fields.append(attr)
    if "description" in payload:
        ttp.description = str(payload["description"] or "")
        fields.append("description")
    if not fields:
        return _bad("No updatable field supplied", {})
    ttp.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)
