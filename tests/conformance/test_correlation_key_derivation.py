"""Tests for correlation_key derivation (H3-4 / M-1)."""

from __future__ import annotations

import pytest

from alerts.models import Alert
from ingest.models import IngestionSource
from ingest.pipeline import store_ingested_alert


@pytest.mark.django_db
def test_correlation_key_derived_from_mapping_config_nested():
    source = IngestionSource.objects.create(
        slug="test-source",
        name="Test Source",
        mapping_config={"correlation_key": "$.event.correlationId"},
    )
    payload = {"event": {"correlationId": "corr-12345"}}

    result = store_ingested_alert(
        wire_source="test",
        alert_type="phish",
        payload=payload,
        title="Test Alert",
        ingestion_source=source,
    )

    assert result.alert.correlation_key == "corr-12345"
    assert Alert.objects.filter(correlation_key="corr-12345").exists()


@pytest.mark.django_db
def test_correlation_key_derived_from_mapping_config_flat():
    source = IngestionSource.objects.create(
        slug="flat-source",
        name="Flat Source",
        mapping_config={"correlation_key": "$.correlation_key"},
    )
    payload = {"correlation_key": "flat-corr-789"}

    result = store_ingested_alert(
        wire_source="flat",
        alert_type="malware",
        payload=payload,
        title="Flat Alert",
        ingestion_source=source,
    )

    assert result.alert.correlation_key == "flat-corr-789"


@pytest.mark.django_db
def test_correlation_key_falls_back_to_empty_when_no_mapping():
    source = IngestionSource.objects.create(
        slug="no-map",
        name="No Map",
        mapping_config={},
    )
    payload = {"correlation": "should-not-be-used"}

    result = store_ingested_alert(
        wire_source="nomap",
        alert_type="other",
        payload=payload,
        title="No Map Alert",
        ingestion_source=source,
    )

    assert result.alert.correlation_key == ""


@pytest.mark.django_db
def test_correlation_key_respects_explicit_parameter():
    source = IngestionSource.objects.create(
        slug="override",
        name="Override",
        mapping_config={"correlation_key": "$.event.id"},
    )
    payload = {"event": {"id": "mapped-123"}}

    result = store_ingested_alert(
        wire_source="override-src",
        alert_type="phish",
        payload=payload,
        title="Override Alert",
        ingestion_source=source,
        correlation_key="explicit-456",
    )

    # Explicit parameter takes precedence
    assert result.alert.correlation_key == "explicit-456"
