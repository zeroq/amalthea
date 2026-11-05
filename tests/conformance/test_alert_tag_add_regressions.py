"""Regression coverage for the P1 alert-tag issues found in the 2026-11-05 review.

The alert-tag feature shipped without conformance tests. These tests pin the fixes that were
needed to make the basic UI/API paths safe: empty submissions, linked alerts, comma-separated
names, the `Tag.name` column bound, and duplicate names in the API payload.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus, AlertTagLink
from cases.models import Case, CaseStatus, Tag
from identity.models import Organisation, User


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Alert Tag Regression Org")


@pytest.fixture
def analyst(org):
    return User.objects.create(
        login="tag-analyst",
        username="tag-analyst",
        email="tag-analyst@example.net",
        org=org,
        is_active=True,
    )


@pytest.fixture
def client(analyst):
    c = Client()
    c.force_login(analyst)
    return c


@pytest.fixture
def api(analyst):
    c = APIClient()
    c.force_authenticate(user=analyst)
    return c


@pytest.fixture
def alert_status(db):
    return AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 0})[0]


@pytest.fixture
def alert(org, alert_status):
    return Alert.objects.create(
        title="Tag Regression Alert",
        type="phishing",
        source="mock",
        source_ref="tag-regression-1",
        status=alert_status,
        owner_org=org,
    )


def _tag_add_url(alert: Alert) -> str:
    return reverse("ui-alert-tag-add", kwargs={"alert_id": alert.id})


def test_empty_submission_redirects_instead_of_500(client, alert):
    response = client.post(_tag_add_url(alert), {})

    assert response.status_code == 302
    assert response.url == reverse("ui-alert-detail", kwargs={"alert_id": alert.id})


def test_linked_alert_still_offers_add_tags_and_can_tag(client, alert, org):
    case_status = CaseStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 0}
    )[0]
    case = Case.objects.create(title="Linked Case", status=case_status, owner_org=org)
    alert.case = case
    alert.save(update_fields=["case"])

    detail = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert detail.status_code == 200
    assert b"Add tags</h2>" in detail.content

    response = client.post(_tag_add_url(alert), {"new_tag_names": "linked-tag"})
    assert response.status_code == 302
    assert AlertTagLink.objects.filter(alert=alert, tag__name="linked-tag").exists()


def test_comma_separated_names_become_separate_tags(client, alert):
    response = client.post(_tag_add_url(alert), {"new_tag_names": "alpha, beta"})

    assert response.status_code == 302
    assert set(Tag.objects.values_list("name", flat=True)) == {"alpha", "beta"}
    assert AlertTagLink.objects.filter(alert=alert).count() == 2


def test_new_tag_name_is_clamped_to_tag_column_limit(client, alert):
    response = client.post(_tag_add_url(alert), {"new_tag_names": "x" * 150})

    assert response.status_code == 302
    tag = Tag.objects.get()
    assert len(tag.name) == 100
    assert AlertTagLink.objects.filter(alert=alert, tag=tag).exists()


def test_api_duplicate_tag_names_are_deduplicated(api, alert):
    response = api.post(
        reverse("alert-tag-add", kwargs={"alert_id": str(alert.id)}),
        {"tags": ["duplicate", "duplicate"]},
        format="json",
    )

    assert response.status_code == 201
    assert Tag.objects.filter(name="duplicate").count() == 1
    assert AlertTagLink.objects.filter(alert=alert, tag__name="duplicate").count() == 1
