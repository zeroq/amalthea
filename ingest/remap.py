"""Re-apply one feed's mapping to the alerts it already ingested (PLAN-2026-10-16 §8 P7).

The receiver applies a mapping once, at ingest, and never looks at the alert again. That is fine until
the *mapping* was wrong: every alert the feed accepted before the fix keeps the values the broken rules
produced, and there was no way to repair them short of re-sending the payloads.

This module is that repair, and it is deliberately the third layer of the mapping stack:

* `ingest/mapping.py` — pure functions over a payload (no Django, no DB);
* `ingest/pipeline.py` — the write path for a **new** alert;
* `ingest/remap.py` (here) — the write path for **existing** alerts.

The candidate set is the whole safety story, so it is one function with no parameter that widens it:

    ingestion_source = this feed   AND   case is NULL   AND   stage in (New, InProgress)

* `ingestion_source`, not the wire `source` string: the FK is our record of which configured feed
  accepted the payload (`Alert`'s docstring: a mismatch is data, not a bug), so two feeds that both send
  `source: "crowdstrike"` cannot be confused. An alert that arrived over the TheHive-compatible API has
  no feed and is never a candidate.
* **unlinked**: a case file is human-authored evidence. Rewriting an alert *inside* a case changes what an
  investigator has already read, and the case ledger is append-only — so an escalated alert keeps the
  values it was escalated with (the alert row is preserved by design, `alerts/escalation.py`).
* **open**: `New`/`Triaged` (`stage` `New`/`InProgress`) are the stages in which a wrong severity still
  misleads triage. `Dismissed` is `stage="Closed"` and `Imported` is `stage="Imported"`; both are terminal
  decisions rather than mapping errors, so both fall out of the set.

Two passes over that set live here, and they share one evaluation (`_evaluate`) so they cannot disagree:
`remap_alerts` writes, and `preview_alerts` counts what writing *would* do — the confirmation dialog's
numbers (P7.1). The preview exists because "0 updated, 19 unchanged" is the same sentence whether the
alerts were already correct or your new rule was rejected, and an operator cannot tell those apart
without a per-target account of what the mapping answered.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from django.db.models import Count, QuerySet

from alerts.models import Alert
from ingest.mapping import UNRESOLVED_REASONS, AlertDraft, apply_mapping
from ingest.models import IngestionSource

#: The fields a re-map may write, in the order every message and audit row lists them.
#:
#: Everything else is out of scope on purpose. `type` and `source_ref` are the dedupe key together with
#: `source`/`owner_org` (`uniq_alert_source_type_ref`), so rewriting them can collide with a stored row —
#: and they are the replay key, so rewriting them moves the identity of an alert that already exists
#: (**MAP-9**). `correlation_key` is not a v2 field at all (plan §11 Q8). And `status`, `case`, `tags`,
#: `assignee`, `flag`, `follow`, `tlp`, `pap` are triage decisions, not mapping output.
REMAP_FIELDS: Final[tuple[str, ...]] = ("title", "description", "summary", "severity", "date")

#: The stages that count as *open*, next to the predicate that uses them so the two cannot drift.
OPEN_ALERT_STAGES: Final[tuple[str, ...]] = ("New", "InProgress")

#: Ingest clamps the title to 200 characters (`ingest/views.py`); the re-map mirrors that, so an alert
#: repaired today equals one ingested today under the same rules.
TITLE_LIMIT: Final[int] = 200

#: How many alerts one request may re-map. The cap exists because this runs inside the request/response
#: cycle; past a few thousand rows the honest answer is a background task, and a "save & apply" that
#: silently drops its apply half is worse than a refusal (the view disables its confirm control instead).
MAX_REMAP_ALERTS: Final[int] = 5000

#: How many stored alerts the source page's health card reads before it says it stopped early (P5).
#: Deliberately smaller than `MAX_REMAP_ALERTS`: that one bounds a *write* the operator asked for, while
#: this bounds a page render nobody asked for. The card is a prompt to look, not the pass itself — past
#: the limit it names the number it read and points at the dialog, which previews a fresh pass anyway.
HEALTH_SCAN_LIMIT: Final[int] = 2000


@dataclass(frozen=True)
class RemapSummary:
    """What one feed's re-map *would* touch — computed before anything is written."""

    candidates: int
    by_status: dict[str, int]

    @property
    def over_cap(self) -> bool:
        return self.candidates > MAX_REMAP_ALERTS


@dataclass(frozen=True)
class RemapResult:
    """What it *did* touch. `candidates == updated + unchanged` unless the cap truncated the pass."""

    candidates: int
    updated: int
    unchanged: int
    #: Rows whose **fields** did not change but whose `mapping` record did, and were rewritten for it.
    #: A subset of `unchanged`: the alert kept its values, but the page no longer describes rules the
    #: feed has stopped storing (**MAP-12**).
    refreshed: int
    changed_fields: dict[str, int]
    #: `target -> reason -> alerts`, for the rules that produced no value at all. This is the answer to
    #: "I fixed the mapping and nothing changed": a target whose rule cannot read the payload changes
    #: nothing and falls back to the default, which is *identical* to the stored value and therefore
    #: invisible in `updated`/`unchanged` alone (`ingest.mapping.UNRESOLVED_REASONS`).
    unresolved: dict[str, dict[str, int]]

    @property
    def touched(self) -> list[str]:
        """The fields the pass wrote, in `REMAP_FIELDS` order — what the flash message lists.

        A property rather than a second field: the list and `changed_fields` are the same fact, and the
        earlier "which fields did it write?" text must not be able to disagree with the per-field counts.
        """
        return [target for target in REMAP_FIELDS if self.changed_fields.get(target)]


@dataclass(frozen=True)
class RemapPreview:
    """What a pass *would* do — the confirmation dialog's numbers, computed by reading (P7.1, AC24).

    Built by `preview_alerts`, which shares `_evaluate` with `remap_alerts`: the dialog's promise and the
    writer's behaviour cannot drift, because the same function decides both.

    It wraps a `RemapSummary` rather than replacing it, so the shape the page already reads
    (`apply_pending.candidates`, `.by_status`, `.over_cap`) is the same object either way — and the cap
    guard keeps using the cheap aggregate count.
    """

    summary: RemapSummary
    #: `target -> alerts` whose stored value disagrees with what the new mapping produces.
    changes: dict[str, int]
    #: `target -> reason -> alerts` that produced no value, i.e. nothing will change for them. The column
    #: that turns "0 updated" from a shrug into a diagnosis.
    unresolved: dict[str, dict[str, int]]

    @property
    def candidates(self) -> int:
        return self.summary.candidates

    @property
    def by_status(self) -> dict[str, int]:
        return self.summary.by_status

    @property
    def over_cap(self) -> bool:
        return self.summary.over_cap


@dataclass(frozen=True)
class MappingHealth:
    """What the candidate alerts' **stored** mapping records say, for the source page (P5).

    Deliberately not a re-computation. `RemapPreview` asks "what would the rules produce *now*" and may
    only be answered by running them; this asks "what is recorded on the rows as last written", which is
    the feed-level view of the same record the alert page renders — so the card and the row it links to
    cannot disagree. A rule fixed but not yet re-applied therefore still shows here, which is the point:
    this is the evidence that a pass is worth running.
    """

    #: Unlinked open alerts from this feed — the re-map's candidate set, not all of its alerts.
    total: int
    #: How many of them the card actually read (`HEALTH_SCAN_LIMIT`).
    scanned: int
    #: Alerts whose stored record carries at least one unresolved field.
    affected: int
    #: `target -> reason -> alerts`, the same shape the re-map reports.
    unresolved: dict[str, dict[str, int]]

    @property
    def complete(self) -> bool:
        return self.scanned >= self.total

    @property
    def clean(self) -> bool:
        return self.affected == 0


def candidate_alerts(source: IngestionSource) -> QuerySet[Alert]:
    """The alerts one feed's re-map may touch. The only place that predicate is written."""
    return Alert.objects.filter(
        ingestion_source=source,
        case__isnull=True,
        status__stage__in=OPEN_ALERT_STAGES,
    ).order_by("created_at")


def summarise_candidates(source: IngestionSource) -> RemapSummary:
    """Count the candidates, per status value, for the confirmation dialog (no writes)."""
    rows = (
        candidate_alerts(source)
        .values("status__value")
        .annotate(count=Count("id"))
        .order_by("-count", "status__value")
    )
    by_status = {row["status__value"]: row["count"] for row in rows}
    return RemapSummary(candidates=sum(by_status.values()), by_status=by_status)


def summarise_mapping_records(
    records: Iterable[Mapping[str, Any] | None],
) -> tuple[int, dict[str, dict[str, int]]]:
    """Count the unresolved fields in stored `ingestion_warnings` dicts (pure, so it can be tested).

    Returns `(alerts with at least one unresolved field, target -> reason -> alerts)`. Only the
    structured `"mapping"` list is read: `"warnings"` is the alert's append-only event log, so an
    ingest-time `severity_defaulted` line would be counted as a live defect forever once a rule is fixed
    (MAP-2b). Each alert counts once per `(target, reason)`, so a payload warning twice about the same
    field does not inflate the count.
    """
    affected = 0
    unresolved: dict[str, dict[str, int]] = {}
    for stored in records:
        seen: set[tuple[str, str]] = set()
        for warning in (stored or {}).get("mapping") or []:
            if not isinstance(warning, Mapping):  # defensive: the column is operator-visible JSONB
                continue
            target = str(warning.get("field") or "")
            reason = str(warning.get("reason") or "")
            if target and reason in UNRESOLVED_REASONS:
                seen.add((target, reason))
        if not seen:
            continue
        affected += 1
        for target, reason in sorted(seen):
            by_reason = unresolved.setdefault(target, {})
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return affected, unresolved


def mapping_health(source: IngestionSource, *, limit: int = HEALTH_SCAN_LIMIT) -> MappingHealth:
    """Read the candidate alerts' stored mapping records for the source page's health card (P5).

    Read-only and bounded: `candidate_alerts` is the re-map's own predicate, so the card counts exactly
    the rows a pass would repair and cannot promise a fix the button would refuse (MAP-9: a linked or
    closed alert is never re-mapped, and is therefore never counted here).
    """
    candidates = candidate_alerts(source)
    total = candidates.count()
    rows = candidates.values_list("ingestion_warnings", flat=True)[:limit]
    affected, unresolved = summarise_mapping_records(rows)
    return MappingHealth(
        total=total,
        scanned=min(total, limit),
        affected=affected,
        unresolved=unresolved,
    )


def remap_alerts(
    source: IngestionSource,
    config: dict[str, Any] | None,
    *,
    limit: int = MAX_REMAP_ALERTS,
) -> RemapResult:
    """Apply `config` to `source`'s unlinked open alerts, writing only the rows that change.

    `config` must already be validated (`ingest.mapping.validate_config`, via `ingest.editor`), and the
    caller owns the transaction: the writes are meant to commit together with the mapping save that
    produced them, so the stored rules and the stored alerts cannot disagree.

    Three things are counted, and the third is the one the operator needs when this appears to do
    nothing: a row can be written (a field changed), left alone with its `mapping` record refreshed, or
    left alone entirely — and separately, a rule can have produced *no* value at all, which shows up as
    "unchanged" no matter how wrong the rule is.
    """
    candidates = 0
    updated = 0
    refreshed = 0
    changed_fields: dict[str, int] = {}
    unresolved: dict[str, dict[str, int]] = {}
    for alert in candidate_alerts(source)[:limit].iterator(chunk_size=200):
        candidates += 1
        draft, wanted, changed, issues = _evaluate(alert, source, config)
        for target, reason in issues:
            by_reason = unresolved.setdefault(target, {})
            by_reason[reason] = by_reason.get(reason, 0) + 1
        if changed:
            _write_alert(alert, source, draft, wanted, changed)
            updated += 1
            for target in changed:
                changed_fields[target] = changed_fields.get(target, 0) + 1
        elif _refresh_record(alert, source, draft):
            refreshed += 1
    return RemapResult(
        candidates=candidates,
        updated=updated,
        unchanged=candidates - updated,
        refreshed=refreshed,
        changed_fields=changed_fields,
        unresolved=unresolved,
    )


def preview_alerts(
    source: IngestionSource,
    config: dict[str, Any] | None,
    *,
    limit: int = MAX_REMAP_ALERTS,
) -> RemapPreview:
    """Dry-run `config` over the candidate set. **Reads only** — the caller writes nothing (AC24).

    This is the confirmation dialog's half of the operation, and it exists because the numbers that
    describe the *set* ("19 unlinked open alerts") say nothing about the *rules*: an operator who saves a
    severity rule that cannot read the payload sees "0 updated, 19 unchanged", which is equally the
    outcome of a rule that was already correct. Per target, this reports what would change and where the
    rule produced nothing at all, before anything is committed.

    `limit` mirrors `remap_alerts`: past the cap the dialog refuses the write anyway, so a preview of the
    first cap-many rows is enough to explain it, and a 20 000-alert feed cannot make the page slow.
    """
    summary = summarise_candidates(source)
    changes: dict[str, int] = {}
    unresolved: dict[str, dict[str, int]] = {}
    for alert in candidate_alerts(source)[:limit].iterator(chunk_size=200):
        _draft, _wanted, changed, issues = _evaluate(alert, source, config)
        for target in changed:
            changes[target] = changes.get(target, 0) + 1
        for target, reason in issues:
            by_reason = unresolved.setdefault(target, {})
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return RemapPreview(summary=summary, changes=changes, unresolved=unresolved)


def _evaluate(
    alert: Alert,
    source: IngestionSource,
    config: dict[str, Any] | None,
) -> tuple[AlertDraft, dict[str, Any], list[str], list[tuple[str, str]]]:
    """One alert against one config: the draft, what it wants written, what differs, what it refused.

    The single decision point for both the writer (`remap_alerts`) and the preview (`preview_alerts`),
    so the dialog cannot promise something the pass does not do.
    """
    draft = apply_mapping(config, alert.raw_payload, default_severity=source.default_severity)
    wanted = _target_values(draft)
    changed = [target for target, value in wanted.items() if getattr(alert, target) != value]
    unresolved = [
        (warning.target, warning.reason)
        for warning in draft.field_warnings
        if warning.reason in UNRESOLVED_REASONS
    ]
    return draft, wanted, changed, unresolved


def _target_values(draft: AlertDraft) -> dict[str, Any]:
    """What the new mapping says each written field should be.

    `severity` is always written: the engine never leaves it unanswered (its absence falls back to
    `default_severity`, which is the feed's own declared floor) and severity is not content a human typed.

    The text fields are written **only when the new mapping answers them.** Ingest fills an unanswered
    title with the literal `"Alert"`, which is right for an alert that has no title yet and destructive
    for a stored one: clearing a rule in the editor would retitle every unlinked open alert of the feed.
    Leaving the field alone keeps the operation non-destructive; the modal body says so.
    """
    values: dict[str, Any] = {"severity": draft.severity}
    if draft.title is not None:
        values["title"] = draft.title[:TITLE_LIMIT]
    if draft.description is not None:
        values["description"] = draft.description
    if draft.summary is not None:
        values["summary"] = draft.summary
    if draft.date is not None:
        values["date"] = draft.date
    return values


def _refresh_record(
    alert: Alert,
    source: IngestionSource,
    draft: AlertDraft,
) -> bool:
    """Rewrite nothing but the alert's `mapping` record, when that record is out of date. Returns "wrote".

    The case this exists for: the feed's rules changed and the new ones produce *exactly* the values the
    alert already has. Nothing about the alert is wrong — but its `mapping` entry still names the rule
    paths that failed under the **previous** config, so the page goes on describing rules the feed has
    stopped storing. Replacing that entry is the whole write; the log is not touched, because nothing
    happened to the alert (**MAP-12**).
    """
    fresh = _refreshed_warnings(source, alert.ingestion_warnings, draft, [], log=False)
    if _record_is_current(alert.ingestion_warnings, fresh):
        return False
    alert.ingestion_warnings = fresh
    # `updated_at` is `auto_now` and an `update_fields` UPDATE writes only the columns it names.
    alert.save(update_fields=["ingestion_warnings", "updated_at"])
    return True


def _record_is_current(previous: dict[str, Any] | None, fresh: dict[str, Any]) -> bool:
    """Whether the stored warnings already say what `fresh` says — with absent == empty.

    An alert ingested by a feed with no rules carries `{}`, and the first re-map computes
    `{"mapping": [], "warnings": []}`. Treating those as different would make a pass that changed
    nothing write every one of the feed's alerts, which is the opposite of the point.
    """
    stored = previous or {}
    # Named, because `stored` is JSON: each comparison is asserted `bool` before it is combined, so a
    # reflexively-typed `and` cannot leak `Any` into the return.
    same_mapping: bool = list(stored.get("mapping") or []) == list(fresh["mapping"] or [])
    same_log: bool = list(stored.get("warnings") or []) == list(fresh["warnings"] or [])
    return same_mapping and same_log


def _write_alert(
    alert: Alert,
    source: IngestionSource,
    draft: AlertDraft,
    wanted: dict[str, Any],
    changed: list[str],
) -> None:
    """Write one alert's changed fields and refresh its warnings record.

    Takes the draft the caller already evaluated rather than re-running `apply_mapping`: the decision and
    the write are the same evaluation, so a row that was counted as "would change" cannot be written from
    a second, different reading of its payload.
    """
    for target in changed:
        setattr(alert, target, wanted[target])
    alert.ingestion_warnings = _refreshed_warnings(
        source, alert.ingestion_warnings, draft, changed, log=True
    )
    # `updated_at` is `auto_now`, and an UPDATE writes only the columns it names — so the timestamp has
    # to be named explicitly (the same reason `_save_source_mapping` names it).
    alert.save(update_fields=[*changed, "ingestion_warnings", "updated_at"])


def _refreshed_warnings(
    source: IngestionSource,
    previous: dict[str, Any] | None,
    draft: AlertDraft,
    changed: list[str],
    *,
    log: bool,
) -> dict[str, Any]:
    """The alert's warnings after a re-map: structure replaced, and — when something changed — history appended.

    Two different things live under this key and they must not be handled the same way:

    * `mapping` **describes the current fields**, so it is *replaced*. A stale entry contradicts the row
      it is attached to — the alert page would keep saying "severity_defaulted … defaulted to 2" under a
      severity of 4, which is the lie this feature exists to end.
    * `warnings` is the alert's **event log** (a synthesised `source_ref`, `status_created`, the
      ingest-time severity fallback), so the re-map *appends* one line and rewrites nothing. That is also
      why the line names fields and never values: a description does not belong in a log.

    `log` says whether this pass changed a field. When it did not, the record is still brought up to date
    (`mapping`) but the log stays silent: a log line is about what happened to the *alert*, and an
    operator re-applying an unchanged mapping fifty times should not leave fifty lines saying so.
    """
    warnings = dict(previous or {})
    warnings["mapping"] = [warning.as_dict() for warning in draft.field_warnings]
    lines = [line for line in (warnings.get("warnings") or []) if isinstance(line, str)]
    if log:
        lines.append(
            f"remapped: re-applied the mapping of feed {source.slug!r} ({', '.join(changed)})"
        )
    warnings["warnings"] = lines
    return warnings
