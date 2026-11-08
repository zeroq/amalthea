"""Template conformance tests for the "raw-data click-to-map" feature (TODO §17).

See TODO.md §17 and the implementation in:
  - ui/templates/ui/_json_map_modal.html
  - ui/templates/ui/source_detail.html
  - ui/static/ui/json-map.js
  - ui/templates/ui/_json_tree.html
  - ui/templatetags/ui_tags.py
  - ui/views/ingestion_sources.py
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User
from ingest.models import IngestionSource
from observables.models import ObservableType

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Click-Map-Template Org")


@pytest.fixture
def admin(org: Organisation) -> User:
    return User.objects.create(
        login="click-template-admin",
        username="click-template-admin",
        email="admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )


@pytest.fixture
def source(org: Organisation) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=org,
        slug="click-template-feed",
        name="Click template feed",
        mapping_config={"version": 2, "fields": {"title": {"paths": ["$.name"]}}},
    )


@pytest.fixture
def client(admin: User) -> Client:
    c = Client()
    c.force_login(admin)
    return c


def _url(source: IngestionSource) -> str:
    return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})


def _make_alert(source, org, *, source_ref, payload, date):
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        title="Raw alert",
        type="phishing",
        source="webhook",
        source_ref=source_ref,
        status=status,
        raw_payload=payload,
        ingestion_source=source,
        owner_org=org,
        date=date,
    )


# ---------------------------------------------------------------------------
# Raw-data tab: JSON tree and modal markup
# ---------------------------------------------------------------------------


def test_raw_tab_has_json_tree_and_leaf_paths(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Raw tab with an ingested alert: response contains class="json-tree" and a concrete
    data-json-path="$.<...>" for a leaf."""
    _make_alert(
        source,
        org,
        source_ref="ref1",
        payload={"iocs": {"ip": "1.2.3.4", "arr": [{"h": "x"}]}},
        date="2026-01-01",
    )
    response = client.get(_url(source) + "?tab=raw-data")
    body = response.content.decode()

    assert response.status_code == 200
    assert 'class="json-tree"' in body, "expected json-tree class in raw-data tab body"
    # A leaf span with a concrete JSON path
    assert 'data-json-path="$.iocs.ip"' in body, "expected data-json-path for a leaf"
    # An array element also offers the all-items wildcard variant
    assert 'data-json-path="$.iocs.arr[0].h"' in body
    assert 'data-json-path-all="$.iocs.arr[*].h"' in body
    # Real nesting: list items and a child group inside a branch
    assert '<li class="json-node"' in body, "expected tree list items"
    assert '<details class="json-branch" open>' in body, "expected expandable branches"
    assert 'class="json-tree children"' in body, "expected nested json-tree children ul"
    # Keys and array indices are rendered; containers are not clickable
    assert '<span class="json-key">iocs</span>' in body
    assert '<span class="json-key">[0]</span>' in body
    assert 'data-json-path="$.iocs"' not in body
    assert 'data-json-path="$.iocs.arr"' not in body
    # Container counts (root object has 1 key; iocs has 2 keys; arr has 1 item)
    assert ">2 keys<" in body
    assert ">1 item<" in body


def test_raw_tab_json_tree_escapes_keys_and_values(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Hostile keys/values are autoescaped in the tree and never emitted raw."""
    _make_alert(
        source,
        org,
        source_ref="ref-xss",
        payload={"<b>k</b>": "<script>alert(1)</script>"},
        date="2026-01-01",
    )
    response = client.get(_url(source) + "?tab=raw-data")
    body = response.content.decode()

    assert response.status_code == 200
    assert "&lt;b&gt;k&lt;/b&gt;" in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>alert(1)</script>" not in body


def test_raw_tab_always_contains_modal_markup(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Raw tab always contains the modal markup: all 7 target labels,
    value="observable", name="map_observable_type", and an existing ObservableType.name
    appears as an option."""
    # Create an ObservableType so the test can verify it appears as an option
    ObservableType.objects.create(name="IP", is_case_sensitive=False)
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    response = client.get(_url(source) + "?tab=raw-data")
    body = response.content.decode()

    assert response.status_code == 200
    # All 7 target labels present (hardcoded from ingest.mapping.TARGET_LABELS)
    target_labels = {
        "title": "Title",
        "description": "Description",
        "summary": "Summary",
        "severity": "Severity",
        "date": "Date",
        "type": "Type",
        "source_ref": "Source reference",
    }
    for label in target_labels.values():
        assert label in body, f"expected target label '{label}' in raw-data modal markup"
    # observable radio button
    assert 'value="observable"' in body, "expected value='observable' in map_target radio group"
    assert 'name="map_observable_type"' in body, "expected name='map_observable_type' in select"
    # ObservableType name appears as an option
    assert '<option value="IP">IP</option>' in body, "expected ObservableType 'IP' as an option"
    # Template comments must never leak as visible text (a multi-line `{# #}` is not a
    # Django comment and renders literally; only `{% comment %}` may span lines).
    assert "{#" not in body
    assert "raw-data click-to-map:" not in body


def test_raw_tab_has_external_json_map_script(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Raw tab loads <script src=".../ui/json-map.js" and contains no inline <script>
    executing payload (assert the external script tag and that a payload string like
    <script>alert(1)</script> is escaped, not raw)."""
    _make_alert(
        source,
        org,
        source_ref="ref1",
        payload={"note": "<script>alert(1)</script>"},
        date="2026-01-01",
    )
    response = client.get(_url(source) + "?tab=raw-data")
    body = response.content.decode()

    assert response.status_code == 200
    # External script tag present
    assert 'src="/static/ui/json-map.js"' in body, "expected external json-map.js script tag"
    # Inline script with payload is escaped, not raw
    assert "<script>alert(1)</script>" not in body, "inline script payload must be escaped"


def test_raw_tab_no_inline_script_executing_payload(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Raw tab: a reflected <script> in the payload is never executed (escaped, not raw)."""
    _make_alert(
        source,
        org,
        source_ref="ref1",
        payload={"note": "<script>alert(1)</script>"},
        date="2026-01-01",
    )
    response = client.get(_url(source) + "?tab=raw-data")
    body = response.content.decode()

    assert response.status_code == 200
    # CSP: every <script> in the document must be an external src= tag; no inline code.
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", body)
    assert inline == [], f"inline <script> tags violate the CSP: {inline}"


# ---------------------------------------------------------------------------
# Mapping tab: prefill behaviour
# ---------------------------------------------------------------------------


def test_mapping_tab_with_prefill_has_hint_and_data_prefilled(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Mapping tab with ?tab=mapping&map_target=title&map_path=$.actor.email:
    contains the prefill hint and data-prefilled="1" on paths__title."""
    _make_alert(
        source,
        org,
        source_ref="ref1",
        payload={"actor": {"email": "user@example.com"}},
        date="2026-01-01",
    )
    response = client.get(_url(source) + "?tab=mapping&map_target=title&map_path=$.actor.email")
    body = response.content.decode()

    assert response.status_code == 200
    # Prefill hint present
    assert 'class="hint" role="status"' in body, "expected prefill hint role status"
    assert "Added" in body, "expected 'Added' in prefill hint"
    # data-prefilled on the title textarea
    assert 'data-prefilled="1"' in body, "expected data-prefilled=1 on paths__title"
    # The path should appear in the body (auto-escaped)
    assert "$.actor.email" in body
    # The textarea value must start immediately after `>`: a newline here would be
    # submitted as leading whitespace on the next Save.
    assert re.search(r'id="paths__title"[^>]*>\$\.name', body), (
        "textarea content must not begin with whitespace"
    )


def test_mapping_tab_prefill_observable_type(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    """Mapping tab with ?tab=mapping&map_target=observable&map_observable_type=IP&map_path=$.iocs.ip:
    contains the prefill hint and data-prefilled="1" on observable_paths__IP."""
    ObservableType.objects.create(name="IP", is_case_sensitive=False)
    _make_alert(
        source, org, source_ref="ref1", payload={"iocs": {"ip": "1.2.3.4"}}, date="2026-01-01"
    )
    response = client.get(
        _url(source)
        + "?tab=mapping&map_target=observable&map_observable_type=IP&map_path=$.iocs.ip"
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert "Added" in body, "expected prefill hint"
    assert 'data-prefilled="1"' in body, "expected data-prefilled=1 on observable_paths__IP"
    assert "$.iocs.ip" in body, "expected path in body"
