from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from cases.models import (
    CaseTagLink,
    Tag,
)
from cases.tagging import tag_names_from_payload
from core.serializers import (
    tag_json,
)
from observables.models import ObservableTagLink

from ._common import _as_uuid, _bad, _not_found


def _resolve_tag(identifier: str) -> Tag | None:
    """Resolve `{tagId}` as a UUID first, then as the unique `name`."""
    pk = _as_uuid(identifier)
    if pk is not None:
        found = Tag.objects.filter(pk=pk).first()
        if found is not None:
            return found
    return Tag.objects.filter(name=identifier).first()


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def tag_collection(request: Request) -> Response:
    """`GET /api/v1/tag` lists; `POST /api/v1/tag` creates (201).

    TheHive 5.8 exposes only `GET|PATCH|DELETE /tag/{tagId}` — no collection — and mints tags
    implicitly when they are attached. This REST collection is an Amalthea extension
    (`docs/spec/deviations.md`) so a tag can be described before it is used.
    """
    if request.method == "POST":
        return _create_tag(request)
    return Response([tag_json(tag) for tag in Tag.objects.order_by("name")])


def _create_tag(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    name = str(payload.get("name") or payload.get("predicate") or "").strip()
    if not name:
        return _bad("name is required", {"name": ["required"]})
    if Tag.objects.filter(name=name[:100]).exists():
        return _bad("name already exists", {"name": ["already exists"]})
    tag = Tag.objects.create(
        name=name[:100],
        colour=str(payload.get("colour") or "")[:20],
        description=str(payload.get("description") or ""),
    )
    return Response(tag_json(tag), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def tag_detail(request: Request, tag_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/tag/{tagId}`.

    DELETE is guarded: a tag still attached to a case, alert or observable is a **400**. TheHive
    cascades, but the tag *is* the classification and removing it everywhere silently would be
    the data loss this endpoint exists to refuse (recorded in `docs/spec/deviations.md`). PATCH
    accepts the recorded `InputUpdateTag` fields (`predicate`, `description`, `colour`) and our
    `name` spelling.
    """
    tag = _resolve_tag(tag_id)
    if tag is None:
        return _not_found("Tag")
    if request.method == "DELETE":
        if _tag_in_use(tag):
            return _bad(
                "Tag is in use",
                {"_id": ["a case, alert or observable still carries this tag"]},
            )
        tag.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_tag(tag, request)
    return Response(tag_json(tag))


def _tag_in_use(tag: Tag) -> bool:
    # `exists()` per link table: a tag carried by any of the three owner kinds is in use.
    return bool(
        tag.case_links.exists() or tag.alert_links.exists() or tag.observable_links.exists()
    )


def _update_tag(tag: Tag, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    if "predicate" in payload or "name" in payload:
        key = "predicate" if "predicate" in payload else "name"
        name = str(payload[key] or "").strip()
        if not name:
            return _bad(f"{key} is required", {key: ["required"]})
        if Tag.objects.exclude(pk=tag.pk).filter(name=name[:100]).exists():
            return _bad("name already exists", {key: ["already exists"]})
        tag.name = name[:100]
        fields.append("name")
    if "description" in payload:
        tag.description = str(payload["description"] or "")
        fields.append("description")
    if "colour" in payload:
        tag.colour = str(payload["colour"] or "")[:20]
        fields.append("colour")
    if not fields:
        return _bad("No updatable field supplied", {})
    tag.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


def _requested_tags(request: Request) -> list[str] | Response:
    """The tag names in the body, or a 400 response when none were supplied."""
    payload = request.data if isinstance(request.data, dict) else {}
    names = tag_names_from_payload(payload)
    if names is None:
        return _bad("tags is required", {"tags": ["required"]})
    return names


@api_view(["POST", "DELETE"])
@renderer_classes([JSONRenderer])
def case_tag_link(request: Request, case_id: str) -> Response:
    """`POST|DELETE /api/v1/case/{idOrNumber}/tag` — attach or detach tags (200 with the set).

    TheHive 5.8 has no such route; tags arrive through the case create/update body. A dedicated
    link endpoint keeps the operation explicit and idempotent — re-linking and un-linking an
    absent tag are both no-ops — and returns the case's current tag set so the caller can render
    it without a follow-up read (recorded in `docs/spec/deviations.md`).
    """
    case = identity.scoping.resolve_case(request.user, case_id, write=True)
    if case is None:
        return _not_found("Case")
    names = _requested_tags(request)
    if isinstance(names, Response):
        return names
    if request.method == "DELETE":
        CaseTagLink.objects.filter(case=case, tag__name__in=names).delete()
    else:
        for name in names:
            tag, _created = Tag.objects.get_or_create(name=name)
            CaseTagLink.objects.get_or_create(case=case, tag=tag)
    return Response([tag_json(tag) for tag in case.tags.order_by("name")])


@api_view(["POST", "DELETE"])
@renderer_classes([JSONRenderer])
def observable_tag_link(request: Request, observable_id: str) -> Response:
    """`POST|DELETE /api/v1/observable/{id}/tag` — attach or detach tags (200 with the set).

    Org-scoped (ADR-004): an `Observable` is per-org deduplicated, so each org owns its own rows.
    Cross-org access returns uniform 404 (no existence oracle).
    """
    pk = _as_uuid(observable_id)
    if pk is None:
        return _not_found("Observable")
    observable = identity.scoping.resolve_observable(request.user, str(pk))
    if observable is None:
        return _not_found("Observable")
    names = _requested_tags(request)
    if isinstance(names, Response):
        return names
    if request.method == "DELETE":
        ObservableTagLink.objects.filter(observable=observable, tag__name__in=names).delete()
    else:
        for name in names:
            tag, _created = Tag.objects.get_or_create(name=name)
            ObservableTagLink.objects.get_or_create(observable=observable, tag=tag)
    return Response([tag_json(tag) for tag in observable.tags.order_by("name")])
