"""Bulk alert update endpoint."""

from __future__ import annotations

from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from alerts.models import Alert
from compat.bulk import bulk_patch

from .alerts import _set_alert_fields


def _resolve_alert_for_bulk(request: Request, raw: str) -> Alert | None:
    """Resolve an alert for bulk update with tenant scoping."""
    return identity.scoping.resolve_alert(request.user, raw)


@api_view(["PATCH"])
@renderer_classes([JSONRenderer])
def alert_bulk_update(request: Request) -> Response:
    """`PATCH /api/v1/alert/_bulk` — apply one field set to many alerts (per-item, AC6.1-P4-a)."""
    return bulk_patch(
        request, ids_key="ids", resolve=_resolve_alert_for_bulk, apply=_set_alert_fields
    )
