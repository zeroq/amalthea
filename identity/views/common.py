"""Shared helpers for identity API views."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from identity.models import Organisation, User
from identity.services import as_uuid, resolve_organisation, resolve_user


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


def _conflict(message: str, fields: dict[str, object] | None = None) -> Response:
    payload: dict[str, object] = {"type": "ConflictError", "message": message}
    if fields is not None:
        payload["blockers"] = fields
    return Response(payload, status=status.HTTP_409_CONFLICT)


def _as_uuid(value: object) -> UUID | None:
    """Parse a path segment as a UUID without letting a malformed one reach the ORM.

    `filter(pk="not-a-uuid")` raises `ValidationError` out of the query compiler, which the
    exception handler does not map — a 500 where the contract promises a 404.
    """
    return as_uuid(value)


def _resolve_org(identifier: str) -> Organisation | None:
    """Resolve `identifier` (UUID **or** name) to an Organisation, or `None`."""
    return resolve_organisation(identifier)


def _resolve_user(identifier: str) -> User | None:
    """Resolve `identifier` (UUID **or** login) to a User, or `None`."""
    return resolve_user(identifier)


def _require_superadmin(request: Request) -> User | Response:
    """Return the authenticated superAdmin user, or a uniform 404 Response if not superAdmin."""
    user = cast(User, request.user)
    if not getattr(user, "is_authenticated", False) or getattr(user, "role", "") != "superAdmin":
        # A uniform 404 for everyone else — a 403 would confirm the route exists.
        return _not_found("Resource")
    return user


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
