from __future__ import annotations

from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from cases.models import (
    Share,
)
from core.serializers import (
    share_json,
)
from identity.audit import maybe_record_cross_org_read
from identity.models import Organisation

from ._common import _actor, _as_uuid, _bad, _case_access, _not_found


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


@api_view(["GET", "POST", "PUT"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def case_share_list(request: Request, case_id: str) -> Response:
    """`GET|POST|PUT /api/v1/case/{idOrNumber}/shares` — read, add or replace shares.

    **Default deny** (plan §6-P2): reading the share list needs read access; mutating it
    (POST/PUT) requires the caller to be in the case's **owner organisation** (or superAdmin).
    A share holder with write access may mutate the case body, but only the owner org may
    manage the share ACL itself. `POST` adds (TheHive's `share`); `PUT` replaces the whole set
    (TheHive's `set_share`). A share only ever adds access for one tenant and cannot touch
    the owner's.
    """
    case = _case_access(request, case_id, write=request.method in ("POST", "PUT"))
    if case is None:
        return _not_found("Case")
    if request.method == "GET":
        # Audit superAdmin cross-org read
        maybe_record_cross_org_read(
            request,
            actor=request.user,
            target_obj=case,
            target_type="Case",
            target_id=str(case.id),
            target_org_field="owner_org",
        )

        shares = case.shares.select_related("organisation").order_by("created_at")
        return Response([share_json(share) for share in shares])

    # POST/PUT: require owner organisation (or superAdmin) — not merely a write share
    if not identity.scoping.is_case_owner(request.user, case):
        return _not_found("Case")

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
    """`DELETE /api/v1/case/{idOrNumber}/share/{shareId}` — revoke one share (TheHive 5.8).

    Requires the caller to be in the case's **owner organisation** (or superAdmin).
    A share holder with write access may mutate the case body, but only the owner org
    may revoke shares.
    """
    case = _case_access(request, case_id, write=True)
    if case is None:
        return _not_found("Case")

    # DELETE: require owner organisation (or superAdmin) — not merely a write share
    if not identity.scoping.is_case_owner(request.user, case):
        return _not_found("Case")

    share_pk = _as_uuid(share_id)
    share = Share.objects.filter(pk=share_pk, case=case).first() if share_pk is not None else None
    if share is None:
        return _not_found("Share")
    share.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
