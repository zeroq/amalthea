"""Conformance tests for the admin/superAdmin alert/case deletion UI actions.

The destructive actions are exposed in the alert and case list tables, but only to
an `admin` (own-org) or `superAdmin` (cross-org), and the server must enforce that
independently of what the template renders.

These tests guard the server-side half: a non-admin/non-superAdmin POST must be
refused (404), and an admin superAdmin POST must actually delete the row (within
own org for admin). These tests also guard the UX half: the delete affordance
appears only where the signed-in user may actually delete.
"""

import pytest
from django.test import Client

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus, Share
from identity.models import Organisation, User


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Delete Org")


@pytest.fixture
def admin(db, org):
    return User.objects.create(
        login="admin",
        username="admin",
        email="admin@b.com",
        org=org,
        is_active=True,
        role="admin",
    )


@pytest.fixture
def admin_client(admin):
    return _client_for(admin)


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


@pytest.fixture
def other_org(db):
    return Organisation.objects.create(name="Other Org")


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


def test_superadmin_can_delete_another_org_case(superadmin_client, other_org):
    other_case = Case.objects.create(
        title="Other Org Case",
        status=CaseStatus.objects.get(value="InProgress"),
        owner_org=other_org,
    )
    response = superadmin_client.post(f"/cases/{other_case.number}/delete")

    assert response.status_code == 302
    assert not Case.objects.filter(pk=other_case.pk).exists()


# --- admin case deletion --------------------------------------------------------


def test_admin_can_delete_own_org_case(admin_client, case):
    """Admin may permanently delete a case belonging to their own organisation."""
    response = admin_client.post(f"/cases/{case.number}/delete")
    assert response.status_code == 302
    assert not Case.objects.filter(pk=case.pk).exists()


def test_admin_cannot_delete_another_org_case(admin_client, other_org):
    """Admin may not delete a case belonging to another organisation."""
    other_case = Case.objects.create(
        title="Other Org Case",
        status=CaseStatus.objects.get(value="InProgress"),
        owner_org=other_org,
    )
    response = admin_client.post(f"/cases/{other_case.number}/delete")
    assert response.status_code == 404
    assert Case.objects.filter(pk=other_case.pk).exists()


def test_admin_cannot_delete_shared_org_case_even_with_write_share(
    admin_client, other_org, org, db
):
    """Admin may NOT delete a shared-org case even when a write Share exists.

    A Share grants read/write access to another org's case, but the admin's
    deletion guard checks `case.owner_org_id == user.org_id`, not share
    permissions.  This test verifies that invariant.
    """
    shared_case = Case.objects.create(
        title="Shared Case", status=CaseStatus.objects.get(value="InProgress"), owner_org=other_org
    )
    # Grant the admin's org a read/write share on the case
    Share.objects.create(
        case=shared_case, organisation=org, permissions={"read": True, "write": True}
    )

    response = admin_client.post(f"/cases/{shared_case.number}/delete")
    assert response.status_code == 404
    assert Case.objects.filter(pk=shared_case.pk).exists()


def test_superadmin_can_delete_another_org_alert(superadmin_client, other_org):
    other_alert = Alert.objects.create(
        title="Other Org Alert",
        status=AlertStatus.objects.get(value="New"),
        owner_org=other_org,
    )
    response = superadmin_client.post(f"/alerts/{other_alert.id}/delete")

    assert response.status_code == 302
    assert not Alert.objects.filter(pk=other_alert.pk).exists()


# --- admin alert deletion -------------------------------------------------------


def test_admin_can_delete_own_org_alert(admin_client, org):
    """Admin may permanently delete an alert belonging to their own organisation."""
    own_alert = Alert.objects.create(
        title="Own Org Alert",
        status=AlertStatus.objects.get(value="New"),
        owner_org=org,
    )
    response = admin_client.post(f"/alerts/{own_alert.id}/delete")
    assert response.status_code == 302
    assert not Alert.objects.filter(pk=own_alert.pk).exists()


def test_admin_cannot_delete_another_org_alert(admin_client, other_org):
    """Admin may not delete an alert belonging to another organisation."""
    other_alert = Alert.objects.create(
        title="Other Org Alert",
        status=AlertStatus.objects.get(value="New"),
        owner_org=other_org,
    )
    response = admin_client.post(f"/alerts/{other_alert.id}/delete")
    assert response.status_code == 404
    assert Alert.objects.filter(pk=other_alert.pk).exists()


# --- visibility in the list templates ----------------------------------------------


def test_alert_list_shows_delete_only_to_admin_and_superadmin(
    client_for, superadmin_client, admin_client, alert
):
    """Alert list shows delete affordance for admin and superAdmin; analyst sees none."""
    analyst_response = client_for.get("/alerts")
    assert "fa-trash" not in analyst_response.content.decode()

    admin_response = admin_client.get("/alerts")
    assert "fa-trash" in admin_response.content.decode()

    superadmin_response = superadmin_client.get("/alerts")
    assert "fa-trash" in superadmin_response.content.decode()


def test_case_list_shows_delete_for_admin_own_org_but_not_shared(
    client_for, admin_client, case, org, other_org, superadmin_client
):
    """Admin sees delete for own-org cases; a writable share is not ownership."""
    shared_case = Case.objects.create(
        title="Shared Case",
        status=CaseStatus.objects.get(value="InProgress"),
        owner_org=other_org,
    )
    Share.objects.create(
        case=shared_case, organisation=org, permissions={"read": True, "write": True}
    )

    analyst_body = client_for.get("/cases").content.decode()
    assert "fa-trash" not in analyst_body

    admin_body = admin_client.get("/cases").content.decode()
    assert f"delete-case-{case.id}" in admin_body
    assert f"delete-case-{shared_case.id}" not in admin_body

    superadmin_body = superadmin_client.get("/cases").content.decode()
    assert f"delete-case-{case.id}" in superadmin_body
    assert f"delete-case-{shared_case.id}" in superadmin_body


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
