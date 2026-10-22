"""Observable suggestion staging service (P4) — extraction creates suggestions only.

This module replaces direct `extract_into_case` calls with a staging workflow:
1. Extract findings with provenance
2. Apply aggressive filtering
3. Create/update `ObservableSuggestion` rows as `proposed` (idempotent; accepted stays accepted)
4. **Never** create `CaseObservable` or dispatch triggers here
5. Attach suggestions to both `source_alert` (when available) and `case`

Manual `add_observable` / `add_observable_with_status` remain immediate (unchanged).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from observables.extractor import find_observables, json_paths_and_values, resolve_observable
from observables.filtering import should_keep_finding
from observables.models import Observable, ObservableSuggestion, ObservableType

if TYPE_CHECKING:
    from alerts.models import Alert
    from cases.models import Case
    from identity.models import User


@transaction.atomic
def create_suggestions_for_case(
    case: Case,
    *texts: str,
    source_alert: Alert | None = None,
    raw_payload: dict[str, Any] | list[Any] | None = None,
    **kwargs: Any,
) -> list[ObservableSuggestion]:
    """Create observable suggestions for a case from text and/or alert payload.

    This is the new extraction entry point (P4). It:
    - Extracts findings with full provenance (JSON paths)
    - Applies aggressive filtering (filtered items still recorded with `filtered_out=True`)
    - Creates/updates `ObservableSuggestion` rows idempotently
    - **Does not** create `CaseObservable` links
    - **Does not** dispatch automation triggers
    - Attaches suggestions to both `case` and `source_alert` (when provided)

    Args:
        case: The case to attach suggestions to.
        *texts: Free-text strings (case title, description, alert title, etc.).
        source_alert: Optional alert these suggestions originate from.
        raw_payload: Optional raw JSON payload (e.g., alert.raw_payload) for path-based provenance.
        **kwargs: Additional context for filtering (e.g., feed_type, source).

    Returns:
        List of created/updated ObservableSuggestion objects.
    """
    org = case.owner_org
    vocabulary = {t.name: t for t in ObservableType.objects.all()}

    # Build path_values from raw_payload for provenance
    path_values: list[tuple[str, str]] = []
    if raw_payload:
        path_values = [(p, v) for p, v in json_paths_and_values(raw_payload) if isinstance(v, str)]

    source_alert_id = str(source_alert.id) if source_alert else None

    # Build filtering context
    filter_context = {
        "source_alert_source": source_alert.source if source_alert else None,
        "feed_type": getattr(source_alert, "ingestion_source_id", None),
        **kwargs,
    }

    # One query for the current review states, so the loop below does not hit the DB per finding.
    existing_statuses: dict[tuple[Any, str], str] = {
        (data_type_id, normalized): status
        for data_type_id, normalized, status in ObservableSuggestion.objects.filter(
            owner_org=org, case=case, source_alert=source_alert
        ).values_list("data_type_id", "normalized_data", "status")
    }

    suggestions: list[ObservableSuggestion] = []

    for finding in find_observables(
        *texts,
        types=vocabulary,
        source_alert_id=source_alert_id,
        path_values=path_values,
    ):
        # Apply filtering
        keep, filter_reason, _filter_category = should_keep_finding(finding, filter_context)

        # Build suggestion reason from provenance
        reason_parts = []
        if finding.source_paths:
            reason_parts.append(f"Extracted from fields: {', '.join(finding.source_paths)}")
        if finding.source_context.get("parent_keys"):
            reason_parts.append(f"Parent keys: {', '.join(finding.source_context['parent_keys'])}")
        if filter_reason:
            reason_parts.append(f"Filtered: {filter_reason}")
        reason = "; ".join(reason_parts)

        # Idempotent create/update via unique constraint. A rejected/ignored finding is offered
        # again, but an accepted one must stay accepted: the observable link already exists and
        # the reviewer's decision must survive re-extraction.
        lookup: dict[str, Any] = {
            "owner_org": org,
            "data_type": vocabulary[finding.type_name],
            "normalized_data": finding.normalized,
            "case": case,
            "source_alert": source_alert,
        }
        type_id = vocabulary[finding.type_name].pk
        status = (
            "accepted"
            if existing_statuses.get((type_id, finding.normalized)) == "accepted"
            else "proposed"
        )
        suggestion, _ = ObservableSuggestion.objects.update_or_create(
            **lookup,
            defaults={
                "raw_data": finding.raw,
                "status": status,
                "source_paths": finding.source_paths,
                "reason": reason,
                "filtered_out": not keep,
                "filter_reason": filter_reason or "",
            },
        )
        suggestions.append(suggestion)

    return suggestions


class AcceptSuggestionError(Exception):
    """Raised when a suggestion cannot be accepted (e.g. unknown observable type)."""


@transaction.atomic
def accept_suggestion(
    suggestion: ObservableSuggestion,
    *,
    actor: User | None = None,
) -> Observable:
    """Accept a suggestion: link the observable to the case, fire triggers, record provenance.

    This is the single implementation of the accept semantics, shared by the JSON API
    (`observables/views.py`) and the server-rendered UI (`ui/views.py`) so the two cannot drift.

    The ordering matters and is the whole point of this helper:

    1. Resolve (get-or-create) the global `Observable` row for the suggestion's type and value.
    2. Create the `CaseObservable` link.
    3. **Dispatch the automation trigger exactly once** — only when the observable row was newly
       created or the case link was newly created. An observable is shared across cases, so
       re-accepting a value that is already linked must not re-fire the playbook.
    4. Persist `AlertObservable` provenance when the suggestion came from an alert.
    5. Mark the suggestion accepted and stamp the reviewer.

    Note that `add_observable_with_status()` already dispatches on create/link, so this helper
    deliberately uses `resolve_observable()` directly rather than that wrapper — calling it and
    dispatching again would fire every trigger twice.

    Raises:
        AcceptSuggestionError: if the suggestion's data type is not in the observable vocabulary.
    """
    from alerts.models import AlertObservable
    from automation.registry import dispatch_observable_linked
    from cases.models import CaseObservable

    resolved = resolve_observable(
        suggestion.data_type.name,
        suggestion.raw_data,
        owner_org=suggestion.owner_org,
        defaults={"data": suggestion.raw_data},
    )
    if resolved is None:
        raise AcceptSuggestionError(f"Unknown dataType {suggestion.data_type.name!r}")
    observable, created = resolved

    link_created = False
    if suggestion.case_id:
        _, link_created = CaseObservable.objects.get_or_create(
            case_id=suggestion.case_id,
            observable=observable,
            defaults={"added_by": actor},
        )

    # Dispatch exactly once: on a new observable or a new case link, never on a re-accept.
    if (created or link_created) and suggestion.case_id:
        dispatch_observable_linked(observable, suggestion.case_id)

    if suggestion.source_alert_id:
        AlertObservable.objects.get_or_create(
            alert=suggestion.source_alert,
            observable=observable,
            defaults={"added_by": actor},
        )

    suggestion.observable = observable
    suggestion.status = "accepted"
    suggestion.reviewed_by = actor
    suggestion.reviewed_at = timezone.now()
    suggestion.save(
        update_fields=["observable", "status", "reviewed_by", "reviewed_at", "updated_at"]
    )
    return observable


def bulk_review_suggestions(
    suggestions: Iterable[ObservableSuggestion],
    *,
    target_status: str,
    actor: User | None = None,
) -> list[tuple[ObservableSuggestion, str | None]]:
    """Apply a review status to many suggestions, collecting per-item errors.

    Shared by the JSON API bulk endpoints and the server-rendered UI bulk actions so the two
    paths cannot drift. Unlike the API, this takes an **already-scoped** queryset: the caller is
    responsible for org/case scoping before calling.

    Args:
        suggestions: The already-scoped suggestions to act on.
        target_status: One of `accepted`, `rejected`, `ignored`.
        actor: The reviewing user, stamped on each suggestion.

    Returns:
        One `(suggestion, error)` pair per suggestion; `error` is `None` on success. Suggestions
        already in `target_status` are a no-op success (idempotent).
    """
    results: list[tuple[ObservableSuggestion, str | None]] = []
    for suggestion in suggestions:
        if suggestion.status == target_status:
            # Idempotent: already in the target status.
            results.append((suggestion, None))
            continue

        if target_status == "accepted":
            if suggestion.status in ("rejected", "ignored"):
                results.append((suggestion, "Cannot accept a rejected or ignored suggestion"))
                continue
            try:
                accept_suggestion(suggestion, actor=actor)
            except AcceptSuggestionError as exc:
                results.append((suggestion, str(exc)))
                continue
        else:
            if suggestion.status == "accepted":
                results.append((suggestion, f"Cannot {target_status} an accepted suggestion"))
                continue
            suggestion.status = target_status
            suggestion.reviewed_by = actor
            suggestion.reviewed_at = timezone.now()
            suggestion.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        results.append((suggestion, None))
    return results
