"""P2 — ingest turns a feed's observable rules into append-only alert links."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertObservable
from identity.models import IngestionToken, Organisation
from ingest.models import IngestionSource
from ingest.pipeline import store_ingested_alert
from observables.models import Observable

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "title": "Suspicious sign-in",
    "severity": "critical",
    "actor": {"email": "alice@example.com"},
    "recipients": [{"address": "bob@example.com"}],
    "source": {"ip": "203.0.113.10"},
}

CONFIG = {
    "version": 2,
    "fields": {
        "title": {"paths": ["$.title"]},
        "severity": {"paths": ["$.severity"], "transform": "severity_name"},
    },
    "observables": [
        {"type": "mail", "paths": ["$.actor.email", "$.recipients[*].address"]},
        {"type": "ip", "paths": ["$.source.ip"]},
    ],
}


def _source(organisation: Organisation, *, slug: str = "edr", config: dict | None = None):
    return IngestionSource.objects.create(
        organisation=organisation,
        slug=slug,
        name=slug,
        mapping_config=CONFIG if config is None else config,
    )


def _store(source: IngestionSource, *, ref: str, payload: dict | None = None):
    return store_ingested_alert(
        wire_source=source.slug,
        alert_type="sign-in",
        payload=PAYLOAD if payload is None else payload,
        title="Suspicious sign-in",
        mapped_source_ref=ref,
        severity=4,
        ingestion_source=source,
        owner_org=source.organisation,
    )


def test_ingest_links_every_candidate_to_the_alert(default_org: Organisation) -> None:
    source = _source(default_org)

    stored = _store(source, ref="evt-1")

    links = AlertObservable.objects.filter(alert=stored.alert).select_related(
        "observable__data_type"
    )
    assert {
        (link.observable.data_type.name, link.observable.normalized_data) for link in links
    } == {
        ("mail", "alice@example.com"),
        ("mail", "bob@example.com"),
        ("ip", "203.0.113.10"),
    }
    assert all(link.observable.owner_org == default_org for link in links)
    assert stored.alert.raw_payload == PAYLOAD


def test_replaying_the_same_payload_does_not_duplicate_links(default_org: Organisation) -> None:
    source = _source(default_org)
    first = _store(source, ref="evt-1")
    assert AlertObservable.objects.filter(alert=first.alert).count() == 3

    replay = _store(source, ref="evt-1", payload={**PAYLOAD, "title": "resent"})

    assert replay.created is False
    assert replay.alert.pk == first.alert.pk
    assert AlertObservable.objects.filter(alert=first.alert).count() == 3


def test_one_observable_is_shared_across_alerts_in_the_same_org(
    default_org: Organisation,
) -> None:
    source = _source(default_org)
    first = _store(source, ref="evt-1")
    second = _store(source, ref="evt-2")

    assert first.alert.pk != second.alert.pk
    assert Observable.objects.filter(owner_org=default_org, data_type__name="mail").count() == 2
    assert AlertObservable.objects.filter(observable__data_type__name="mail").count() == 4
    assert set(
        AlertObservable.objects.filter(observable__data_type__name="mail").values_list(
            "alert_id", flat=True
        )
    ) == {first.alert.pk, second.alert.pk}


def test_a_source_without_observable_rules_creates_no_links(default_org: Organisation) -> None:
    source = _source(default_org, config={"version": 2, "fields": {}})

    stored = _store(source, ref="evt-1")

    assert stored.created
    assert AlertObservable.objects.filter(alert=stored.alert).count() == 0


def test_a_direct_pipeline_call_without_a_source_creates_no_links(
    default_org: Organisation,
) -> None:
    stored = store_ingested_alert(
        wire_source="direct",
        alert_type="sign-in",
        payload=PAYLOAD,
        title="Direct",
        mapped_source_ref="evt-1",
        owner_org=default_org,
    )

    assert stored.created
    assert AlertObservable.objects.filter(alert=stored.alert).count() == 0


def test_an_unknown_type_is_skipped_with_a_warning_but_the_alert_survives(
    default_org: Organisation,
) -> None:
    source = _source(
        default_org,
        config={
            "version": 2,
            "fields": {"title": {"paths": ["$.title"]}},
            "observables": [{"type": "not-a-real-type", "paths": ["$.actor.email"]}],
        },
    )

    stored = _store(source, ref="evt-1")

    assert stored.created
    assert AlertObservable.objects.filter(alert=stored.alert).count() == 0
    warnings = stored.alert.ingestion_warnings["warnings"]
    assert any(w.startswith("observable_type_unknown:") for w in warnings), warnings


def test_the_webhook_endpoint_links_observables(default_org: Organisation) -> None:
    source = _source(default_org)
    _token, plaintext = IngestionToken.create_token(
        source=source, organisation=default_org, name="edr"
    )

    response = Client().post(
        reverse("alerts-webhook"),
        data=PAYLOAD,
        content_type="application/json",
        headers={"X-Webhook-Secret": plaintext},
    )

    assert response.status_code == 201, response.content
    alert = Alert.objects.get(owner_org=default_org)
    assert AlertObservable.objects.filter(alert=alert).count() == 3
