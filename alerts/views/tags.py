"""Tag link endpoints for alerts."""

from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.models import AlertTagLink
from cases.models import Tag
from cases.tagging import tag_names_from_payload
from core.serializers import tag_json

from ._common import _bad, _not_found_alert, _resolve_alert_scoped


def _requested_tags(request: Request) -> list[str] | Response:
    """The tag names in the body, or a 400 response when none were supplied."""
    payload = request.data if isinstance(request.data, dict) else {}
    names = tag_names_from_payload(payload)
    if names is None:
        return _bad("tags is required", {"tags": ["required"]})
    return names


@api_view(["POST", "DELETE"])
@renderer_classes([JSONRenderer])
def alert_tag_link(request: Request, alert_id: str) -> Response:
    """`POST|DELETE /api/v1/alert/{alertId}/tag` — attach or detach tags (200 with the set).

    TheHive 5.8 has no such route (tags arrive in the create/update body); recorded in
    `docs/spec/deviations.md` with its case/observable siblings. Idempotent in both directions.
    """
    alert = _resolve_alert_scoped(request, alert_id)
    if alert is None:
        return _not_found_alert("Alert")
    names = _requested_tags(request)
    if isinstance(names, Response):
        return names
    if request.method == "DELETE":
        AlertTagLink.objects.filter(alert=alert, tag__name__in=names).delete()
    else:
        for name in names:
            tag, _created = Tag.objects.get_or_create(name=name)
            AlertTagLink.objects.get_or_create(alert=alert, tag=tag)
    return Response([tag_json(tag) for tag in alert.tags.order_by("name")])


@api_view(["POST"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def alert_tag_add(request: Request, alert_id: str) -> Response:
    """POST /api/v1/alert/{alertId}/tag/add — attach existing tags to an alert (201).

    TheHive 5.8 has no dedicated endpoint; tags arrive through the create/update body.
    This dedicated add endpoint keeps the operation explicit and idempotent —
    re-linking an existing tag is a no-op — and returns the alert's updated tag set.
    """
    alert = _resolve_alert_scoped(request, alert_id)
    if alert is None:
        return Response(
            {"type": "NotFoundError", "message": "Alert not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not isinstance(request.data, dict):
        return Response(
            {"type": "BadRequest", "message": "Request body must be a JSON object"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    names = tag_names_from_payload(request.data)
    if names is None:
        return Response(
            {"type": "BadRequest", "message": "No tags supplied"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # The input is a list, so the same name can appear more than once. Dedupe before attaching:
    # `AlertTagLink` is unique per (alert, tag), and a repeated name would otherwise 500 on the
    # second insert.
    names = list(dict.fromkeys(names))

    # Ensure no duplicate tags (same name as an existing alert tag)
    existing_tag_names = [tag.name for tag in alert.tags.all()]
    for name in names:
        if name in existing_tag_names:
            return Response(
                {"type": "BadRequest", "message": f"Tag '{name}' already assigned to this alert."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Validate and create each tag
    for name in names:
        if not name:
            return Response(
                {"type": "BadRequest", "message": "Empty tag name"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tag, _ = Tag.objects.get_or_create(name=name, defaults={"colour": "", "description": ""})

    # Attach the tags to the alert
    for name in names:
        tag = Tag.objects.get(name=name)
        AlertTagLink.objects.create(alert=alert, tag=tag)

    # Return updated tag set (existing + new)
    return Response([tag_json(tag) for tag in alert.tags.order_by("name")], status=201)
