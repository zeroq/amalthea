"""Regression coverage for the P1 alert-tag issues found in the 2026-11-05 review.

The alert-tag feature shipped without conformance tests. These tests pin the fixes that were
needed to make the basic UI/API paths safe: empty submissions, linked alerts, comma-separated
names, the `Tag.name` column bound, and duplicate names in the API payload.

The P1 alert-tag parity changes (2026-11-24) switch the UI POST fields from
`existing_tag_ids` / `new_tag_names` to `tags` / `new_tags`, matching the case-detail
pattern. These tests reflect that contract.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus, AlertTagLink
from cases.models import Case, CaseStatus, Tag
from identity.models import Organisation, User
from ui.pagination import PICKER_PAGE_SIZE


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


def _alert_detail_url(alert: Alert) -> str:
    return reverse("ui-alert-detail", kwargs={"alert_id": alert.id})


# ── Picker parity tests ────────────────────────────────────────────────


def test_alert_detail_picker_lists_global_tags(client, alert, org):
    """Alert picker shows global Tag catalog, excluding tags already on the alert."""
    # Create some global tags and attach first to the alert
    tag_gt1 = Tag.objects.create(name="global-tag-1")
    Tag.objects.create(name="global-tag-2")
    Tag.objects.create(name="global-tag-3")
    AlertTagLink.objects.create(alert=alert, tag=tag_gt1)

    response = client.get(_alert_detail_url(alert))
    assert response.status_code == 200
    ctx = response.context
    # Available tags should be global, excluding the one already on the alert
    available = ctx["available_tags"]
    available_names = [t.name for t in available]
    assert "global-tag-1" not in available_names  # excluded (already on alert)
    assert "global-tag-2" in available_names
    assert "global-tag-3" in available_names
    # Should be capped at PICKER_PAGE_SIZE
    assert len(available) <= PICKER_PAGE_SIZE
    # Truncation flag should be set if more candidates exist
    assert "available_tags_truncated" in ctx


def test_alert_detail_picker_excludes_current_tags(client, alert, org):
    """Tags already on the alert are absent from the available list."""
    tag = Tag.objects.create(name="existing-tag")
    AlertTagLink.objects.create(alert=alert, tag=tag)

    response = client.get(_alert_detail_url(alert))
    assert response.status_code == 200
    available_names = [t.name for t in response.context["available_tags"]]
    assert "existing-tag" not in available_names


def test_alert_detail_picker_caps_at_picker_page_size(client, alert, org):
    """More than PICKER_PAGE_SIZE candidates sets available_tags_truncated=True."""
    # Create PICKER_PAGE_SIZE + 1 tags attached to a case (global catalog)
    case_status = CaseStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 0}
    )[0]
    case = Case.objects.create(title="Tag Source Case", status=case_status, owner_org=org)
    for index in range(PICKER_PAGE_SIZE + 1):
        tag = Tag.objects.create(name=f"picker-{index:04d}")
        case.tags.add(tag)

    response = client.get(_alert_detail_url(alert))
    assert response.status_code == 200
    assert len(response.context["available_tags"]) == PICKER_PAGE_SIZE
    assert response.context["available_tags_truncated"] is True
    assert "alert_tags_total" not in response.context


# ── POST handling tests (new field names) ─────────────────────────────


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

    detail = client.get(_alert_detail_url(alert))
    assert detail.status_code == 200
    assert b"Add tags</h2>" in detail.content

    # Select existing tag by name via POST
    _ = Tag.objects.create(name="selectable-tag")

    response = client.post(_tag_add_url(alert), {"tags": ["selectable-tag"], "new_tags": ""})
    assert response.status_code == 302
    assert AlertTagLink.objects.filter(alert=alert, tag__name="selectable-tag").exists()


def test_comma_separated_new_tag_names(client, alert):
    """Comma-separated new tag names become separate tags."""
    response = client.post(_tag_add_url(alert), {"tags": [], "new_tags": "alpha, beta"})

    assert response.status_code == 302
    assert set(Tag.objects.values_list("name", flat=True)) == {"alpha", "beta"}
    assert AlertTagLink.objects.filter(alert=alert).count() == 2


def test_new_tag_name_is_clamped_to_tag_column_limit(client, alert):
    response = client.post(_tag_add_url(alert), {"tags": [], "new_tags": "x" * 150})

    assert response.status_code == 302
    tag = Tag.objects.get()
    assert len(tag.name) == 100
    assert AlertTagLink.objects.filter(alert=alert, tag=tag).exists()


def test_selecting_existing_tag_by_name_attaches_it(client, alert):
    """Selecting an existing tag by name attaches it to the alert."""
    _ = Tag.objects.create(name="my-existing-tag")

    response = client.post(_tag_add_url(alert), {"tags": ["my-existing-tag"], "new_tags": ""})
    assert response.status_code == 302
    assert AlertTagLink.objects.filter(alert=alert, tag__name="my-existing-tag").exists()


def test_duplicate_names_are_deduplicated(client, alert):
    """Duplicate names in tags/new_tags produce one link."""
    # Send the same tag name twice via the `tags` list
    response = client.post(
        _tag_add_url(alert), {"tags": ["dedup-tag", "dedup-tag"], "new_tags": ""}
    )

    assert response.status_code == 302
    assert AlertTagLink.objects.filter(alert=alert, tag__name="dedup-tag").count() == 1


def test_api_duplicate_tag_names_are_deduplicated(api, alert):
    response = api.post(
        reverse("alert-tag-add", kwargs={"alert_id": str(alert.id)}),
        {"tags": ["duplicate", "duplicate"]},
        format="json",
    )

    assert response.status_code == 201
    assert Tag.objects.filter(name="duplicate").count() == 1
    assert AlertTagLink.objects.filter(alert=alert, tag__name="duplicate").count() == 1


@pytest.mark.parametrize("body", [[], ["tag"]])
def test_api_tag_add_rejects_non_object_body_with_400(api, alert, body):
    response = api.post(
        reverse("alert-tag-add", kwargs={"alert_id": str(alert.id)}),
        body,
        format="json",
    )

    assert response.status_code == 400
    assert response.json().get("message") == "Request body must be a JSON object"


# ── Legacy UUID field rejection ───────────────────────────────────────


def test_legacy_uuid_field_ignored_rejected(client, alert):
    """Legacy UUID field names are ignored/rejected; only name-based fields work."""
    # Post with existing_tag_ids (legacy) and no new_tags should result in empty submission
    response = client.post(
        _tag_add_url(alert), {"existing_tag_ids": ["some-uuid"], "new_tag_names": ""}
    )

    assert response.status_code == 302
    assert response.url == reverse("ui-alert-detail", kwargs={"alert_id": alert.id})
    # No tags should be added since existing_tag_ids are ignored and new_tag_names is empty
    assert AlertTagLink.objects.filter(alert=alert).count() == 0  # alert had none initially
