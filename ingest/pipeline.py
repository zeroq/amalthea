"""The dedupe-and-persist slice of the ingest pipeline.

**Scope note.** REVIEW-2026-10-03 C3's fix requires the pipeline to always populate
`Alert.source_ref`, and its acceptance criteria (AC4.3 "an unconfigured source ingests
without error, never a 500", AC4.4 "replay is idempotent") are only provable against a
real persistence step. This module is that step — nothing else. Phase 4 still owns the
HTTP surface (`POST /api/v1/alerts/webhook/{source_id}`), secret verification, size caps,
throttling and the jsonpath-ng mapping engine; this function is what they will call.

Idempotency comes from the database, not from application bookkeeping: the lookup is on
the same triple as the unique constraint `(source, type, source_ref)`, and
`get_or_create` falls back to an INSERT guarded by that constraint, so two concurrent
replays of the same payload collapse to one row instead of raising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from alerts.models import Alert, AlertStatus
from ingest.models import IngestionSource

from .mapping import extract_correlation_key
from .references import resolve_source_ref

#: Severity an unmapped alert gets, with a warning (plan §14 Q1, provisional).
DEFAULT_SEVERITY = 2
DEFAULT_ALERT_STATUS = "New"


@dataclass(frozen=True)
class StoredAlert:
    """Outcome of one ingest attempt. `created=False` means "this was a replay"."""

    alert: Alert
    created: bool
    source_ref: str
    source_ref_synthesised: bool
    warnings: list[str] = field(default_factory=list)


def _resolve_default_status(warnings: list[str]) -> AlertStatus:
    """Get-or-create the `New` alert status, warning when it had to be created.

    ADR-002 §D11: an unknown status name is auto-created at the default stage rather
    than rejected. A source that has never been configured has no status mapping, so this
    is the path every unconfigured feed takes.
    """
    status, created = AlertStatus.objects.get_or_create(
        value=DEFAULT_ALERT_STATUS,
        defaults={"stage": "New", "order": 1},
    )
    if created:
        warnings.append(f"status_created: auto-created alert status {DEFAULT_ALERT_STATUS!r}")
    return status


def store_ingested_alert(
    *,
    wire_source: str,
    alert_type: str,
    payload: Any,
    title: str,
    mapped_source_ref: str | None = None,
    severity: int | None = None,
    date: datetime | None = None,
    correlation_key: str = "",
    ingestion_source: IngestionSource | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> StoredAlert:
    """Persist one alert, or recognise `payload` as a replay of one already stored.

    `wire_source` is the caller-supplied TheHive `source` value and is stored verbatim;
    `ingestion_source` is our own record of which configured feed accepted the payload.
    They are independent — see the `Alert` docstring.
    """
    warnings: list[str] = list(extra_warnings)

    if severity is None:
        severity = DEFAULT_SEVERITY
        warnings.append(f"severity_defaulted: no severity mapping; defaulted to {DEFAULT_SEVERITY}")

    source_ref, ref_warning = resolve_source_ref(mapped_source_ref, payload)
    if ref_warning:
        warnings.append(ref_warning)

    # Derive correlation_key from ingestion_source mapping if not explicitly provided
    if ingestion_source is not None and not correlation_key:
        try:
            derived = extract_correlation_key(ingestion_source.mapping_config, payload)
            if derived:
                correlation_key = derived
        except Exception:
            warnings.append(
                "correlation_key_derived_failed: failed to derive correlation_key from mapping_config"
            )

    status = _resolve_default_status(warnings)

    with transaction.atomic():
        alert, created = Alert.objects.get_or_create(
            source=wire_source,
            type=alert_type,
            source_ref=source_ref,
            defaults={
                "title": title,
                "severity": severity,
                "status": status,
                "date": date or timezone.now(),
                "raw_payload": payload,
                "correlation_key": correlation_key,
                "ingestion_source": ingestion_source,
                "ingestion_warnings": {"warnings": warnings},
            },
        )

    return StoredAlert(
        alert=alert,
        created=created,
        source_ref=source_ref,
        source_ref_synthesised=ref_warning is not None,
        warnings=warnings if created else [],
    )
