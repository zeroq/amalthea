"""DRF permission classes for tenant isolation (P1 — Foundations).

These are minimal, explicit guards used by views to enforce role/org requirements.
They complement the scoping helpers in `identity.scoping` — the helpers answer
"which rows may this caller see?"; these answer "may this caller enter this view?".
"""

from __future__ import annotations

from rest_framework import permissions
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
]
