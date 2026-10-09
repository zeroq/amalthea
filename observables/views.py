"""T2 `observable/type` endpoints — the observable vocabulary as a managed resource.

TheHive 5.8 exposes observable types through `POST /query` (`listObservableType`) plus a
`GET|PATCH|DELETE /observable/type/{typeId}` detail; it has no REST collection. This module serves
the same resource with a REST collection GET/POST (recorded in `docs/spec/deviations.md`), which
is what makes the vocabulary editable without hand-writing a query document.

`PATCH` touches only `isCaseSensitive`/`isAttachment` — the recorded `InputUpdateObservableType`
fields — and goes through `ObservableType.save()` so a case-rule change re-hashes that type's
observables (`observables/models.py`), which is why the save is a full save rather than a
`QuerySet.update()`.
"""

from __future__ import annotations

from uuid import UUID

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from core.serializers import observable_type_json
from observables.models import ObservableType


def _not_found(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"},
        status=status.HTTP_404_NOT_FOUND,
    )


def _bad(message: str, fields: dict[str, list[str]]) -> Response:
    return Response(
        {"type": "BadRequest", "message": message, "fields": fields},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _as_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _lookup(identifier: str) -> ObservableType | None:
    """Resolve `{typeId}` as a UUID first, then as the unique `name` (recorded contract)."""
    pk = _as_uuid(identifier)
    if pk is not None:
        found = ObservableType.objects.filter(pk=pk).first()
        if found is not None:
            return found
    return ObservableType.objects.filter(name=identifier).first()


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def observable_type_collection(request: Request) -> Response:
    """`GET` lists the vocabulary; `POST` adds a type (201)."""
    if request.method == "POST":
        return _create_type(request)
    return Response(
        [observable_type_json(otype) for otype in ObservableType.objects.order_by("name")]
    )


def _create_type(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return _bad("name is required", {"name": ["required"]})
    if ObservableType.objects.filter(name=name[:100]).exists():
        return _bad("name already exists", {"name": ["already exists"]})
    otype = ObservableType.objects.create(
        name=name[:100],
        is_attachment=bool(payload.get("isAttachment") or False),
        is_case_sensitive=bool(payload.get("isCaseSensitive") or False),
    )
    return Response(observable_type_json(otype), status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def observable_type_detail(request: Request, type_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/observable/type/{typeId}`.

    DELETE is guarded: an `ObservableType` in use is a **400**, not a cascade that would silently
    take a case's evidence with it, and not the FK `PROTECT`'s 500. The check is the same shape
    the recorded OpenAPI documents ("An observable type can't be deleted while at least one
    observable uses it").
    """
    otype = _lookup(type_id)
    if otype is None:
        return _not_found("ObservableType")
    if request.method == "DELETE":
        if otype.observables.exists():
            return _bad(
                "Observable type is in use",
                {"_id": ["at least one observable uses this type"]},
            )
        otype.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_type(otype, request)
    return Response(observable_type_json(otype))


def _update_type(otype: ObservableType, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []
    for key, attr in (
        ("isAttachment", "is_attachment"),
        ("isCaseSensitive", "is_case_sensitive"),
    ):
        if key in payload:
            setattr(otype, attr, bool(payload[key]))
            fields.append(attr)
    if not fields:
        return _bad("No updatable field supplied", {})
    # `save()` is where the re-hash hook lives; it reads the persisted flag before writing, so a
    # partial write still detects the case-rule flip and re-hashes the type's observables.
    otype.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


__all__ = [
    "observable_type_collection",
    "observable_type_detail",
]
