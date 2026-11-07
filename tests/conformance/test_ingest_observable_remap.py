"""P3 — a re-map re-extracts observables for every candidate, additively."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alerts.models import Alert, AlertObservable, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation
from ingest.models import IngestionSource
from ingest.remap import preview_alerts, remap_alerts

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "title": "Mapped title",
    "actor": {"email": "analyst@example.com"},
    "source": {"ip": "198.51.100.7"},
}

OBSERVABLE_CONFIG = {
    "version": 2,
    "fields": {},
    "observables": [
        {"type": "mail", "paths": ["$.actor.email"]},
        {"type": "ip", "paths": ["$.source.ip"]},
    ],
}


def _source(organisation: Organisation) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=organisation,
        slug="remap-observables",
        name="Remap observables",
        mapping_config={},
    )


def _alert(
    source: IngestionSource,
    *,
    ref: str,
    case: Case | None = None,
    severity: int = 2,
    warnings: dict | None = None,
) -> Alert:
    status, _ = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New"})
    return Alert.objects.create(
        title="Stored title",
        description="",
        summary="",
        severity=severity,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        type="phishing",
        source="wire-name",
        source_ref=ref,
        status=status,
        raw_payload=dict(PAYLOAD),
        ingestion_source=source,
        case=case,
        ingestion_warnings={} if warnings is None else warnings,
        owner_org=source.organisation,
    )


def test_remap_adds_observables_and_reports_the_count(default_org: Organisation) -> None:
    source = _source(default_org)
    alert = _alert(source, ref="evt-1")

    result = remap_alerts(source, OBSERVABLE_CONFIG)

    assert result.candidates == 1
    assert result.observables_added == 2
    assert AlertObservable.objects.filter(alert=alert).count() == 2


def test_remap_is_idempotent_for_observables(default_org: Organisation) -> None:
    source = _source(default_org)
    alert = _alert(source, ref="evt-1")
    remap_alerts(source, OBSERVABLE_CONFIG)

    result = remap_alerts(source, OBSERVABLE_CONFIG)

    assert result.observables_added == 0
    assert AlertObservable.objects.filter(alert=alert).count() == 2


def test_remap_adds_observables_even_when_no_field_changes(default_org: Organisation) -> None:
    source = _source(default_org)
    alert = _alert(source, ref="evt-1", severity=source.default_severity)
    alert.title = "Mapped title"
    alert.save(update_fields=["title"])

    result = remap_alerts(source, OBSERVABLE_CONFIG)

    assert result.updated == 0
    assert result.observables_added == 2
    assert AlertObservable.objects.filter(alert=alert).count() == 2


def test_remap_skips_an_alert_already_escalated_to_a_case(default_org: Organisation) -> None:
    source = _source(default_org)
    case_status, _ = CaseStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    case = Case.objects.create(title="Case", status=case_status, owner_org=default_org)
    alert = _alert(source, ref="evt-1", case=case)

    result = remap_alerts(source, OBSERVABLE_CONFIG)

    assert result.candidates == 0
    assert result.observables_added == 0
    assert AlertObservable.objects.filter(alert=alert).count() == 0


def test_preview_does_not_write_observables(default_org: Organisation) -> None:
    source = _source(default_org)
    alert = _alert(source, ref="evt-1")

    preview = preview_alerts(source, OBSERVABLE_CONFIG)

    assert preview.candidates == 1
    assert AlertObservable.objects.filter(alert=alert).count() == 0
