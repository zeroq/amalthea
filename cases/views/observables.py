from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from core.serializers import (
    observable_json,
)
from identity.audit import maybe_record_cross_org_read
from observables.extractor import add_observable, extract_into_case
from observables.models import Observable, ObservableType

from ._common import _as_uuid, _bad, _not_found


def case_observable_add(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/observable` — attach one artifact, or re-run extraction.

    A plain helper rather than its own route (the `case_task_create` pattern): the collection
    is served by one view, `case_observable_list`, which dispatches GET/POST itself. That keeps
    both spellings — the slashless one thehive4py posts, and the slashed one — on the same
    handler, so POST can never 405 on a spelling that resolves (verifier V1).

    Deliberately *not* `@api_view`-decorated: wrapping it would make it a fresh
    `WrappedAPIView.as_view()`, and calling that with a DRF `Request` runs
    `initialize_request()` on it — `Request.__init__` then asserts `isinstance(request,
    HttpRequest)` and the endpoint answers 500 for every POST. Exactly the trap
    `case_task_create` documents; the two helpers must stay undecorated together.

    Tenant isolation: requires write access to the case (T4-a).
    """
    case = identity.scoping.resolve_case(request.user, case_id, write=True)
    if case is None:
        return _not_found("Case")
    payload = request.data if isinstance(request.data, dict) else {}
    with transaction.atomic():
        if payload.get("extract") or payload.get("data") is None:
            extract_into_case(case, case.title, case.description)
            case.refresh_from_db()
        if payload.get("data"):
            created = add_observable(case, str(payload.get("dataType") or ""), str(payload["data"]))
            if created is None:
                return Response(
                    {
                        "type": "BadRequest",
                        "message": f"unknown dataType {payload.get('dataType')!r}",
                        "fields": {"dataType": ["not in the observable vocabulary"]},
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
    links = case.case_observables.select_related("observable__data_type").order_by("-created_at")
    return Response([observable_json(link) for link in links])


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_observable_list(request: Request, case_id: str) -> Response:
    """`GET` lists a case's observables; `POST` attaches one and lists the result.

    Both verbs share the route *and* the view, mirroring `case_task_list`. Registering GET on
    the slashless spelling and POST on the slashed one (the pre-verifier split) made
    `POST /api/v1/case/{id}/observable` — the exact spelling thehive4py 2.1.0 sends — answer
    405, because Django resolves the slashless path to the GET view before APPEND_SLASH can
    redirect. One view, both spellings: no spelling can 405.
    """
    if request.method == "POST":
        return case_observable_add(request, case_id)
    case = identity.scoping.resolve_case(request.user, case_id, write=False)
    if case is None:
        return _not_found("Case")

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=case,
        target_type="Case",
        target_id=str(case.id),
        target_org_field="owner_org",
    )

    links = case.case_observables.select_related("observable__data_type").order_by("-created_at")
    return Response([observable_json(link) for link in links])


def _observable_affected_cases(observable: Observable) -> list[dict[str, Any]]:
    """The cases an observable is linked to, in the shape both surfaces report (TODO 6.5)."""
    return [
        {
            "case_id": str(link.case.id),
            "case_number": link.case.number,
            "case_title": link.case.title,
        }
        for link in observable.case_observables.select_related("case").all()
    ]


def _cross_case_error(case_links: list[dict[str, Any]], action: str) -> Response:
    """Build the shared `CrossCaseMutationError` 409 body for the force guard."""
    return Response(
        {
            "type": "CrossCaseMutationError",
            "message": (
                f"Observable is linked to {len(case_links)} cases. "
                f"Use ?force=true to confirm {action} across all cases."
            ),
            "fields": {"force": ["required when observable is linked to multiple cases"]},
            "affected_cases": case_links,
        },
        status=status.HTTP_409_CONFLICT,
    )


def _observable_cross_case_guard(
    observable: Observable, force: bool, *, action: str = "mutation"
) -> Response | None:
    """TODO 6.5 — a multi-case observable needs `?force=true` to be mutated.

    Returns the 409 `CrossCaseMutationError` (listing the affected cases) when `observable` is
    linked to more than one case and the caller did not force, else `None`. Both the single-item
    `observable_detail` (PATCH and DELETE) and `observable_bulk_update` route through here so the
    rule cannot drift between the two surfaces; `action` only tunes the wording ("mutation" vs
    "deletion"), not the rule.
    """
    if force:
        return None
    case_links = _observable_affected_cases(observable)
    if len(case_links) <= 1:
        return None
    return _cross_case_error(case_links, action)


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def observable_detail(request: Request, observable_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/observable/{id}` — one artifact plus the cases it appears in.

    PATCH/DELETE are org-scoped (ADR-004): an `Observable` is per-org deduplicated, so each org
    owns its own rows. Cross-org access is 404 (uniform, no existence oracle).

    `dataType` is the interesting PATCH field: re-typing changes the case-fold rule the identity
    digest is taken under, and `Observable.save()` re-hashes `data_hash` in the same write —
    without which `(owner_org, data_type, data_hash)` would still describe the old type and the
    unique constraint would admit a duplicate of a value already on file.

    **Cross-case protection (TODO 6.5):** If an observable is linked to multiple cases, PATCH/DELETE
    require the `force=true` query parameter. Without it, a 409 is returned listing the affected
    cases. This prevents accidental mutation of shared evidence.
    """
    pk = _as_uuid(observable_id)
    if pk is None:
        return _not_found("Observable")
    observable = identity.scoping.resolve_observable(request.user, str(pk))
    if observable is None:
        return _not_found("Observable")

    # Cross-case protection (TODO 6.5) is enforced through the shared guard below, so PATCH/DELETE
    # agree exactly with the bulk surface.
    force = request.query_params.get("force", "").lower() == "true"

    if request.method == "DELETE":
        guard = _observable_cross_case_guard(observable, force, action="deletion")
        if guard is not None:
            return guard
        observable.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        guard = _observable_cross_case_guard(observable, force)
        if guard is not None:
            return guard
        return _update_observable(observable, request)

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=observable,
        target_type="Observable",
        target_id=str(observable.id),
        target_org_field="owner_org",
    )

    return _observable_detail_payload(observable)


def _set_observable_fields(
    observable: Observable, payload: Mapping[str, Any]
) -> Response | list[str]:
    """Apply `InputUpdateObservable`'s fields to `observable`; shared by detail and `_bulk`."""
    fields: list[str] = []

    if "dataType" in payload:
        obs_type = ObservableType.objects.filter(name=str(payload["dataType"])).first()
        if obs_type is None:
            return _bad(
                f"unknown dataType {payload['dataType']!r}",
                {"dataType": ["not in the observable vocabulary"]},
            )
        observable.data_type = obs_type
        fields.append("data_type")
    if "message" in payload:
        observable.message = str(payload["message"] or "")
        fields.append("message")
    for key in ("tlp", "pap"):
        if key in payload:
            try:
                value = int(payload[key])
            except (TypeError, ValueError):
                value = -1
            if not 0 <= value <= 3:
                return _bad(
                    f"{key} must be an integer between 0 and 3",
                    {key: ["must be an integer between 0 and 3"]},
                )
            setattr(observable, key, value)
            fields.append(key)
    for key, attr in (
        ("ioc", "ioc"),
        ("sighted", "sighted"),
        ("ignoreSimilarity", "ignore_similarity"),
    ):
        if key in payload:
            setattr(observable, attr, bool(payload[key]))
            fields.append(attr)
    return fields


@transaction.atomic
def _update_observable(observable: Observable, request: Request) -> Response:
    """Apply `InputUpdateObservable`'s fields. Only-present, no field, no write.

    Returns 204 (the recorded OpenAPI, and what thehive4py's `observable.update()` reads as
    `None`) rather than the echoed body the alert/case PATCHes return — the two surfaces were
    recorded from different operations and are kept as recorded.
    """
    payload = request.data if isinstance(request.data, dict) else {}
    fields = _set_observable_fields(observable, payload)
    if isinstance(fields, Response):
        return fields
    if not fields:
        return _bad("No updatable field supplied", {})
    observable.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


def _observable_detail_payload(observable: Observable) -> Response:
    payload = {
        "_id": str(observable.id),
        "id": str(observable.id),
        "_type": "observable",
        "dataType": observable.data_type.name,
        "data": observable.data,
        "normalizedData": observable.normalized_data,
        "enrichmentData": observable.enrichment_data or {},
        "tlp": observable.tlp,
        "pap": observable.pap,
        "cases": [
            {
                "_id": str(link.case_id),
                "number": link.case.number,
                "title": link.case.title,
            }
            for link in observable.case_observables.select_related("case").order_by("-created_at")
        ],
    }
    return Response(payload)
