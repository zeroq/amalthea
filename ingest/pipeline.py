"""The dedupe-and-persist slice of the ingest pipeline.

**Scope note.** REVIEW-2026-10-03 C3's fix requires the pipeline to always populate
`Alert.source_ref`, and its acceptance criteria (AC4.3 "an unconfigured source ingests
without error, never a 500", AC4.4 "replay is idempotent") are only provable against a
real persistence step. This module is that step — nothing else. Phase 4 still owns the
HTTP surface (`POST /api/v1/alerts/webhook/`), secret verification, size caps,
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

from alerts.hashing import compute_payload_ssdeep
from alerts.models import Alert, AlertStatus
from identity.models import Organisation
from ingest.models import IngestionSource

from .mapping import FieldWarning, extract_correlation_key
from .references import resolve_source_ref

#: Severity an unmapped alert gets, with a warning (plan §14 Q1). Still the floor for callers that
#: pass no severity at all; the ingest path now threads `IngestionSource.default_severity` through
#: `default_severity=` instead, so a source that declares its own floor gets it (plan §5.3 step 3).
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
    description: str | None = None,
    summary: str | None = None,
    default_severity: int = DEFAULT_SEVERITY,
    correlation_key: str = "",
    ingestion_source: IngestionSource | None = None,
    owner_org: Organisation,
    extra_warnings: tuple[str, ...] = (),
    mapping_warnings: tuple[FieldWarning, ...] = (),
) -> StoredAlert:
    """Persist one alert, or recognise `payload` as a replay of one already stored.

    `wire_source` is the caller-supplied TheHive `source` value and is stored verbatim;
    `ingestion_source` is our own record of which configured feed accepted the payload.
    They are independent — see the `Alert` docstring.

    `owner_org` is the organisation derived from the `IngestionToken` (ADR-004 P2). Every
    ingested alert is stamped with it; there is no tokenless/ownerless ingestion path.
    """
    warnings: list[str] = list(extra_warnings)

    if severity is None:
        severity = default_severity
        warnings.append(f"severity_defaulted: no severity mapping; defaulted to {default_severity}")

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

    # Compute SSDEEP hash for fuzzy similarity (only on create, via defaults)
    payload_ssdeep = compute_payload_ssdeep(summary or "", payload)

    with transaction.atomic():
        alert, created = Alert.objects.get_or_create(
            owner_org=owner_org,
            source=wire_source,
            type=alert_type,
            source_ref=source_ref,
            defaults={
                "title": title,
                "description": description or "",
                "summary": summary or "",
                "severity": severity,
                "status": status,
                "date": date or timezone.now(),
                "raw_payload": payload,
                "correlation_key": correlation_key,
                "payload_ssdeep": payload_ssdeep,
                "ingestion_source": ingestion_source,
                "owner_org": owner_org,
                "ingestion_warnings": {
                    "warnings": warnings,
                    # The structured half of the same story: which rule paths failed, for the UI to
                    # render next to the value rather than in a paragraph (plan §5.2, P4).
                    "mapping": [warning.as_dict() for warning in mapping_warnings],
                },
            },
        )

    return StoredAlert(
        alert=alert,
        created=created,
        source_ref=source_ref,
        source_ref_synthesised=ref_warning is not None,
        warnings=warnings if created else [],
    )
