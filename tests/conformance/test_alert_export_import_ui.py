"""UI conformance for TODO §19 — alert export/import (AIMP-6/7)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User

pytestmark = pytest.mark.django_db


def _admin(org: Organisation) -> User:
    return User.objects.create(
        login="admin", username="admin", email="admin@example.net", org=org, role="admin"
    )


def _alert(org: Organisation) -> Alert:
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        title="Phish",
        type="phishing",
        source="siem",
        source_ref="ui-ref",
        status=status,
        owner_org=org,
    )


def _login(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def test_alert_detail_has_export_button(analyst: User) -> None:
    alert = _alert(analyst.org)
    response = _login(analyst).get(reverse("ui-alert-detail", args=[alert.id]))
    assert response.status_code == 200
    assert reverse("ui-alert-export", args=[alert.id]).encode() in response.content
    assert b"Export JSON" in response.content


def test_admin_alert_list_has_import_modal(analyst: User) -> None:
    admin = _admin(analyst.org)
    response = _login(admin).get(reverse("ui-alert-list"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'data-modal-open="import-alert-modal"' in body
    assert 'id="import-alert-modal"' in body
    assert 'enctype="multipart/form-data"' in body
    assert 'id="import-alert-form"' in body
    assert f'action="{reverse("ui-alert-import")}"' in body
    assert 'id="alert-import-file"' in body
    assert 'name="file"' in body
    assert "{#" not in body


def test_analyst_alert_list_has_no_import_modal(analyst: User) -> None:
    response = _login(analyst).get(reverse("ui-alert-list"))
    body = response.content.decode()
    assert response.status_code == 200
    assert "import-alert-modal" not in body
    assert reverse("ui-alert-import") not in body


def test_standalone_import_page_renders(analyst: User) -> None:
    response = _login(_admin(analyst.org)).get(reverse("ui-alert-import"))
    body = response.content.decode()
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in body
    assert 'name="file"' in body
