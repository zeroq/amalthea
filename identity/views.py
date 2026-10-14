"""T2 identity endpoints: the caller's user, and the caller's organisation.

Both surfaces are *about the authenticated caller*. `user/current` exists because TheHive's own
clients ask for it (`Get current User info`) and it is the cheapest way for a client to learn the
`login` it must send as an `assignee`; the organisation routes are scoped to the caller's own
tenant for the reason documented on `_own_org` — a directory of every tenant is a cross-tenant
read the moment a second one exists.

`organisation` gets a REST *collection* GET even though it holds at most one row: the recorded
5.8.0 path carries only `POST /organisation` (create), so this read is an Amalthea extension and
is recorded in `docs/spec/deviations.md`.

SuperAdmin management endpoints (O1-O3) extend this surface with:
- `GET /api/v1/organisation` returning ALL organisations with counts (superAdmin only)
- `POST /api/v1/organisation` create (superAdmin only)
- `PATCH|DELETE /api/v1/organisation/{idOrName}` on any org (superAdmin only)
- `GET /api/v1/user` list all users (superAdmin only)
- `POST /api/v1/user` create (superAdmin only)
- `PATCH|DELETE /api/v1/user/{idOrLogin}` on any user (superAdmin only)

Non-superAdmin callers receive a uniform 404 on every management route — no enumeration.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from core.enums import USER_ROLE_VALUES
from core.serializers import organisation_admin_json, organisation_json, user_admin_json, user_json
from identity.audit import record_organisation_change, record_user_change
from identity.models import DEFAULT_USER_ROLE, Organisation, User
from identity.services import (
    OrganisationDeleteBlockedError,
    as_uuid,
    delete_organisation,
    parse_bool,
    resolve_organisation,
    resolve_user,
    serializes_superadmin_changes,
)


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


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def user_current(request: Request) -> Response:
    """`GET /api/v1/user/current` — the authenticated caller as `OutputUser`."""
    return Response(user_json(cast(User, request.user)))


# ======================================================================
# Organisation management (superAdmin only)
# ======================================================================


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


# ======================================================================
# User management (superAdmin only)
# ======================================================================


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def user_collection(request: Request) -> Response:
    """`GET|POST /api/v1/user` — users.

    GET (superAdmin only): list all users.
    POST (superAdmin only): create a user. Body: `{username, email, role?, org, password, is_active?}`.
    """
    guard = _require_superadmin(request)
    if isinstance(guard, Response):
        return guard
    actor = guard

    if request.method == "GET":
        # Optional org filter (not required by plan, but harmless)
        org_filter = request.query_params.get("org")
        qs = User.objects.select_related("org").all()
        if org_filter:
            org = _resolve_org(org_filter)
            if org:
                qs = qs.filter(org=org)
        return Response([user_admin_json(u) for u in qs.order_by("username")])

    # POST
    payload = request.data if isinstance(request.data, dict) else {}

    # Required fields
    username = str(payload.get("username") or "").strip()
    email = str(payload.get("email") or "").strip()
    password = payload.get("password")
    org_identifier = payload.get("org")

    errors: dict[str, list[str]] = {}
    if not username:
        errors.setdefault("username", []).append("required")
    if not email:
        errors.setdefault("email", []).append("required")
    if password is None or password == "":
        errors.setdefault("password", []).append("required")
    if not org_identifier:
        errors.setdefault("org", []).append("required")

    # Role validation
    role = str(payload.get("role") or DEFAULT_USER_ROLE)
    if role not in USER_ROLE_VALUES:
        errors.setdefault("role", []).append(f"must be one of {', '.join(USER_ROLE_VALUES)}")

    # Org resolution
    org = None
    if org_identifier:
        org = _resolve_org(str(org_identifier))
        if org is None:
            errors.setdefault("org", []).append("organisation not found")

    if errors:
        return _bad("Validation failed", errors)

    # Duplicate username/login check
    if User.objects.filter(username=username).exists():
        return _bad("username already exists", {"username": ["already exists"]})
    if User.objects.filter(login=username).exists():
        return _bad(
            "login already exists (derived from username)", {"username": ["already exists"]}
        )

    # Password validation
    assert password is not None and password != ""
    is_active = parse_bool(payload.get("is_active", True))
    if is_active is None:
        return _bad("is_active must be a boolean", {"is_active": ["must be true or false"]})
    user = User(username=username, email=email, role=role, org=org, is_active=is_active)
    try:
        validate_password(password, user=user)
    except ValidationError as e:
        return _bad("Password validation failed", {"password": e.messages})

    user.set_password(password)
    user.save()  # This populates `login` from `username`

    record_user_change(
        request, actor=actor, action="user.created", target_id=str(user.id), target_org=org
    )
    return Response(user_admin_json(user), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def user_detail_management(request: Request, user_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/user/{idOrLogin}` — user detail/mutate.

    GET: any authenticated user (for assignee resolution), returns the documented
    `OutputUser` contract (`user_json`) — unchanged. This is why the route is *not*
    swapped for the superAdmin `user_admin_json`: TheHive clients read `hasKey`/
    `hasPassword` here. PATCH|DELETE: superAdmin only.

    For the mutating verbs the superAdmin guard runs **before** the target is resolved,
    so a missing id/login and a forbidden one return the identical uniform 404 body —
    no existence oracle (F6).
    """
    if request.method == "GET":
        # Documented `OutputUser` contract for this path (assignee resolution reads it).
        user = _resolve_user(user_id)
        if user is None:
            return _not_found("User")
        return Response(user_json(user))

    # PATCH/DELETE require superAdmin
    guard = _require_superadmin(request)
    if isinstance(guard, Response):
        return guard
    actor = guard

    user = _resolve_user(user_id)
    if user is None:
        return _not_found("Resource")

    if request.method == "PATCH":
        return _update_user(request, user, actor)

    # DELETE
    return _delete_user(request, user, actor)


@serializes_superadmin_changes
def _update_user(request: Request, user: User, actor: User) -> Response:
    """SuperAdmin PATCH on any user. Audits `user.updated`."""
    payload = request.data if isinstance(request.data, dict) else {}
    if not payload:
        return _bad("No updatable field supplied", {})

    errors: dict[str, list[str]] = {}
    changed: list[str] = []

    # Email
    if "email" in payload:
        email = str(payload["email"] or "").strip()
        if user.email != email:
            user.email = email
            changed.append("email")

    # Role
    if "role" in payload:
        new_role = str(payload["role"])
        if new_role not in USER_ROLE_VALUES:
            return _bad("Invalid role", {"role": [f"must be one of {', '.join(USER_ROLE_VALUES)}"]})
        # Guard: cannot change your own role away from superAdmin
        if user.pk == actor.pk and actor.role == "superAdmin" and new_role != "superAdmin":
            return _bad(
                "Cannot change your own role away from superAdmin", {"role": ["self_lockout"]}
            )
        # Guard: cannot demote the last active superAdmin
        if user.role == "superAdmin" and new_role != "superAdmin":
            active_superadmins = User.objects.filter(role="superAdmin", is_active=True).count()
            if active_superadmins == 1:
                return _bad(
                    "Cannot demote the last active superAdmin", {"role": ["last_superadmin"]}
                )
        if user.role != new_role:
            user.role = new_role
            changed.append("role")

    if "org" in payload:
        org_identifier = payload["org"]
        if org_identifier is None:
            errors.setdefault("org", []).append("cannot be null")
        else:
            new_org = _resolve_org(str(org_identifier))
            if new_org is None:
                errors.setdefault("org", []).append("organisation not found")
            elif user.org_id != new_org.id:
                user.org = new_org
                changed.append("org")

    # is_active
    if "is_active" in payload:
        new_active = parse_bool(payload["is_active"])
        if new_active is None:
            return _bad("is_active must be a boolean", {"is_active": ["must be true or false"]})
        # Guard: cannot deactivate the last active superAdmin
        if user.pk == actor.pk and actor.role == "superAdmin" and not new_active:
            return _bad("Cannot deactivate yourself as superAdmin", {"is_active": ["self_lockout"]})
        if user.role == "superAdmin" and user.is_active and not new_active:
            active_superadmins = User.objects.filter(role="superAdmin", is_active=True).count()
            if active_superadmins == 1:
                return _bad(
                    "Cannot deactivate the last active superAdmin",
                    {"is_active": ["last_superadmin"]},
                )
        if user.is_active != new_active:
            user.is_active = new_active
            changed.append("is_active")

    # Password (optional reset)
    if "password" in payload:
        password = payload["password"]
        if password is not None and password != "":
            try:
                validate_password(password, user=user)
            except ValidationError as e:
                return _bad("Password validation failed", {"password": e.messages})
            user.set_password(password)
            changed.append("password")

    if errors:
        return _bad("Validation failed", errors)

    if not changed:
        return _bad("No updatable field supplied", {})

    user.save(
        update_fields=[
            f
            for f in ["email", "role", "org", "is_active", "password"]
            if f in changed or f == "password"
        ]
    )

    if changed:
        record_user_change(
            request, actor=actor, action="user.updated", target_id=str(user.id), target_org=user.org
        )
    return Response(status=status.HTTP_204_NO_CONTENT)


@serializes_superadmin_changes
def _delete_user(request: Request, user: User, actor: User) -> Response:
    """SuperAdmin DELETE on any user. Guards: cannot delete self, cannot delete last active superAdmin. Audits `user.deleted`."""
    # Guard: cannot delete yourself
    if user.pk == actor.pk:
        return _bad("Cannot delete yourself", {"user": ["self_delete"]})

    # Guard: cannot delete the last active superAdmin
    if user.role == "superAdmin" and user.is_active:
        active_superadmins = User.objects.filter(role="superAdmin", is_active=True).count()
        if active_superadmins == 1:
            return _bad("Cannot delete the last active superAdmin", {"user": ["last_superadmin"]})

    target_id = str(user.id)
    target_org = user.org
    user.delete()

    record_user_change(
        request, actor=actor, action="user.deleted", target_id=target_id, target_org=target_org
    )
    return Response(status=status.HTTP_204_NO_CONTENT)


__all__ = [
    "DEFAULT_USER_ROLE",
    "organisation_collection",
    "organisation_detail",
    "user_collection",
    "user_current",
    "user_detail_management",
]
