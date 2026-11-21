"""Conformance tests for the observable detail Linked Alerts card."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from alerts.models import AlertObservable
from cases.models import CaseObservable
from identity.models import Organisation
from ui.pagination import RAIL_CAP


def _client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _detail_url(observable_id) -> str:
    return reverse("ui-observable-detail", kwargs={"observable_id": observable_id})


def _link(alert, observable, added_by=None) -> AlertObservable:
    return AlertObservable.objects.create(alert=alert, observable=observable, added_by=added_by)


@pytest.mark.django_db
class TestLinkedAlertsCard:
    def test_renders_alert_columns_and_links(
        self, make_user, make_observable, make_alert, default_org
    ) -> None:
        user = make_user(login="analyst", username="analyst", org=default_org)
        observable = make_observable(org=default_org, data="10.1.2.3")
        alert = make_alert(
            org=default_org,
            title="Suspicious sign-in",
            source="siem",
            source_ref="evt-1",
            severity=3,
            date=timezone.now() - timedelta(hours=1),
        )
        _link(alert, observable, added_by=user)

        body = _client(user).get(_detail_url(observable.id)).content.decode()

        assert "Linked Alerts (1)" in body
        assert '<caption class="sr-only">Linked alerts</caption>' in body
        assert f'data-linked-alert-id="{alert.id}"' in body
        assert reverse("ui-alert-detail", kwargs={"alert_id": alert.id}) in body
        assert "Suspicious sign-in" in body
        assert alert.status.value in body
        assert '<th scope="col">Seen</th>' in body

    def test_orders_newest_first(self, make_user, make_observable, make_alert, default_org) -> None:
        user = make_user(login="analyst", username="analyst", org=default_org)
        observable = make_observable(org=default_org, data="10.1.2.4")
        older = make_alert(
            org=default_org,
            title="Older alert",
            source_ref="old",
            date=timezone.now() - timedelta(days=2),
        )
        newer = make_alert(
            org=default_org,
            title="Newer alert",
            source_ref="new",
            date=timezone.now() - timedelta(hours=1),
        )
        _link(older, observable)
        _link(newer, observable)

        body = _client(user).get(_detail_url(observable.id)).content.decode()

        assert body.index("Newer alert") < body.index("Older alert")

    def test_caps_rows_and_shows_honest_total(
        self, make_user, make_observable, make_alert, default_org
    ) -> None:
        user = make_user(login="analyst", username="analyst", org=default_org)
        observable = make_observable(org=default_org, data="10.1.2.5")
        for index in range(RAIL_CAP + 1):
            alert = make_alert(
                org=default_org,
                title=f"Alert {index}",
                source_ref=f"alert-{index}",
                date=timezone.now() - timedelta(minutes=index),
            )
            _link(alert, observable)

        body = _client(user).get(_detail_url(observable.id)).content.decode()

        assert body.count("data-linked-alert-id=") == RAIL_CAP
        assert f"Linked Alerts ({RAIL_CAP})" in body
        assert f"Showing latest {RAIL_CAP} of {RAIL_CAP + 1} linked alerts" in body

    def test_empty_state_when_only_case_links_exist(
        self, make_user, make_observable, make_case, default_org
    ) -> None:
        user = make_user(login="analyst", username="analyst", org=default_org)
        observable = make_observable(org=default_org, data="10.1.2.6")
        case = make_case(org=default_org, title="Case only")
        CaseObservable.objects.create(case=case, observable=observable)

        body = _client(user).get(_detail_url(observable.id)).content.decode()

        assert "This observable is not linked to any alert." in body
        assert '<caption class="sr-only">Linked alerts</caption>' not in body

    def test_cross_org_alert_link_is_hidden_from_non_superadmin(
        self, make_user, make_observable, make_alert, default_org
    ) -> None:
        user = make_user(login="analyst", username="analyst", org=default_org)
        other_org = Organisation.objects.create(name="Other org")
        observable = make_observable(org=default_org, data="10.1.2.7")
        alert = make_alert(org=other_org, title="Foreign alert", source_ref="foreign")
        _link(alert, observable)

        body = _client(user).get(_detail_url(observable.id)).content.decode()

        assert "Foreign alert" not in body
        assert "Linked Alerts (0)" in body

    def test_superadmin_sees_cross_org_alert_link(
        self, super_admin, make_observable, make_alert
    ) -> None:
        observable = make_observable(org=super_admin.org, data="10.1.2.8")
        other_org = Organisation.objects.create(name="Super other org")
        alert = make_alert(org=other_org, title="Foreign alert", source_ref="foreign")
        _link(alert, observable)

        body = _client(super_admin).get(_detail_url(observable.id)).content.decode()

        assert "Foreign alert" in body
        assert "Linked Alerts (1)" in body

    def test_cross_org_observable_detail_still_returns_404(
        self, make_user, make_observable, default_org
    ) -> None:
        user = make_user(login="analyst", username="analyst", org=default_org)
        other_org = Organisation.objects.create(name="Other org")
        observable = make_observable(org=other_org, data="10.1.2.9")

        response = _client(user).get(_detail_url(observable.id))

        assert response.status_code == 404
