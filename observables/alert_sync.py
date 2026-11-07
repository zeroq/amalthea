"""Persistence for alert-mapped observables and their case copies.

Three operations share one rule: the database's uniqueness constraints are the idempotency boundary,
and an existing link is never edited or deleted by a re-run.

* `sync_alert_observables` turns one source's observable rules into `AlertObservable` rows.
* `copy_alert_observables_to_case` promotes those rows into `CaseObservable` links on escalation.
* `reconcile_suggestions_with_copied_observables` closes the review queue for artifacts that are now
  unambiguously on the case.

Dispatch lives at the copy boundary, not the alert boundary: an alert observable is evidence but not
yet a case event, while a new case link is exactly what `observable.created` means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from alerts.models import Alert, AlertObservable
from cases.models import Case, CaseObservable
from ingest.observable_rules import extract_observable_candidates
from observables.extractor import resolve_observable_for_type
from observables.models import ObservableSuggestion, ObservableType


@dataclass(frozen=True)
class AlertObservableSyncResult:
    """What one sync added or recognised. Warnings are ready for `ingestion_warnings`."""

    created: int = 0
    existing: int = 0
    skipped_unknown_type: int = 0
    truncated: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return self.created > 0


@dataclass(frozen=True)
class CaseObservableCopyResult:
    """The result of copying alert links into one case."""

    copied: int = 0
    already_present: int = 0

    @property
    def total(self) -> int:
        return self.copied + self.already_present


@transaction.atomic
def sync_alert_observables(
    alert: Alert,
    *,
    config: dict[str, Any] | None = None,
    actor: Any | None = None,
) -> AlertObservableSyncResult:
    """Extract and link the alert's mapped observables, append-only.

    `config` is only for the re-map path, which is applying a candidate mapping before it is stored.
    Ingest and replay deliberately pass nothing so the **stored** feed config is the only authority.
    """
    if config is None:
        source = alert.ingestion_source
        if source is None:
            return AlertObservableSyncResult()
        config = source.mapping_config

    extraction = extract_observable_candidates(config, alert.raw_payload)
    warning_lines = list(extraction.notes)
    if not extraction.candidates:
        return AlertObservableSyncResult(
            truncated=extraction.truncated,
            warnings=tuple(warning_lines),
        )

    vocabulary = {obs_type.name: obs_type for obs_type in ObservableType.objects.all()}
    unknown_types: set[str] = set()
    skipped_unknown = 0
    created = 0
    existing = 0

    for candidate in extraction.candidates:
        obs_type = vocabulary.get(candidate.type_name)
        if obs_type is None:
            unknown_types.add(candidate.type_name)
            skipped_unknown += 1
            continue
        observable, _observable_created = resolve_observable_for_type(
            obs_type,
            candidate.raw_value,
            owner_org=alert.owner_org,
        )
        _link, link_created = AlertObservable.objects.get_or_create(
            alert=alert,
            observable=observable,
            defaults={"added_by": actor},
        )
        if link_created:
            created += 1
        else:
            existing += 1

    if unknown_types:
        names = ", ".join(sorted(unknown_types))
        warning_lines.append(
            f"observable_type_unknown: skipped {skipped_unknown} value(s) for unknown "
            f"observable type(s): {names}"
        )

    return AlertObservableSyncResult(
        created=created,
        existing=existing,
        skipped_unknown_type=skipped_unknown,
        truncated=extraction.truncated,
        warnings=tuple(warning_lines),
    )


@transaction.atomic
def copy_alert_observables_to_case(
    alert: Alert,
    case: Case,
    *,
    actor: Any | None = None,
) -> CaseObservableCopyResult:
    """Copy every alert observable into `case`, dispatching once per new case link.

    A second copy converges: the unique constraint finds the existing `CaseObservable`, no row is
    created, and no event is emitted. Cross-org copies are refused rather than silently filtered,
    because reaching here with mismatched owners means an authorization layer above has failed.
    """
    if alert.owner_org_id != case.owner_org_id:
        raise PermissionDenied("Cross-org alert/case observable copy refused")

    links = AlertObservable.objects.filter(alert=alert).select_related("observable__data_type")
    copied = 0
    already_present = 0
    for link in links:
        _case_link, created = CaseObservable.objects.get_or_create(
            case=case,
            observable=link.observable,
            defaults={"added_by": actor if actor is not None else link.added_by},
        )
        if not created:
            already_present += 1
            continue
        copied += 1
        # Imported lazily so callers/tests can patch the registry symbol and so this service has no
        # import-time dependency on the automation dispatcher.
        from automation.registry import dispatch_observable_linked

        dispatch_observable_linked(link.observable, case.pk)

    return CaseObservableCopyResult(copied=copied, already_present=already_present)


@transaction.atomic
def reconcile_suggestions_with_copied_observables(
    alert: Alert,
    case: Case,
    *,
    actor: Any | None = None,
) -> int:
    """Accept still-proposed suggestions whose artifact is now on the case.

    Only `proposed` rows are touched. A reviewer's explicit `rejected`/`ignored` decision is not
    overridden just because a mapping also produced the value — the copy is evidence, not a mandate to
    rewrite an intentional triage choice.
    """
    links = AlertObservable.objects.filter(alert=alert).select_related("observable")
    copied_by_key = {
        (link.observable.data_type_id, link.observable.normalized_data): link.observable
        for link in links
    }
    if not copied_by_key:
        return 0

    suggestions = ObservableSuggestion.objects.filter(
        case=case,
        source_alert=alert,
        status="proposed",
    )
    reconciled = 0
    now = timezone.now()
    for suggestion in suggestions:
        observable = copied_by_key.get((suggestion.data_type_id, suggestion.normalized_data))
        if observable is None:
            continue
        if not CaseObservable.objects.filter(case=case, observable=observable).exists():
            continue
        suggestion.observable = observable
        suggestion.status = "accepted"
        suggestion.reviewed_by = actor
        suggestion.reviewed_at = now
        suggestion.save(
            update_fields=[
                "observable",
                "status",
                "reviewed_by",
                "reviewed_at",
                "updated_at",
            ]
        )
        reconciled += 1
    return reconciled
