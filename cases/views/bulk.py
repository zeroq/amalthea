from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from cases.ledger import append_timeline_event
from cases.merge import merge_cases
from cases.models import (
    Case,
    Task,
)
from cases.update import (
    CaseUpdateError,
    _apply_case_tags,
    _capture_classification_values,
    _emit_tracked_changes,
    _set_case_fields,
    enforce_close_rule,
    propagate_close_to_alerts,
)
from compat.bulk import bulk_patch
from core.serializers import (
    case_json,
)
from observables.models import Observable

from ._common import (
    _actor,
    _bad,
    _case_access,
    _not_found,
    _resolve_observable_scoped,
    _resolve_task,
)
from .observables import _cross_case_error, _observable_cross_case_guard, _set_observable_fields
from .tasks import _set_task_fields


@api_view(["PATCH"])
@renderer_classes([JSONRenderer])
def case_bulk_update(request: Request) -> Response:
    """`PATCH /api/v1/case/_bulk` — apply one field set to many cases (per-item, AC6.1-P4-a).

    The body is `InputBulkUpdateCase` (`ids` plus any `InputUpdateCase` field). Each id is its own
    savepoint and its own ledger entry: a case that fails (unknown, or a bad value) is reported in
    `results` with its status and does not roll back the others. The 200 envelope is an Amalthea
    extension — TheHive answers 204 with no body — recorded in `docs/spec/deviations.md`, because
    AC6.1-P4-a requires the per-item outcome to be observable.
    """

    def apply_one(case: Case, body: Mapping[str, Any]) -> Response | list[str] | None:
        # Map wire names (camelCase) to model field names (snake_case) for the service layer
        mapped_body = dict(body)
        if "caseType" in mapped_body:
            mapped_body["case_type"] = mapped_body.pop("caseType")
        if "dataClassification" in mapped_body:
            mapped_body["data_classification"] = mapped_body.pop("dataClassification")

        # Capture previous classification values for audit/timeline (S2)
        previous_classification = _capture_classification_values(case)
        previous_stage = case.status.stage if case.status_id else ""

        try:
            fields = _set_case_fields(case, mapped_body, actor=_actor(request))
            # The bulk path does not use `apply_case_update`, so it must call the close rule
            # itself or `PATCH /case/_bulk` would close cases the single PATCH refuses
            # (Plan-2026-10-17 §12.8(4), decision D1).
            enforce_close_rule(case, mapped_body, previous_stage=previous_stage)
        except CaseUpdateError as exc:
            # Return a DRF Response so bulk_patch reports the correct per-item status
            status_code = (
                status.HTTP_403_FORBIDDEN
                if exc.error_type == "AuthorizationError"
                else status.HTTP_400_BAD_REQUEST
            )
            return Response(exc.to_response_payload(), status=status_code)
        tags_touched = any(key in mapped_body for key in ("tags", "addTags", "removeTags"))
        if not fields and not tags_touched:
            return None
        if tags_touched:
            _apply_case_tags(case, mapped_body)
        closing = previous_stage != "Closed" and case.status.stage == "Closed"
        reopening = previous_stage == "Closed" and case.status.stage != "Closed"
        if closing:
            case.stamp_closed_date()
            if case.end_date is None:
                case.end_date = case.closed_date
        elif reopening:
            case.closed_date = None
        if closing or reopening:
            fields = [f for f in fields if f not in ("closed_date", "end_date")]
            fields += ["closed_date", "end_date"]
        if fields:
            case.save(update_fields=[*fields, "updated_at"])
        case.refresh_from_db()

        # Emit classification change events + audit log (S2)
        _emit_tracked_changes(case, previous_classification, actor=_actor(request))

        append_timeline_event(
            case, title="Case updated (bulk)", kind="case-updated", actor=_actor(request)
        )
        # Same invariant as `apply_case_update`: a closed case leaves no open linked alerts.
        if closing:
            propagate_close_to_alerts(case, case.verdict, actor=_actor(request))
        return fields

    return bulk_patch(
        request,
        ids_key="ids",
        resolve=lambda req, raw: _case_access(req, raw, write=True),
        apply=apply_one,
    )


def _resolve_task_for_bulk(request: Request, raw: str) -> Task | None:
    """Resolve a task for bulk update (requires write access)."""
    return _resolve_task(request, raw, write=True)


@api_view(["PATCH"])
@renderer_classes([JSONRenderer])
def task_bulk_update(request: Request) -> Response:
    """`PATCH /api/v1/task/_bulk` — apply one field set to many tasks."""
    return bulk_patch(
        request, ids_key="ids", resolve=_resolve_task_for_bulk, apply=_set_task_fields
    )


def _resolve_observable_for_bulk(request: Request, raw: str) -> Observable | None:
    """Resolve an observable for bulk update with tenant scoping."""
    return _resolve_observable_scoped(request, raw)


def _bulk_cross_case_guard(request: Request, ids: list[Any]) -> Response | None:
    """Pre-flight the TODO 6.5 guard over every id before `bulk_patch` mutates anything.

    The single-item endpoint refuses a shared observable; the bulk surface must not offer a way
    around it. Each id is resolved with tenant scoping and run through the same
    `_observable_cross_case_guard`. If any target is linked to more than one case and the request
    lacks `?force=true`, the whole request aborts with the same 409 the single-item endpoint
    returns, and the affected cases from every offending observable are merged (deduplicated by
    case id) so the caller sees the full blast radius. `?force=true` skips the check, exactly as
    it does for a single row.
    """
    force = request.query_params.get("force", "").lower() == "true"
    affected: dict[str, dict[str, Any]] = {}
    for raw in ids:
        observable = _resolve_observable_for_bulk(request, str(raw))
        if observable is None:
            continue
        guard = _observable_cross_case_guard(observable, force)
        if guard is None:
            continue
        for link in guard.data["affected_cases"]:
            affected.setdefault(link["case_id"], link)
    if not affected:
        return None
    return _cross_case_error(list(affected.values()), "mutation")


@api_view(["PATCH"])
@renderer_classes([JSONRenderer])
def observable_bulk_update(request: Request) -> Response:
    """`PATCH /api/v1/observable/_bulk` — apply one field set to many observables.

    The TODO 6.5 cross-case guard is a pre-flight over every resolved id, so a shared target
    aborts the batch with a 409 before any row is written (the caller must repeat with
    `?force=true`, mirroring `PATCH /observable/{id}`). Once the guard passes, `bulk_patch`'s
    per-item boundary still governs the non-guard failures (unknown id, bad field): one bad id
    does not roll back its siblings.
    """
    payload = request.data if isinstance(request.data, dict) else {}
    ids = payload.get("ids")
    if isinstance(ids, list) and ids:
        guard = _bulk_cross_case_guard(request, ids)
        if guard is not None:
            return guard
    return bulk_patch(
        request,
        ids_key="ids",
        resolve=_resolve_observable_for_bulk,
        apply=_set_observable_fields,
    )


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def case_merge(request: Request, case_ids: str) -> Response:
    """`POST /api/v1/case/_merge/{id1,id2,...}` — fold the later cases into the first (AC6.1-P4-b).

    Ids are resolved best-effort so a replay is idempotent: the merged-away cases are gone, so the
    second call resolves only the surviving target and `merge_cases` is a no-op that still returns
    it. A case the caller may not write (foreign org / share without `write`) is treated as
    unresolvable and skipped; if *nothing* resolves the endpoint answers 404.
    """
    tokens = [token.strip() for token in case_ids.split(",") if token.strip()]
    if not tokens:
        return _bad("at least one case id is required", {"ids": ["required"]})
    cases = [case for token in tokens if (case := _case_access(request, token, write=True))]
    if not cases:
        return _not_found("Case")
    target = merge_cases(cases, actor=_actor(request))
    target.refresh_from_db()
    return Response(case_json(target, detail=True))
