"""T2 identity endpoints: the caller's user, and the caller's organisation.

Both surfaces are *about the authenticated caller*. `user/current` exists because TheHive's own
clients ask for it (`Get current User info`) and it is the cheapest way for a client to learn the
`login` it must send as an `assignee`; the organisation routes are scoped to the caller's own
tenant for the reason documented on `_own_org` — a directory of every tenant is a cross-tenant
read the moment a second one exists.

`organisation` gets a REST *collection* GET even though it holds at most one row: the recorded
5.8.0 path carries only `POST /organisation` (create), so this read is an Amalthea extension and
is recorded in `docs/spec/deviations.md`.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from core.serializers import organisation_json, user_json
from identity.models import Organisation, User


def _not_found(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"},
        status=status.HTTP_404_NOT_FOUND,
    )


def _bad(message: str, fields: dict[str, list[str]]) -> Response:
    return Response(
        {"type": "BadRequest", "message": message, "fields": fields},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _as_uuid(value: object) -> UUID | None:
    """Parse a path segment as a UUID without letting a malformed one reach the ORM.

    `filter(pk="not-a-uuid")` raises `ValidationError` out of the query compiler, which the
    exception handler does not map — a 500 where the contract promises a 404.
    """
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def user_current(request: Request) -> Response:
    """`GET /api/v1/user/current` — the authenticated caller as `OutputUser`."""
    return Response(user_json(cast(User, request.user)))


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def user_detail(request: Request, user_id: str) -> Response:
    """`GET /api/v1/user/{idOrLogin}` — one user.

    TheHive's `{userId}` is a row id; ours also accepts the `login` that ADR-002 §D11 made the
    assignee key, because that is the identifier a client already holds from an alert, a task or
    its own session. A user belonging to no organisation is still readable — `user_json` renders
    `org: null` for it.
    """
    pk = _as_uuid(user_id)
    if pk is not None:
        user = User.objects.select_related("org").filter(pk=pk).first()
    else:
        user = User.objects.select_related("org").filter(login=user_id).first()
    if user is None:
        return _not_found("User")
    return Response(user_json(user))


def _own_org(request: Request, identifier: str) -> Organisation | None:
    """Resolve `identifier` (UUID **or** name) to the caller's own organisation, else `None`.

    Scoped deliberately. An organisation listing is a directory of tenants, so returning another
    tenant's row from a guessed id would be a cross-tenant read. "Not yours" and "does not exist"
    are the *same* 404 here — an endpoint that distinguished them could be used to probe which
    organisations exist.
    """
    org_id = getattr(getattr(request, "user", None), "org_id", None)
    pk = _as_uuid(identifier)
    if pk is not None:
        org = Organisation.objects.filter(pk=pk).first()
    else:
        org = Organisation.objects.filter(name=identifier).first()
    if org is None or org.pk != org_id:
        return None
    return org


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def organisation_collection(request: Request) -> Response:
    """`GET /api/v1/organisation` — the caller's own organisation (extension, see module docstring).

    A list rather than a bare object because that is the shape every other collection on this
    surface uses, and because a caller with no `org` gets an honest `[]` instead of a body the
    schema says must carry a `name`.
    """
    org = getattr(cast(User, request.user), "org", None)
    return Response([organisation_json(org)] if org is not None else [])


@api_view(["GET", "PATCH"])
@renderer_classes([JSONRenderer])
def organisation_detail(request: Request, org_id: str) -> Response:
    """`GET|PATCH /api/v1/organisation/{idOrName}` — the caller's own organisation.

    PATCH is **204, no body** (the recorded `Update Organisation` response). Only `name` and
    `description` are written; an empty body is a 400 rather than a silent no-op (ADR D11), and a
    rename onto an tenant that already holds the name is a 400 rather than the IntegrityError a
    500 that would otherwise be.
    """
    org = _own_org(request, org_id)
    if org is None:
        return _not_found("Organisation")
    if request.method == "PATCH":
        return _update_organisation(org, request)
    return Response(organisation_json(org))


def _update_organisation(org: Organisation, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            return _bad("name is required", {"name": ["required"]})
        if Organisation.objects.exclude(pk=org.pk).filter(name=name).exists():
            return _bad("name already exists", {"name": ["already exists"]})
        org.name = name[:200]
        fields.append("name")
    if "description" in payload:
        org.description = str(payload["description"] or "")
        fields.append("description")
    if not fields:
        return _bad("No updatable field supplied", {})
    org.save(update_fields=fields)
    return Response(status=status.HTTP_204_NO_CONTENT)


__all__ = [
    "organisation_collection",
    "organisation_detail",
    "user_current",
    "user_detail",
]
