"""Alert comment endpoints."""

from __future__ import annotations

from functools import partial

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from cases.models import Comment
from core.serializers import comment_json
from identity.audit import maybe_record_cross_org_read
from realtime.publisher import publish_case_event, publish_org_event

from ._common import _actor, _bad


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def alert_comment_list(request: Request, alert_id: str) -> Response:
    """`GET|POST /api/v1/alert/{alertId}/comment` — list or add alert comments (TheHive 5.8).

    An alert already promoted to a case mirrors a new comment onto that case's WebSocket so an
    open case view updates without a reload; an un-promoted alert has no socket and is fetched on
    navigation. The comment itself always hangs off the alert's stable `_id`.
    """
    alert = identity.scoping.resolve_alert(request.user, alert_id)
    if alert is None:
        return Response(
            {"type": "NotFoundError", "message": "Alert not found"},
            status=status.HTTP_404_NOT_FOUND,
        )
    if request.method == "GET":
        # Audit superAdmin cross-org read
        maybe_record_cross_org_read(
            request,
            actor=request.user,
            target_obj=alert,
            target_type="Alert",
            target_id=str(alert.id),
            target_org_field="owner_org",
        )

        comments = alert.comments.select_related("created_by", "updated_by").order_by(
            "-created_at", "-id"
        )
        return Response([comment_json(c) for c in comments])
    payload = request.data if isinstance(request.data, dict) else {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return _bad("message is required", {"message": ["required"]})
    comment = Comment.objects.create(alert=alert, message=message, created_by=_actor(request))
    if alert.case_id:
        # Publish case timeline event (existing behavior)
        transaction.on_commit(
            partial(
                publish_case_event,
                str(alert.case_id),
                "comment",
                {"event": comment_json(comment)},
            )
        )
        # Also emit an org refresh event so the case list/dashboard updates
        transaction.on_commit(
            lambda: publish_org_event(
                str(alert.owner_org_id),
                "refresh",
                {
                    "surface": "cases",
                    "reason": "case-updated",
                    "object_id": str(alert.case_id),
                    "occurred_at": timezone.now().isoformat(),
                },
            )
        )
    return Response(comment_json(comment), status=status.HTTP_201_CREATED)
