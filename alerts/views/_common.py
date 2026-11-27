"""Shared helpers for the alerts API view package."""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from alerts.models import Alert


def _actor(request: Request) -> Any:
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


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
            return Response(
                {"type": "NotFoundError", "message": "Resource not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
    return None


def _resolve_alert_scoped(request: Request, alert_id: str) -> Alert | None:
    """Resolve an alert the caller may reach (direct org ownership)."""
    return identity.scoping.resolve_alert(request.user, alert_id)


def _not_found_alert(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"},
        status=status.HTTP_404_NOT_FOUND,
    )
