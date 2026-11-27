"""Organisation identity API views."""

from __future__ import annotations

from typing import cast

from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from core.serializers import organisation_admin_json, organisation_json
from identity.audit import record_organisation_change
from identity.models import Organisation, User
from identity.services import OrganisationDeleteBlockedError, delete_organisation

from .common import _bad, _conflict, _not_found, _require_superadmin, _resolve_org


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def organisation_collection(request: Request) -> Response:
    """`GET|POST /api/v1/organisation` — organisations.

    GET:
    - Non-superAdmin: returns the caller's own organisation (0 or 1 item list), unchanged.
    - SuperAdmin: returns ALL organisations annotated with counts (members, cases, alerts, observables).

    POST (superAdmin only):
    - Create an organisation. Body: `{name, description?}`.
    """
    user = cast(User, request.user)

    if request.method == "GET":
        if getattr(user, "role", "") == "superAdmin":
            qs = Organisation.objects.annotate(
                members_count=Count("users", distinct=True),
                cases_count=Count("cases", distinct=True),
                alerts_count=Count("alerts", distinct=True),
                observables_count=Count("observables", distinct=True),
            ).order_by("name")
            return Response(
                [
                    organisation_admin_json(
                        org,
                        {
                            "members": org.members_count,
                            "cases": org.cases_count,
                            "alerts": org.alerts_count,
                            "observables": org.observables_count,
                        },
                    )
                    for org in qs
                ]
            )
        # Non-superAdmin: own org only (legacy behaviour)
        org = getattr(user, "org", None)
        return Response([organisation_json(org)] if org is not None else [])

    # POST - superAdmin only
    guard = _require_superadmin(request)
    if isinstance(guard, Response):
        return guard
    actor = guard

    payload = request.data if isinstance(request.data, dict) else {}
    name = str(payload.get("name") or "").strip()[:200]
    if not name:
        return _bad("name is required", {"name": ["required"]})
    if Organisation.objects.filter(name=name).exists():
        return _bad("name already exists", {"name": ["already exists"]})

    description = str(payload.get("description") or "")
    org = Organisation.objects.create(name=name, description=description)

    record_organisation_change(
        request, actor=actor, action="organisation.created", target_org=org, target_id=str(org.id)
    )
    return Response(organisation_json(org), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def organisation_detail(request: Request, org_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/organisation/{idOrName}` — organisation detail/mutate.

    - SuperAdmin: any organisation (GET returns counts; PATCH updates; DELETE guarded).
    - Non-superAdmin: own organisation only (GET/PATCH as before; DELETE → 404).
    """
    user = cast(User, request.user)
    is_super = getattr(user, "role", "") == "superAdmin"

    org = _resolve_org(org_id)
    if org is None:
        return _not_found("Organisation")

    if not is_super:
        # Non-superAdmin: own org only
        if org.pk != getattr(user, "org_id", None):
            return _not_found("Organisation")
        # Legacy paths for own org
        if request.method == "GET":
            return Response(organisation_json(org))
        if request.method == "PATCH":
            return _update_organisation(org, request)
        # DELETE not allowed for non-superAdmin
        return _not_found("Organisation")

    # SuperAdmin paths
    if request.method == "GET":
        counts = {
            "members": org.users.count(),
            "cases": org.cases.count(),
            "alerts": org.alerts.count(),
            "observables": org.observables.count(),
        }
        return Response(organisation_admin_json(org, counts))

    if request.method == "PATCH":
        return _update_organisation_superadmin(org, request, user)

    # DELETE
    return _delete_organisation(request, org, user)


def _update_organisation(org: Organisation, request: Request) -> Response:
    """Legacy own-org PATCH (non-superAdmin). 204 no body."""
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "name" in payload:
        name = str(payload["name"] or "").strip()[:200]
        if not name:
            return _bad("name is required", {"name": ["required"]})
        if Organisation.objects.exclude(pk=org.pk).filter(name=name).exists():
            return _bad("name already exists", {"name": ["already exists"]})
        org.name = name
        fields.append("name")
    if "description" in payload:
        org.description = str(payload["description"] or "")
        fields.append("description")
    if not fields:
        return _bad("No updatable field supplied", {})
    org.save(update_fields=fields)
    return Response(status=status.HTTP_204_NO_CONTENT)


def _update_organisation_superadmin(org: Organisation, request: Request, actor: User) -> Response:
    """SuperAdmin PATCH on any org. 204 no body. Audits `organisation.updated`."""
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    changed: list[str] = []
    updatable_provided = False
    if "name" in payload:
        updatable_provided = True
        name = str(payload["name"] or "").strip()[:200]
        if not name:
            return _bad("name is required", {"name": ["required"]})
        if Organisation.objects.exclude(pk=org.pk).filter(name=name).exists():
            return _bad("name already exists", {"name": ["already exists"]})
        if org.name != name:
            org.name = name
            changed.append("name")
        fields.append("name")
    if "description" in payload:
        updatable_provided = True
        desc = str(payload["description"] or "")
        if org.description != desc:
            org.description = desc
            changed.append("description")
        fields.append("description")
    if not updatable_provided:
        return _bad("No updatable field supplied", {})
    if fields:
        org.save(update_fields=fields)

    if changed:
        record_organisation_change(
            request,
            actor=actor,
            action="organisation.updated",
            target_org=org,
            target_id=str(org.id),
        )
    return Response(status=status.HTTP_204_NO_CONTENT)


def _delete_organisation(request: Request, org: Organisation, actor: User) -> Response:
    """SuperAdmin DELETE on any org. Guarded deletion with member handling. Audits `organisation.deleted`."""
    # Read params from query string or body (form/JSON)
    members_action = request.query_params.get("members") or (
        request.data.get("members") if isinstance(request.data, dict) else None
    )
    target_org_id = request.query_params.get("target_org") or (
        request.data.get("target_org") if isinstance(request.data, dict) else None
    )

    # Validate params first (400 for invalid input)
    if members_action == "reassign":
        if not target_org_id:
            return _bad("target_org required when members=reassign", {"target_org": ["required"]})
        target_org = _resolve_org(target_org_id)
        if target_org is None or target_org.pk == org.pk:
            return _bad(
                "target_org must be an existing different organisation", {"target_org": ["invalid"]}
            )
    elif members_action == "delete":
        target_org = None
    else:
        target_org = None
        if target_org_id:
            target_org = _resolve_org(target_org_id)

    try:
        delete_organisation(
            org,
            members=members_action,
            target=target_org,
            actor=actor,
            request=request,
        )
    except OrganisationDeleteBlockedError as e:
        if e.reason == "self_lockout":
            return _conflict(
                "Cannot delete your own account by deleting an organisation; reassign members instead",
                {"reason": "self_lockout"},
            )
        if e.reason == "last_superadmin":
            return _conflict(
                "Cannot delete the last active superAdmin by deleting an organisation; "
                "reassign members instead",
                {"reason": "last_superadmin"},
            )
        if e.reason == "race" or e.blockers.get("race"):
            return _conflict("Organisation still owns tenant data (race)", dict(e.blockers))
        if e.members is not None:
            return _conflict(
                "Organisation has members",
                {"members": e.members, "options": ["reassign", "delete"]},
            )
        return _conflict("Organisation still owns tenant data", dict(e.blockers))

    return Response(status=status.HTTP_204_NO_CONTENT)
