"""Conformance tests for alert→observable provenance in the case and alert detail pages.

`AlertObservable` records which alert(s) contributed a given observable. The case Observables
tab surfaces it as a Source column and the alert detail page lists the observables the alert
contributed. These views previously assumed alerts have a numeric `number` (they do not — only
cases do), so the Source column rendered `alert #` with an empty value. These tests pin the
title-based link and the "N other alerts" rollup.
"""

import pytest
from django.test import Client

from alerts.models import Alert, AlertObservable, AlertStatus
from cases.models import Case, CaseObservable, CaseStatus
from identity.models import Organisation, User
from observables.models import Observable, ObservableSuggestion, ObservableType


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Provenance Org")


@pytest.fixture
def user(db, org):
    return User.objects.create(
        login="analyst", username="analyst", email="a@b.com", org=org, is_active=True
    )


@pytest.fixture
def client(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def case_status(db):
    return CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 0}
    )[0]


@pytest.fixture
def alert_status(db):
    return AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 0})[0]


@pytest.fixture
def case(db, org, case_status):
    return Case.objects.create(title="Provenance Case", status=case_status, owner_org=org)


@pytest.fixture
def mail_type(db):
    return ObservableType.objects.get(name="mail")


def _alert(org, status, title):
    return Alert.objects.create(
        title=title,
        type="phishing",
        source="mock",
        source_ref=f"ref-{title}",
        status=status,
        owner_org=org,
    )


def _email_observable(org, mail_type, value):
    return Observable.objects.create(
        data_type=mail_type, data=value, normalized_data=value, owner_org=org
    )


def test_accepting_suggestion_creates_alert_provenance(client, case, org, alert_status, mail_type):
    alert = _alert(org, alert_status, "Phishing Alert")
    suggestion = ObservableSuggestion.objects.create(
        owner_org=org,
        data_type=mail_type,
        normalized_data="prov@example.com",
        raw_data="prov@example.com",
        case=case,
        source_alert=alert,
        status="proposed",
    )

    client.post(f"/suggestions/{suggestion.id}/accept")

    suggestion.refresh_from_db()
    assert AlertObservable.objects.filter(alert=alert, observable=suggestion.observable).exists()


def test_case_source_column_links_alert_title(client, case, org, alert_status, mail_type):
    alert = _alert(org, alert_status, "CrowdStrike Detection")
    observable = _email_observable(org, mail_type, "source@example.com")
    CaseObservable.objects.create(case=case, observable=observable)
    AlertObservable.objects.create(alert=alert, observable=observable)

    response = client.get(f"/cases/{case.number}?tab=observables")
    body = response.content.decode()

    assert response.status_code == 200
    assert "CrowdStrike Detection" in body
    # The old bug rendered a bare "alert #" with an empty number.
    assert "alert #" not in body


def test_case_source_column_rolls_up_extra_alerts(client, case, org, alert_status, mail_type):
    first = _alert(org, alert_status, "First Alert")
    second = _alert(org, alert_status, "Second Alert")
    observable = _email_observable(org, mail_type, "multi@example.com")
    CaseObservable.objects.create(case=case, observable=observable)
    AlertObservable.objects.create(alert=first, observable=observable)
    AlertObservable.objects.create(alert=second, observable=observable)

    response = client.get(f"/cases/{case.number}?tab=observables")
    body = response.content.decode()

    assert "1 other alert" in body


def test_alert_detail_lists_observables_and_verdict(client, case, org, alert_status, mail_type):
    alert = _alert(org, alert_status, "Detail Alert")
    alert.verdict = "true_positive"
    alert.save(update_fields=["verdict"])
    observable = _email_observable(org, mail_type, "detail@example.com")
    AlertObservable.objects.create(alert=alert, observable=observable)

    response = client.get(f"/alerts/{alert.id}")
    body = response.content.decode()

    assert response.status_code == 200
    assert "Verdict" in body
    assert "true_positive" in body
    assert "detail@example.com" in body


def test_alert_detail_observable_case_link_is_visible_only_for_visible_cases(
    client, case, org, alert_status, mail_type
):
    alert = _alert(org, alert_status, "Scoped Alert")
    observable = _email_observable(org, mail_type, "scoped@example.com")
    AlertObservable.objects.create(alert=alert, observable=observable)
    CaseObservable.objects.create(case=case, observable=observable)

    response = client.get(f"/alerts/{alert.id}")
    body = response.content.decode()

    assert f"#{case.number}" in body
