from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from identity.models import AuditLog, Organisation, User


@pytest.fixture
def superadmin(db):
    org = Organisation.objects.create(name="SA Org")
    return User.objects.create_user(
        username="sa",
        password="pw-test-12345!",
        email="sa@example.net",
        org=org,
        role="superAdmin",
    )


@pytest.fixture
def admin(db):
    org = Organisation.objects.create(name="Admin Org")
    return User.objects.create_user(
        username="adm",
        password="pw-test-12345!",
        email="adm@example.net",
        org=org,
        role="admin",
    )


@pytest.mark.django_db
def test_audit_log_requires_superadmin(superadmin, admin):
    c = Client()
    c.force_login(admin)
    r = c.get(reverse("ui-settings-audit-log"))
    assert r.status_code == 404

    c2 = Client()
    c2.force_login(superadmin)
    r2 = c2.get(reverse("ui-settings-audit-log"))
    assert r2.status_code == 200
    assert "Audit log" in r2.content.decode()


@pytest.mark.django_db
def test_audit_log_renders_entries(superadmin):
    AuditLog.objects.create(
        actor=superadmin,
        actor_org=superadmin.org,
        target_org=superadmin.org,
        action="organisation.updated",
        target_type="Organisation",
        target_id=str(superadmin.org_id),
        details={"changed": ["name"]},
        ip="127.0.0.1",
    )
    c = Client()
    c.force_login(superadmin)
    r = c.get(reverse("ui-settings-audit-log"))
    assert r.status_code == 200
    body = r.content.decode()
    assert "organisation.updated" in body
    assert "Organisation" in body
    assert "127.0.0.1" in body
