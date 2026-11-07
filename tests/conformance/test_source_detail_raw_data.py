"""Source detail: Mapping and Raw data tabs (PLAN-2026-11-06-source-detail-raw-data).

The source detail page gains a tab strip:
* Mapping (default) — current mapping rules, observable rules, health card.
* Raw data — the latest stored Alert.raw_payload for that ingestion source.

Raw data is the latest *stored* alert row (replays do not overwrite raw_payload), ordered by
`-date, -created_at`. Rendering is auto-escaped plain text in `<pre>`; no link to the alert.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User
from ingest.models import IngestionSource

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Raw Data Org")


@pytest.fixture
def admin(org: Organisation) -> User:
    return User.objects.create(
        login="raw-admin",
        username="raw-admin",
        email="admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )


@pytest.fixture
def source(org: Organisation) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=org,
        slug="raw-feed",
        name="Raw feed",
        mapping_config={"version": 2, "fields": {"title": {"paths": ["$.name"]}}},
    )


@pytest.fixture
def client(admin: User) -> Client:
    c = Client()
    c.force_login(admin)
    return c


def _status() -> AlertStatus:
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return status


def _url(source: IngestionSource) -> str:
    return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})


def _make_alert(
    source: IngestionSource,
    org: Organisation,
    *,
    source_ref: str,
    payload: dict,
    date: datetime,
) -> Alert:
    return Alert.objects.create(
        title="Raw alert",
        type="phishing",
        source="webhook",
        source_ref=source_ref,
        status=_status(),
        raw_payload=payload,
        ingestion_source=source,
        owner_org=org,
        date=date,
    )


def test_default_tab_shows_mapping_content(client: Client, source: IngestionSource) -> None:
    response = client.get(_url(source))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Mapping rules" in body
    assert "$.name" in body
    assert 'aria-current="page"' in body
    # The Mapping tab is active by default and no raw-data panel is rendered.
    assert body.count('href="?tab=mapping"') == 1
    assert "No raw data yet." not in body


def test_raw_data_tab_shows_latest_payload_and_identity(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    first = _make_alert(
        source,
        org,
        source_ref="ref-old",
        payload={"field": "old"},
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    latest = _make_alert(
        source,
        org,
        source_ref="ref-new",
        payload={"field": "new", "deep": {"name": "target"}},
        date=datetime(2026, 1, 2, tzinfo=UTC),
    )

    response = client.get(_url(source) + "?tab=raw-data")
    body = response.content.decode()

    assert response.status_code == 200
    assert "Raw data" in body
    # The mapping panel is hidden on the raw-data tab.
    assert "Mapping rules" not in body
    assert str(latest.id) in body
    assert latest.source_ref in body
    assert latest.type in body
    assert org.name in body
    assert "&quot;field&quot;: &quot;new&quot;" in body
    assert "&quot;name&quot;: &quot;target&quot;" in body
    # The older alert must not surface on the latest-raw tab.
    assert first.source_ref not in body
    assert str(first.id) not in body


def test_raw_data_tab_picks_most_recent_by_date(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    _make_alert(
        source,
        org,
        source_ref="ref-a",
        payload={"winner": "a"},
        date=datetime(2026, 1, 3, tzinfo=UTC),
    )
    _make_alert(
        source,
        org,
        source_ref="ref-b",
        payload={"winner": "b"},
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )

    body = client.get(_url(source) + "?tab=raw-data").content.decode()

    assert "Mapping rules" not in body
    assert "&quot;winner&quot;: &quot;a&quot;" in body
    assert "&quot;winner&quot;: &quot;b&quot;" not in body


def test_raw_data_tab_autoescapes_payload(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    _make_alert(
        source,
        org,
        source_ref="ref-xss",
        payload={"note": "<script>alert(1)</script>"},
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )

    body = client.get(_url(source) + "?tab=raw-data").content.decode()

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>" not in body


def test_raw_data_empty_state_when_no_alerts(client: Client, source: IngestionSource) -> None:
    response = client.get(_url(source) + "?tab=raw-data")
    body = response.content.decode()

    assert response.status_code == 200
    assert "No raw data yet." in body
    assert "ingested from this feed" in body


def test_invalid_tab_falls_back_to_mapping(client: Client, source: IngestionSource) -> None:
    body = client.get(_url(source) + "?tab=bogus").content.decode()

    assert "Mapping rules" in body
    assert "No raw data yet." not in body


def test_analyst_cannot_reach_source_detail(org: Organisation, source: IngestionSource) -> None:
    analyst = User.objects.create(
        login="raw-analyst",
        username="raw-analyst",
        email="analyst@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )
    c = Client()
    c.force_login(analyst)

    assert c.get(_url(source)).status_code == 404
