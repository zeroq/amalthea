from __future__ import annotations

from functools import partial

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from cases.models import (
    Page,
)
from core.serializers import (
    page_json,
)
from identity.audit import maybe_record_cross_org_read
from realtime.publisher import publish_case_event

from ._common import _actor, _as_int, _as_uuid, _bad, _case_access, _not_found


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
@transaction.atomic
def case_page_list(request: Request, case_id: str) -> Response:
    """`GET|POST /api/v1/case/{idOrNumber}/page` — list or create case pages (Markdown).

    A created page publishes a `page` WebSocket event on commit, so a second tab sees it without a
    reload (AC6.1-P2-a). It is deliberately **not** a `TimelineEvent`: a page is mutable state, and
    an immutable ledger row per edit would misrepresent it as an event.
    """
    case = _case_access(request, case_id, write=request.method == "POST")
    if case is None:
        return _not_found("Case")
    if request.method == "GET":
        # Audit superAdmin cross-org read
        maybe_record_cross_org_read(
            request,
            actor=request.user,
            target_obj=case,
            target_type="Case",
            target_id=str(case.id),
            target_org_field="owner_org",
        )

        pages = case.pages.select_related("created_by", "updated_by").order_by(
            "order", "created_at"
        )
        return Response([page_json(page) for page in pages])
    payload = request.data if isinstance(request.data, dict) else {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return _bad("title is required", {"title": ["required"]})
    order = _as_int(payload.get("order"))
    if order is None:
        return _bad("order must be an integer", {"order": ["invalid"]})
    page = Page.objects.create(
        case=case,
        title=title[:500],
        content=str(payload.get("content") or ""),
        order=order,
        category=str(payload.get("category") or "")[:100],
        created_by=_actor(request),
    )
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "page", {"event": page_json(page)})
    )
    return Response(page_json(page), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def case_page_detail(request: Request, case_id: str, page_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/case/{idOrNumber}/page/{pageId}` (TheHive 5.8)."""
    case = _case_access(request, case_id, write=request.method in ("PATCH", "DELETE"))
    if case is None:
        return _not_found("Case")
    page_pk = _as_uuid(page_id)
    page = Page.objects.filter(pk=page_pk, case=case).first() if page_pk is not None else None
    if page is None:
        return _not_found("Page")
    if request.method == "DELETE":
        page.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "GET":
        # Audit superAdmin cross-org read
        maybe_record_cross_org_read(
            request,
            actor=request.user,
            target_obj=case,
            target_type="Page",
            target_id=str(page.id),
            target_org_field="owner_org",
        )
        return Response(page_json(page))
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "title" in payload:
        title = str(payload["title"] or "").strip()
        if not title:
            return _bad("title must not be empty", {"title": ["blank"]})
        page.title = title[:500]
        fields.append("title")
    if "content" in payload:
        page.content = str(payload["content"] or "")
        fields.append("content")
    if "category" in payload:
        page.category = str(payload["category"] or "")[:100]
        fields.append("category")
    if "order" in payload:
        order = _as_int(payload["order"])
        if order is None:
            return _bad("order must be an integer", {"order": ["invalid"]})
        page.order = order
        fields.append("order")
    if not fields:
        return _bad("No updatable field supplied", {})
    page.updated_by = _actor(request)
    page.save(update_fields=[*fields, "updated_by", "updated_at"])
    return Response(page_json(page))
