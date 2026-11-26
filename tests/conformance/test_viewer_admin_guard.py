from __future__ import annotations

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from identity.models import Organisation, User
from identity.services import create_user, update_user


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="org-l2")


@pytest.fixture
def superadmin(db, org):
    return User.objects.create_superuser(
        username="sa-l2",
        email="sa@example.com",
        password="pw-test-long-enough",
        org=org,
        role="superAdmin",
    )


def _request(user):
    req = RequestFactory().get("/admin/")
    req.user = user
    return req


@pytest.mark.django_db
def test_service_rejects_create_viewer_with_staff(org, superadmin):
    with pytest.raises(ValidationError) as exc:
        create_user(
            username="viewer-staff",
            email="v@example.com",
            password="pw-test-long-enough",
            org=org,
            role="viewer",
            is_staff=True,
            actor=superadmin,
            request=_request(superadmin),
        )
    assert "viewer role cannot be combined" in str(exc.value)


@pytest.mark.django_db
def test_service_rejects_update_to_viewer_when_staff(org, superadmin):
    user = User.objects.create_user(
        username="staffy",
        email="s@example.com",
        password="pw-test-long-enough",
        org=org,
        role="analyst",
        is_staff=True,
    )
    with pytest.raises(ValidationError) as exc:
        update_user(user, role="viewer", actor=superadmin, request=_request(superadmin))
    assert "viewer role cannot be combined" in str(exc.value)


@pytest.mark.django_db
def test_service_rejects_granting_staff_to_viewer(org, superadmin):
    user = User.objects.create_user(
        username="viewer-only",
        email="v2@example.com",
        password="pw-test-long-enough",
        org=org,
        role="viewer",
    )
    with pytest.raises(ValidationError) as exc:
        update_user(user, is_staff=True, actor=superadmin, request=_request(superadmin))
    assert "viewer role cannot be combined" in str(exc.value)


@pytest.mark.django_db
def test_admin_site_denies_viewer_even_if_staff(org):
    viewer = User.objects.create_user(
        username="viewer-admin",
        email="va@example.com",
        password="pw-test-long-enough",
        org=org,
        role="viewer",
    )
    viewer.is_staff = True
    viewer.save(update_fields=["is_staff"])
    assert admin.site.has_permission(_request(viewer)) is False
