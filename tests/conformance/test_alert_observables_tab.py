"""P4 — alert observables live in their own tab, not in the General panel."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import AlertObservable
from identity.models import User
from ingest.models import IngestionSource

pytestmark = pytest.mark.django_db


def _alert(make_alert, **kw):
    return make_alert(
        title="Mapped alert",
        type="phishing",
        source="mock",
        source_ref="ref-1",
        **kw,
    )


def _panel(html: str, slug: str) -> str:
    """Return one tabpanel, from its opening tag to the next panel or the end."""
    marker = f'id="panel-{slug}"'
    start = html.rfind("<section", 0, html.index(marker))
    end = html.find('<section class="tabpanel"', start + 10)
    return html[start:] if end == -1 else html[start:end]


def _opening_tag(panel: str) -> str:
    return panel[: panel.index(">") + 1]


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_alert_observables_render_in_the_observables_tab(
    make_alert, make_observable, make_user
) -> None:
    alert = _alert(make_alert)
    observable = make_observable(
        data_type="mail", data="mapped@example.com", normalized_data="mapped@example.com"
    )
    AlertObservable.objects.create(alert=alert, observable=observable)

    response = _client(make_user(role="analyst")).get(
        reverse("ui-alert-detail", kwargs={"alert_id": alert.id}) + "?tab=observables"
    )
    html = response.content.decode()

    assert response.status_code == 200
    panel = _panel(html, "observables")
    assert "mapped@example.com" in panel
    assert "h-observables" in panel
    assert "hidden" not in _opening_tag(panel)
    assert "mapped@example.com" not in _panel(html, "general")


def test_general_is_the_default_and_the_observables_panel_is_hidden(
    make_alert, make_observable, make_user
) -> None:
    alert = _alert(make_alert)
    observable = make_observable(
        data_type="mail", data="hidden@example.com", normalized_data="hidden@example.com"
    )
    AlertObservable.objects.create(alert=alert, observable=observable)

    response = _client(make_user(role="analyst")).get(
        reverse("ui-alert-detail", kwargs={"alert_id": alert.id})
    )
    html = response.content.decode()

    assert "hidden" not in _opening_tag(_panel(html, "general"))
    assert "hidden" in _opening_tag(_panel(html, "observables"))
    assert "hidden@example.com" in _panel(html, "observables")


def test_the_observables_tab_has_tab_semantics_and_an_icon(make_alert, make_user) -> None:
    alert = _alert(make_alert)

    response = _client(make_user(role="analyst")).get(
        reverse("ui-alert-detail", kwargs={"alert_id": alert.id}) + "?tab=observables"
    )
    html = response.content.decode()

    assert 'id="tab-observables"' in html
    assert 'role="tab"' in html
    assert 'href="?tab=observables"' in html
    assert 'aria-controls="panel-observables"' in html
    assert 'aria-selected="true"' in html
    assert "fa-cube" in html


def test_empty_state_names_the_mapping_fix(make_alert, make_user, default_org) -> None:
    source = IngestionSource.objects.create(
        organisation=default_org,
        slug="empty-feed",
        name="Empty feed",
        mapping_config={},
    )
    alert = _alert(make_alert, ingestion_source=source)

    response = _client(make_user(role="analyst")).get(
        reverse("ui-alert-detail", kwargs={"alert_id": alert.id}) + "?tab=observables"
    )
    html = response.content.decode()
    panel = _panel(html, "observables")

    assert "No observables have been mapped for this alert." in panel
    assert "empty-feed" in panel
    assert "Configure observable paths" in panel


def test_viewer_can_read_the_observables_tab(make_alert, make_user) -> None:
    alert = _alert(make_alert)
    viewer = User.objects.create(
        login="observables-viewer",
        username="observables-viewer",
        email="viewer@example.net",
        org=alert.owner_org,
        role="viewer",
        is_active=True,
    )

    response = _client(viewer).get(
        reverse("ui-alert-detail", kwargs={"alert_id": alert.id}) + "?tab=observables"
    )

    assert response.status_code == 200
    assert "h-observables" in response.content.decode()
