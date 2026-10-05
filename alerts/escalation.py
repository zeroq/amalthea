"""Alert → case escalation (AGENTS.md Module B, Phase 5).

Two verbs, and the difference is the whole point:

* **`merge_alert_into_case`** attaches an already-triaged alert to an existing case. The alert
  keeps its own row, `raw_payload` and `source_ref`; only `case_id` and `status` change. This is
  what an analyst does when a new alert turns out to be the same incident.
* **`import_alert_to_case`** promotes an alert into a *new* case, copying its title, severity and
  TLP/PAP onto the case so the case file is readable without opening the alert. The alert is
  marked `Imported`, which is why `core.enums.ALERT_STAGES` carries that stage.

Both are the same transaction: the link, the status transition, the observable extraction and the
timeline entry either all land or none do. A case that exists without the evidence that created it
is worse than no case, because nothing in the ledger records that the evidence was expected.

Escalation is also where observables first appear for a case: `extract_into_case` runs over the
alert's human text **and** its `raw_payload` leaves, so an address buried three levels deep in a
SIEM event still becomes a typed artifact.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus, TimelineEvent
from observables.extractor import extract_into_case, json_leaves


def resolve_alert_status(value: str, *, warnings: list[str]) -> AlertStatus:
    """Get-or-create an `AlertStatus` by stage.

    ADR-002 §D11: an unknown status is auto-created at its own stage rather than rejected, and the
    substitution is recorded as a warning — the same tolerance `_resolve_default_status` applies on
    the ingest path.
    """
    status, created = AlertStatus.objects.get_or_create(
        value=value,
        defaults={"stage": value, "order": 0},
    )
    if created:
        warnings.append(f"alert_status_created: auto-created alert status {value!r}")
    return status


def resolve_case_status(value: str, *, warnings: list[str]) -> CaseStatus:
    status, created = CaseStatus.objects.get_or_create(
        value=value,
        defaults={"stage": value, "order": 1},
    )
    if created:
        warnings.append(f"case_status_created: auto-created case status {value!r}")
    return status


@transaction.atomic
def import_alert_to_case(
    alert: Alert,
    *,
    title: str | None = None,
    description: str | None = None,
    assignee: Any | None = None,
    actor: Any | None = None,
) -> tuple[Case, list[str]]:
    """Promote `alert` into a new `Case`, extract its observables, and record the timeline.

    Returns `(case, warnings)`. Calling it twice for the same alert is refused by the caller, not
    here: `alert.case` being non-null is what means "already escalated".
    """
    if alert.case_id is not None:
        raise ValueError(
            f"alert {alert.id} is already attached to case {alert.case_id}; "
            "use merge_alert_into_case or detach it first"
        )

    warnings: list[str] = []
    status = resolve_case_status("InProgress", warnings=warnings)
    body = description if description is not None else (alert.summary or alert.description or "")

    case = Case.objects.create(
        title=(title or alert.title)[:500],
        description=body,
        severity=alert.severity,
        status=status,
        assignee=assignee,
        tlp=alert.tlp,
        pap=alert.pap,
        start_date=alert.date or timezone.now(),
        ingestion_warnings={"warnings": warnings},
    )

    alert.case = case
    alert.status = resolve_alert_status("Imported", warnings=warnings)
    alert.save(update_fields=["case", "status", "updated_at"])

    extract_into_case(
        case, case.title, case.description, alert.title, json_leaves(alert.raw_payload)
    )

    TimelineEvent.objects.create(
        case=case,
        date=timezone.now(),
        title=f"Alert imported: {alert.title}",
        description=f"Source {alert.source}, type {alert.type}, ref {alert.source_ref}",
        kind="alert-imported",
        actor=actor,
        metadata={"alert_id": str(alert.id)},
    )

    if warnings:
        case.ingestion_warnings = {"warnings": warnings}
        case.save(update_fields=["ingestion_warnings", "updated_at"])

    return case, warnings


@transaction.atomic
def merge_alert_into_case(
    alert: Alert,
    case: Case,
    *,
    actor: Any | None = None,
) -> TimelineEvent:
    """Attach `alert` to an existing `case`, extract observables, and write the ledger entry.

    Merging into the case the alert already belongs to is a no-op that still extracts and still
    writes the timeline entry: a retry after a partial failure should converge on "linked, extracted,
    recorded" rather than raising and leaving the analyst to guess.
    """
    warnings: list[str] = []
    already = alert.case_id == case.pk

    if not already:
        alert.case = case
        alert.status = resolve_alert_status("Imported", warnings=warnings)
        alert.save(update_fields=["case", "status", "updated_at"])

    extract_into_case(
        case, alert.title, alert.summary, alert.description, json_leaves(alert.raw_payload)
    )

    return TimelineEvent.objects.create(
        case=case,
        date=timezone.now(),
        title=f"Alert merged: {alert.title}",
        description=f"Source {alert.source}, type {alert.type}, ref {alert.source_ref}",
        kind="alert-merged",
        actor=actor,
        metadata={"alert_id": str(alert.id), "already_linked": already, "warnings": warnings},
    )


@transaction.atomic
def link_case_from_identifier(identifier: str) -> Case:
    """Resolve a case by UUID **or** by its human `number` (plan §Phase 5 `{idOrName}`).

    Both spellings are in the plan because TheHive's own UI accepts either, and an analyst pasting
    "42" from a ticket should not have to look up a UUID.
    """
    try:
        return Case.objects.get(pk=identifier)
    except (Case.DoesNotExist, ValidationError, ValueError, TypeError):
        # `ValidationError`, not `ValueError`: a non-UUID against a UUID pk raises Django's own
        # `ValidationError("… is not a valid UUID")`, which is neither of the other two. Catching
        # only `DoesNotExist` turned a plain case *number* into a 500.
        pass
    if identifier.isdigit():
        found = Case.objects.filter(number=int(identifier)).first()
        if found is not None:
            return found
    raise Case.DoesNotExist(f"no case matches {identifier!r} as a UUID or a case number")


@transaction.atomic
def link_alert_from_identifier(identifier: str) -> Alert:
    """Resolve an alert by UUID, or by its `source_ref` within its own source when unambiguous."""
    try:
        return Alert.objects.get(pk=identifier)
    except (Alert.DoesNotExist, ValidationError, ValueError, TypeError):
        pass  # Not a UUID — fall through to the `source_ref` lookup.
    matches = list(Alert.objects.filter(source_ref=identifier)[:2])
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise Alert.MultipleObjectsReturned(
            f"{identifier!r} is a source_ref shared by {len(matches)} alerts; use the alert UUID"
        )
    raise Alert.DoesNotExist(f"no alert matches {identifier!r} as a UUID or a source_ref")
