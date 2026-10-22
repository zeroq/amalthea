"""Conformance tests for the superAdmin-only alert/case deletion UI actions.

The destructive actions are exposed in the alert and case list tables, but only to a
`superAdmin`, and the server must enforce that independently of what the template renders.
These tests guard the server-side half: a non-superAdmin POST must be refused (404), and a
superAdmin POST must actually delete the row.
"""

import pytest
from django.test import Client

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation, User


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Delete Org")


@pytest.fixture
def analyst(db, org):
    return User.objects.create(
        login="analyst",
        username="analyst",
        email="a@b.com",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def superadmin(db, org):
    return User.objects.create(
        login="root",
        username="root",
        email="root@b.com",
        org=org,
        is_active=True,
        role="superAdmin",
    )


def _client_for(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def client_for(analyst):
    return _client_for(analyst)


@pytest.fixture
def superadmin_client(superadmin):
    return _client_for(superadmin)


@pytest.fixture
def case(db, org):
    status, _ = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 0}
    )
    return Case.objects.create(title="Delete Case", status=status, owner_org=org)


@pytest.fixture
def alert(db, org):
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 0}
    )
    return Alert.objects.create(title="Delete Alert", status=status, owner_org=org)


# --- case deletion ----------------------------------------------------------------


def test_non_superadmin_cannot_delete_case(client_for, analyst, case):
    response = client_for.post(f"/cases/{case.number}/delete")

    assert response.status_code == 404
    assert Case.objects.filter(pk=case.pk).exists()


def test_superadmin_can_delete_case(superadmin_client, case):
    response = superadmin_client.post(f"/cases/{case.number}/delete")

    assert response.status_code == 302
    assert not Case.objects.filter(pk=case.pk).exists()


def test_case_delete_is_post_only(superadmin_client, case):
    response = superadmin_client.get(f"/cases/{case.number}/delete")

    assert response.status_code == 405
    assert Case.objects.filter(pk=case.pk).exists()


# --- alert deletion ---------------------------------------------------------------


def test_non_superadmin_cannot_delete_alert(client_for, analyst, alert):
    response = client_for.post(f"/alerts/{alert.id}/delete")

    assert response.status_code == 404
    assert Alert.objects.filter(pk=alert.pk).exists()


def test_superadmin_can_delete_alert(superadmin_client, alert):
    response = superadmin_client.post(f"/alerts/{alert.id}/delete")

    assert response.status_code == 302
    assert not Alert.objects.filter(pk=alert.pk).exists()


def test_alert_delete_is_post_only(superadmin_client, alert):
    response = superadmin_client.get(f"/alerts/{alert.id}/delete")

    assert response.status_code == 405
    assert Alert.objects.filter(pk=alert.pk).exists()


# --- visibility in the list templates ----------------------------------------------


def test_alert_list_shows_delete_only_to_superadmin(client_for, superadmin_client, alert):
    analyst_response = client_for.get("/alerts")
    assert "fa-trash" not in analyst_response.content.decode()

    superadmin_response = superadmin_client.get("/alerts")
    assert "fa-trash" in superadmin_response.content.decode()


def test_case_list_shows_delete_only_to_superadmin(client_for, superadmin_client, case):
    analyst_response = client_for.get("/cases")
    assert "fa-trash" not in analyst_response.content.decode()

    superadmin_response = superadmin_client.get("/cases")
    assert "fa-trash" in superadmin_response.content.decode()


def test_list_pages_do_not_leak_literal_template_tags(superadmin_client, alert, case):
    """A malformed/multi-line tag must never reach the browser as text."""
    for path in ("/alerts", "/cases"):
        body = superadmin_client.get(path).content.decode()
        assert "{%" not in body, f"literal template tag leaked on {path}"
        assert "{{" not in body, f"literal template expression leaked on {path}"


def test_delete_modals_are_rendered_with_matching_ids(superadmin_client, alert, case):
    """The dialog id must be the interpolated UUID the opener button points at."""
    for path, modal_id in (
        ("/alerts", f"delete-alert-{alert.id}"),
        ("/cases", f"delete-case-{case.id}"),
    ):
        body = superadmin_client.get(path).content.decode()
        assert f'data-modal-open="{modal_id}"' in body
        assert f'id="{modal_id}"' in body
