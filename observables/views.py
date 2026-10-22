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

from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from core.serializers import observable_suggestion_json, observable_type_json
from observables.models import ObservableSuggestion, ObservableType
from observables.suggestions import (
    AcceptSuggestionError,
    accept_suggestion,
    bulk_review_suggestions,
)


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


# --- P5: ObservableSuggestion endpoints ----------------------------------------


def _actor(request: Request) -> Any:
    user = getattr(request, "user", None)
    return user if getattr(user, "is_authenticated", False) else None


def _resolve_suggestion_scoped(request: Request, suggestion_id: str) -> ObservableSuggestion | None:
    """Resolve a suggestion the caller may reach (org-scoped via case or alert)."""
    pk = _as_uuid(suggestion_id)
    if pk is None:
        return None
    suggestion = (
        ObservableSuggestion.objects.select_related(
            "case", "source_alert", "data_type", "owner_org"
        )
        .filter(pk=pk)
        .first()
    )
    if suggestion is None:
        return None
    # Check org access via case or alert
    if suggestion.case_id:
        case = identity.scoping.resolve_case(request.user, str(suggestion.case_id), write=False)
        if case is None:
            return None
        return suggestion
    if suggestion.source_alert_id:
        alert = identity.scoping.resolve_alert(
            request.user, str(suggestion.source_alert_id), write=False
        )
        if alert is None:
            return None
        return suggestion
    # Fallback: direct org ownership via observable
    if suggestion.observable_id:
        obs = identity.scoping.resolve_observable(request.user, str(suggestion.observable_id))
        if obs is None:
            return None
    return suggestion


def _can_write_suggestion(request: Request, suggestion: ObservableSuggestion) -> bool:
    """Check if caller has write access to the suggestion's case or alert."""
    if suggestion.case_id:
        return (
            identity.scoping.resolve_case(request.user, str(suggestion.case_id), write=True)
            is not None
        )
    if suggestion.source_alert_id:
        return (
            identity.scoping.resolve_alert(
                request.user, str(suggestion.source_alert_id), write=True
            )
            is not None
        )
    return False


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def case_suggestions_list(request: Request, case_id: str) -> Response:
    """`GET /api/v1/case/{caseId}/suggestions` — list suggestions for a case."""
    case = identity.scoping.resolve_case(request.user, case_id, write=False)
    if case is None:
        return _not_found("Case")

    queryset = ObservableSuggestion.objects.select_related(
        "data_type", "observable__data_type", "reviewed_by"
    ).filter(case=case)
    status_filter = request.query_params.get("status")
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    # Filter by alert if provided
    alert_id = request.query_params.get("alert")
    if alert_id:
        queryset = queryset.filter(source_alert_id=alert_id)

    return Response([observable_suggestion_json(s) for s in queryset.order_by("-created_at")])


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def alert_suggestions_list(request: Request, alert_id: str) -> Response:
    """`GET /api/v1/alert/{alertId}/suggestions` — list suggestions for an alert."""
    alert = identity.scoping.resolve_alert(request.user, alert_id, write=False)
    if alert is None:
        return _not_found("Alert")

    queryset = ObservableSuggestion.objects.select_related(
        "data_type", "observable__data_type", "reviewed_by"
    ).filter(source_alert=alert)
    status_filter = request.query_params.get("status")
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    return Response([observable_suggestion_json(s) for s in queryset.order_by("-created_at")])


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def suggestion_accept(request: Request, suggestion_id: str) -> Response:
    """`POST /api/v1/suggestions/{suggestionId}/accept` — accept a suggestion, creating the observable link."""
    suggestion = _resolve_suggestion_scoped(request, suggestion_id)
    if suggestion is None:
        return _not_found("ObservableSuggestion")
    if not _can_write_suggestion(request, suggestion):
        return Response(
            {"type": "AuthorizationError", "message": "Write access required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if suggestion.status == "accepted":
        # Idempotent: already accepted, return current state
        return Response(observable_suggestion_json(suggestion))

    if suggestion.status in ("rejected", "ignored"):
        return _bad(
            "Cannot accept a rejected or ignored suggestion",
            {"status": ["suggestion is not proposed"]},
        )

    with transaction.atomic():
        try:
            accept_suggestion(suggestion, actor=_actor(request))
        except AcceptSuggestionError as exc:
            return _bad(
                str(exc),
                {"dataType": ["not in the observable vocabulary"]},
            )

    return Response(observable_suggestion_json(suggestion))


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def suggestion_reject(request: Request, suggestion_id: str) -> Response:
    """`POST /api/v1/suggestions/{suggestionId}/reject` — reject a suggestion (mark reviewed, no link)."""
    suggestion = _resolve_suggestion_scoped(request, suggestion_id)
    if suggestion is None:
        return _not_found("ObservableSuggestion")
    if not _can_write_suggestion(request, suggestion):
        return Response(
            {"type": "AuthorizationError", "message": "Write access required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if suggestion.status == "rejected":
        # Idempotent: already rejected
        return Response(observable_suggestion_json(suggestion))

    if suggestion.status == "accepted":
        return _bad(
            "Cannot reject an accepted suggestion", {"status": ["suggestion already accepted"]}
        )

    with transaction.atomic():
        suggestion.status = "rejected"
        suggestion.reviewed_by = _actor(request)
        suggestion.reviewed_at = timezone.now()
        suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    return Response(observable_suggestion_json(suggestion))


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def suggestion_ignore(request: Request, suggestion_id: str) -> Response:
    """`POST /api/v1/suggestions/{suggestionId}/ignore` — ignore a suggestion (mark reviewed, no link)."""
    suggestion = _resolve_suggestion_scoped(request, suggestion_id)
    if suggestion is None:
        return _not_found("ObservableSuggestion")
    if not _can_write_suggestion(request, suggestion):
        return Response(
            {"type": "AuthorizationError", "message": "Write access required"},
            status=status.HTTP_403_FORBIDDEN,
        )

    if suggestion.status == "ignored":
        # Idempotent: already ignored
        return Response(observable_suggestion_json(suggestion))

    if suggestion.status == "accepted":
        return _bad(
            "Cannot ignore an accepted suggestion", {"status": ["suggestion already accepted"]}
        )

    with transaction.atomic():
        suggestion.status = "ignored"
        suggestion.reviewed_by = _actor(request)
        suggestion.reviewed_at = timezone.now()
        suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

    return Response(observable_suggestion_json(suggestion))


def _bulk_action(request: Request, target_status: str, opposite_statuses: set[str]) -> Response:
    """Shared logic for bulk accept/reject/ignore."""
    payload = request.data if isinstance(request.data, dict) else {}
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        return _bad("ids is required (non-empty array)", {"ids": ["required"]})

    # Resolve all suggestions the caller can access
    suggestions = ObservableSuggestion.objects.select_related(
        "case", "source_alert", "data_type", "owner_org", "observable__data_type"
    ).filter(id__in=ids)

    # Filter by org access
    accessible_ids = []
    for s in suggestions:
        if _resolve_suggestion_scoped(request, str(s.id)):
            accessible_ids.append(s.id)

    if not accessible_ids:
        return Response([])

    # Filter out already-processed or opposite-status suggestions
    to_process = suggestions.filter(id__in=accessible_ids).exclude(status__in=opposite_statuses)

    results = []
    with transaction.atomic():
        for suggestion, error in bulk_review_suggestions(
            to_process, target_status=target_status, actor=_actor(request)
        ):
            if error is not None:
                results.append({"id": str(suggestion.id), "error": error})
            else:
                results.append(observable_suggestion_json(suggestion))

    return Response(results)


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def suggestions_bulk_accept(request: Request) -> Response:
    """`POST /api/v1/suggestions/bulk-accept` — accept multiple suggestions."""
    return _bulk_action(request, "accepted", {"rejected", "ignored"})


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def suggestions_bulk_reject(request: Request) -> Response:
    """`POST /api/v1/suggestions/bulk-reject` — reject multiple suggestions."""
    return _bulk_action(request, "rejected", {"accepted"})


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def suggestions_bulk_ignore(request: Request) -> Response:
    """`POST /api/v1/suggestions/bulk-ignore` — ignore multiple suggestions."""
    return _bulk_action(request, "ignored", {"accepted"})


__all__ = [
    "alert_suggestions_list",
    "case_suggestions_list",
    "observable_type_collection",
    "observable_type_detail",
    "suggestion_accept",
    "suggestion_ignore",
    "suggestion_reject",
    "suggestions_bulk_accept",
    "suggestions_bulk_ignore",
    "suggestions_bulk_reject",
]
