"""User identity API views."""

from __future__ import annotations

from typing import cast

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from core.enums import USER_ROLE_VALUES
from core.serializers import user_admin_json, user_json
from identity.audit import record_user_change
from identity.models import DEFAULT_USER_ROLE, User
from identity.services import parse_bool, serializes_superadmin_changes

from .common import _bad, _not_found, _require_superadmin, _resolve_org, _resolve_user


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def user_current(request: Request) -> Response:
    """`GET /api/v1/user/current` — the authenticated caller as `OutputUser`."""
    return Response(user_json(cast(User, request.user)))


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
