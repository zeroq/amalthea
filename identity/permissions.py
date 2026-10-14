"""DRF permission classes for tenant isolation (P1 — Foundations).

These are minimal, explicit guards used by views to enforce role/org requirements.
They complement the scoping helpers in `identity.scoping` — the helpers answer
"which rows may this caller see?"; these answer "may this caller enter this view?".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import permissions

if TYPE_CHECKING:
    # Annotation-only, and deliberately not imported at runtime: this module is named by
    # `REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]`, so it is imported while settings load —
    # pulling in `rest_framework.views` there is a circular import.
    from rest_framework.request import Request
    from rest_framework.views import APIView


class IsSuperAdmin(permissions.BasePermission):  # type: ignore[misc]
    """
    Allow only authenticated users with `role == "superAdmin"`.

    Cross-tenant reads by superAdmin are audited by the view via `AuditLog`.
    """

    message = "SuperAdmin role required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and getattr(user, "role", "") == "superAdmin"
        )


class IsOrgAdminOrAbove(permissions.BasePermission):  # type: ignore[misc]
    """
    Allow authenticated users with `role` in {"superAdmin", "admin"}.

    Used for org-scoped administrative actions (share grant/revoke, token mgmt).
    """

    message = "Admin role or above required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        if not (user and getattr(user, "is_authenticated", False)):
            return False
        role = getattr(user, "role", "")
        return role in ("superAdmin", "admin")


class IsAnalystOrAbove(permissions.BasePermission):  # type: ignore[misc]
    """
    Allow authenticated users with `role` in {"superAdmin", "admin", "analyst"}.

    Used for playbook authoring and execution — `read-only` users are denied.
    """

    message = "Analyst role or above required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        if not (user and getattr(user, "is_authenticated", False)):
            return False
        role = getattr(user, "role", "")
        return role in ("superAdmin", "admin", "analyst")


class ViewerReadOnly(permissions.BasePermission):  # type: ignore[misc]
    """
    Deny every unsafe method (POST/PUT/PATCH/DELETE) to the viewer role (ADR-006).

    **Additive, not an allow-list**: it denies if and only if the caller is a viewer
    and the verb is unsafe. Anything else passes, so it composes with the classes
    already in `DEFAULT_PERMISSION_CLASSES` — anonymous callers are still a 401 from
    `IsAuthenticated`, a read-scoped API key still hits `ScopePermission`, and the
    `AllowAny` login/logout and the non-DRF ingest webhook are untouched.

    Registered globally, which is what makes it fail-closed: a new endpoint is
    covered without being touched. The counterpart for the UI is
    `ui.views.not_viewer`, and the invariants are asserted by
    `tests/conformance/test_viewer_readonly_*.py`.
    """

    message = "Your role is read-only."

    def has_permission(self, request: Request, view: APIView) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        from identity.scoping import is_viewer

        return not is_viewer(getattr(request, "user", None))


class IsAuthenticatedAndOrgMember(permissions.BasePermission):  # type: ignore[misc]
    """
    Allow only authenticated users who belong to an organisation.

    Org-less authenticated users (e.g. a freshly created superuser before org
    assignment) are denied — they have no tenant context.
    """

    message = "Authenticated organisation member required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user
            and getattr(user, "is_authenticated", False)
            and getattr(user, "org_id", None) is not None
        )


__all__ = [
    "IsAnalystOrAbove",
    "IsAuthenticatedAndOrgMember",
    "IsOrgAdminOrAbove",
    "IsSuperAdmin",
    "ViewerReadOnly",
]
