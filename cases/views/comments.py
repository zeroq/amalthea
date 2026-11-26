from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from cases.ledger import append_timeline_event
from cases.models import (
    Comment,
)
from core.serializers import (
    comment_json,
)

from ._common import _actor, _as_uuid, _bad, _case_access, _not_found


def _comment_message(request: Request) -> str | Response:
    """The `message` in the body, or a 400 response when it is missing or blank."""
    payload = request.data if isinstance(request.data, dict) else {}
    message = str(payload.get("message") or "").strip()
    if not message:
        return _bad("message is required", {"message": ["required"]})
    return message


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def case_comment_list(request: Request, case_id: str) -> Response:
    """`GET|POST /api/v1/case/{idOrNumber}/comment` — list or add case comments.

    A created comment is also appended to the ledger as a `comment` event in the same transaction:
    the `Comment` row is TheHive's `OutputComment`, while the ledger is what the live case view
    renders, and an entry present in only one of the two would be invisible to half the product.
    The ledger append is what publishes the WebSocket event (AC6.1-P2-a).
    """
    case = _case_access(request, case_id, write=request.method == "POST")
    if case is None:
        return _not_found("Case")
    if request.method == "GET":
        comments = case.comments.select_related("created_by", "updated_by").order_by(
            "-created_at", "-id"
        )
        return Response([comment_json(c) for c in comments])
    message = _comment_message(request)
    if isinstance(message, Response):
        return message
    comment = Comment.objects.create(case=case, message=message, created_by=_actor(request))
    append_timeline_event(
        case,
        title="Comment",
        description=message,
        kind="comment",
        actor=_actor(request),
        metadata={"comment": str(comment.id)},
    )
    return Response(comment_json(comment), status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def comment_detail(request: Request, comment_id: str) -> Response:
    """`PATCH|DELETE /api/v1/comment/{id}` — edit or remove a comment (TheHive 5.8).

    A comment reached through a case obeys the case's share guard: a read-only share can list the
    comment but cannot edit or delete it (AC6.1-P2-b).
    An alert comment is guarded by the alert's org ownership (write access required).
    """
    pk = _as_uuid(comment_id)
    comment = (
        Comment.objects.select_related("case", "alert").filter(pk=pk).first()
        if pk is not None
        else None
    )
    if comment is None:
        return _not_found("Comment")
    if comment.case_id:
        if identity.scoping.resolve_case(request.user, str(comment.case_id), write=True) is None:
            return _not_found("Comment")
    elif comment.alert_id:
        if identity.scoping.resolve_alert(request.user, str(comment.alert_id)) is None:
            return _not_found("Comment")
    else:
        # Orphaned comment with no parent — deny
        return _not_found("Comment")
    if request.method == "DELETE":
        comment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    message = _comment_message(request)
    if isinstance(message, Response):
        return message
    comment.message = message
    comment.updated_by = _actor(request)
    comment.save(update_fields=["message", "updated_by", "updated_at"])
    return Response(comment_json(comment))
