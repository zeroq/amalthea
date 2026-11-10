"""Observable-rule apply service for the source detail page.

Pure, read-only preview + append-only apply, mirroring the mapping-apply pattern
(PLAN-2026-10-16 §8 P7.1, PLAN-2026-11-09-source-observable-apply.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alerts.models import Alert, AlertObservable
from ingest.models import IngestionSource
from ingest.observable_rules import extract_observable_candidates
from ingest.remap import MAX_REMAP_ALERTS, candidate_alerts, summarise_candidates
from observables.alert_sync import candidate_link_key, sync_alert_observables
from observables.models import ObservableType


@dataclass(frozen=True)
class ObservableTypePreview:
    """Per-type counts for one observable type extracted from the candidate set."""

    found: int
    """Total candidates of this type found across all alerts."""

    new_links: int
    """How many of those would be new `AlertObservable` links (not already linked)."""

    existing_links: int
    """How many are already linked to the alert (canonical type+value match)."""


@dataclass(frozen=True)
class ObservableApplyPreview:
    """Read-only preview of applying observable rules to unlinked open alerts."""

    candidates: int
    """Total alerts in the candidate set, even when scanning is capped."""

    by_type: dict[str, ObservableTypePreview]
    """Per-type breakdown: type_name -> ObservableTypePreview, ordered by type name."""

    alerts_with_values: int
    """Alerts that yielded at least one known-type candidate value."""

    unknown_types: tuple[str, ...]
    """Observable type names not in the vocabulary, in sorted order."""

    truncated_alerts: int
    """Alerts where extraction was truncated (rule/candidate/value cap)."""

    over_cap: bool
    """True if the full candidate set exceeds MAX_REMAP_ALERTS."""


@dataclass(frozen=True)
class ObservableApplyResult:
    """What an observable-only apply actually did."""

    candidates: int
    created: int
    existing: int
    alerts_with_values: int
    skipped_unknown_type: int
    truncated_alerts: int
    unknown_types: tuple[str, ...]
    by_type: dict[str, dict[str, int]] = field(default_factory=dict)


def _existing_keys(alert: Alert) -> set[tuple[str, str]]:
    """The canonical `(type, normalized_data)` keys already linked to `alert`."""
    return set(
        AlertObservable.objects.filter(alert=alert).values_list(
            "observable__data_type__name", "observable__normalized_data"
        )
    )


def preview_observable_rules(
    source: IngestionSource,
    config: dict[str, Any],
    *,
    limit: int = MAX_REMAP_ALERTS,
) -> ObservableApplyPreview:
    """Dry-run observable rules over the candidate set. **Reads only** — no DB writes.

    Same candidate predicate as the mapping re-map: same feed, case is NULL,
    stage in (New, InProgress). Bounded by *limit* for the scan; `candidates`
    still reports the full set so the dialog can refuse an over-cap write.
    """
    summary = summarise_candidates(source)
    vocabulary = {obs_type.name: obs_type for obs_type in ObservableType.objects.all()}
    by_type: dict[str, ObservableTypePreview] = {}
    alerts_with_values = 0
    unknown_types: set[str] = set()
    truncated_alerts = 0

    for alert in candidate_alerts(source)[:limit].iterator(chunk_size=200):
        extraction = extract_observable_candidates(config, alert.raw_payload)
        existing = _existing_keys(alert)
        seen: set[tuple[str, str]] = set()
        had_known = False

        for candidate in extraction.candidates:
            obs_type = vocabulary.get(candidate.type_name)
            if obs_type is None:
                unknown_types.add(candidate.type_name)
                continue
            key = candidate_link_key(obs_type, candidate.raw_value)
            if key in seen:
                # Two raw values that normalize to one artifact: one link, counted as already
                # present the second time, exactly as sync_alert_observables does.
                row = by_type.setdefault(candidate.type_name, ObservableTypePreview(0, 0, 0))
                by_type[candidate.type_name] = ObservableTypePreview(
                    row.found + 1, row.new_links, row.existing_links + 1
                )
                continue
            seen.add(key)
            had_known = True
            row = by_type.setdefault(candidate.type_name, ObservableTypePreview(0, 0, 0))
            if key in existing:
                by_type[candidate.type_name] = ObservableTypePreview(
                    row.found + 1, row.new_links, row.existing_links + 1
                )
            else:
                by_type[candidate.type_name] = ObservableTypePreview(
                    row.found + 1, row.new_links + 1, row.existing_links
                )

        if had_known:
            alerts_with_values += 1
        if extraction.truncated:
            truncated_alerts += 1

    return ObservableApplyPreview(
        candidates=summary.candidates,
        by_type=dict(sorted(by_type.items())),
        alerts_with_values=alerts_with_values,
        unknown_types=tuple(sorted(unknown_types)),
        truncated_alerts=truncated_alerts,
        over_cap=summary.over_cap,
    )


def apply_observable_rules(
    source: IngestionSource,
    config: dict[str, Any],
    *,
    actor: Any | None = None,
    limit: int = MAX_REMAP_ALERTS,
) -> ObservableApplyResult:
    """Apply observable rules to the candidate alerts.

    Calls `sync_alert_observables` per alert: append-only, idempotent, and deliberately not
    `remap_alerts`, so an observable-only pass never rewrites field values or `mapping` records.
    """
    candidates = 0
    created = 0
    existing = 0
    alerts_with_values = 0
    skipped_unknown = 0
    truncated_alerts = 0
    unknown_types: set[str] = set()
    by_type: dict[str, dict[str, int]] = {}

    for alert in candidate_alerts(source)[:limit].iterator(chunk_size=200):
        candidates += 1
        result = sync_alert_observables(alert, config=config, actor=actor)
        created += result.created
        existing += result.existing
        skipped_unknown += result.skipped_unknown_type
        truncated_alerts += int(result.truncated)
        unknown_types.update(result.unknown_types)
        if result.created or result.existing:
            alerts_with_values += 1
        for type_name, counts in result.observables_by_type.items():
            aggregate = by_type.setdefault(type_name, {"created": 0, "existing": 0})
            aggregate["created"] += counts["created"]
            aggregate["existing"] += counts["existing"]

    return ObservableApplyResult(
        candidates=candidates,
        created=created,
        existing=existing,
        alerts_with_values=alerts_with_values,
        skipped_unknown_type=skipped_unknown,
        truncated_alerts=truncated_alerts,
        unknown_types=tuple(sorted(unknown_types)),
        by_type=dict(sorted(by_type.items())),
    )
