from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db.models import Q
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from cases.models import (
    Case,
    Task,
)
from core.enums import (
    TASK_STATUS_CHOICES,
)
from identity.models import Organisation
from observables.models import Observable

_TASK_STATUSES = tuple(choice for choice, _label in TASK_STATUS_CHOICES)


def _actor(request: Request) -> Any:
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def _not_found(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"}, status=status.HTTP_404_NOT_FOUND
    )


def _bad(message: str, fields: dict[str, list[str]]) -> Response:
    return Response(
        {"type": "BadRequest", "message": message, "fields": fields},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _require_superadmin_for_write(request: Request) -> Response | None:
    """Return a 404 response for unsafe methods if the user is not a superAdmin.

    GET remains open to all authenticated users. For POST, PATCH, PUT, DELETE,
    non-superAdmin users receive a uniform 404 (NotFoundError) to avoid
    object-existence leakage. The guard runs before resolving the target row.
    """
    if request.method in ("POST", "PATCH", "PUT", "DELETE"):
        user = getattr(request, "user", None)
        if not (
            getattr(user, "is_authenticated", False) and getattr(user, "role", "") == "superAdmin"
        ):
            return _not_found("Resource")
    return None


def _as_uuid(value: Any) -> UUID | None:
    """Parse a path segment as a UUID without letting a malformed one reach the ORM.

    `filter(pk="not-a-uuid")` raises `ValidationError` out of the query compiler, which the
    exception handler does not map — a 500 where the contract promises a 404. Parsing here
    keeps "not found" and "malformed" the same honest answer at the path boundary.

    A `UUID` instance is returned as-is: the API routes take `<str:attachment_id>`, but the UI
    routes that delegate here use `<uuid:...>` converters, so this helper is reached with an
    already-parsed value too — and `UUID(some_uuid)` raises `AttributeError`, not ValueError,
    which used to escape as a 500 on every UI attachment download.
    """
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Coerce a JSON scalar to an integer, or `None` when it is not integer-shaped.

    `None` (absent) becomes `0`, matching the `order` default; booleans are rejected so that JSON
    `true` cannot silently become `1`.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _case_org_scope(request: Request) -> Q:
    """Org guard for rows reached *through* a case.

    Returns a `Q` filtering rows by the caller's organisation via the case's
    `owner_org`. SuperAdmin gets an unfiltered `Q()`; org-less callers get
    a deny-by-default `Q(pk__in=[])`.
    """
    return identity.scoping.org_q("case__owner_org", request.user)


def _case_access(request: Request, identifier: str, *, write: bool) -> Case | None:
    """Resolve a case the caller may reach, or `None` (which callers turn into a 404).

    Thin wrapper over `identity.scoping.resolve_case` — kept so existing call sites
    don't churn. This is the **share** guard (plan §6-P2, deviation **P10-w**).
    A case that belongs to no organisation stays visible to every authenticated
    caller — the same choice `_case_org_scope` makes. A case that *is* org-owned
    is visible only inside that organisation, or to an organisation holding a
    `Share`: `read` is enough to look, `write=True` also demands
    `permissions.write`. A share therefore only ever *adds* access for one
    tenant; it cannot narrow the owner's or leak across cases.
    """
    return identity.scoping.resolve_case(request.user, identifier, write=write)


def _resolve_task(request: Request, task_id: str, *, write: bool = False) -> Task | None:
    """Resolve a task the caller may reach, or `None` (callers turn into 404).

    Tasks are scoped through their owning case. Uses `identity.scoping.resolve_case`
    with the `write` flag to properly handle share permissions.
    """
    pk = _as_uuid(task_id)
    if pk is None:
        return None
    # First resolve the case with appropriate write access
    task = Task.objects.select_related("case", "assignee").filter(pk=pk).first()
    if task is None:
        return None
    # Check case access with write flag
    case = identity.scoping.resolve_case(request.user, str(task.case_id), write=write)
    return task if case is not None else None


def _resolve_observable_scoped(request: Request, observable_id: str) -> Observable | None:
    """Resolve an observable the caller may reach (direct org ownership)."""
    pk = _as_uuid(observable_id)
    if pk is None:
        return None
    return identity.scoping.resolve_observable(request.user, str(pk))


def _resolve_login(value: Any, *, org: Organisation | None = None) -> Any:
    """Login string → user (parity with `_update_case`); empty/`null` unassigns.

    An unknown login unassigns rather than raising: ADR D11 refuses to auto-create a user
    per unrecognised wire value, so the only safe readings of "not a user" are "nobody". The
    UUID fallback is parsed first because `filter(pk="not-a-uuid")` raises `ValidationError`
    out of the query compiler — an unassigned task must not become a 500.

    Tenant isolation (SEC-2026-10-11 M1): when `org` is given, both lookups are scoped to it,
    so a crafted payload cannot bind a task/case to a principal in another organisation (nor
    use the response as a cross-tenant login-existence oracle). Callers pass the owning
    resource's org; a cross-org value simply resolves to "nobody" (unassign).
    """
    if not value:
        return None
    from identity.models import User

    users = User.objects.all()
    if org is not None:
        users = users.filter(org=org)
    candidate = users.filter(login=str(value)).first()
    if candidate is not None:
        return candidate
    pk = _as_uuid(value)
    return users.filter(pk=pk).first() if pk is not None else None
