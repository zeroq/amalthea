"""UI tag editing — admin-only modal, validation and the removed detail POST bypass."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import Tag
from identity.models import Organisation, User

pytestmark = pytest.mark.django_db


def _client(org: Organisation, username: str, role: str) -> Client:
    client = Client()
    client.force_login(User.objects.create(username=username, org=org, role=role))
    return client


@pytest.fixture
def admin_browser(default_org: Organisation) -> Client:
    return _client(default_org, "tag-edit-admin", "admin")


@pytest.fixture
def analyst_browser(default_org: Organisation) -> Client:
    return _client(default_org, "tag-edit-analyst", "analyst")


def test_admin_edits_tag_from_detail_and_detail_displays_metadata(
    admin_browser: Client,
) -> None:
    tag = Tag.objects.create(name="phishing", colour="#111111", description="old")
    detail_url = reverse("ui-tag-detail", kwargs={"tag_id": tag.id})
    edit_url = reverse("ui-tag-edit", kwargs={"tag_id": tag.id})

    html = admin_browser.get(detail_url).content.decode()
    assert edit_url in html
    assert f'id="edit-tag-{tag.id}"' in html

    response = admin_browser.post(
        edit_url,
        {
            "name": "  phishing-updated  ",
            "colour": "x" * 30,
            "description": "  new description  ",
            "return_to": "detail",
        },
    )

    assert response.status_code == 302
    assert response.url == detail_url
    tag.refresh_from_db()
    assert tag.name == "phishing-updated"
    assert tag.colour == "x" * 20
    assert tag.description == "new description"
    detail_html = admin_browser.get(detail_url).content.decode()
    assert "phishing-updated" in detail_html
    assert "new description" in detail_html


def test_list_edit_modal_is_admin_only(admin_browser: Client, analyst_browser: Client) -> None:
    tag = Tag.objects.create(name="phishing")
    list_url = reverse("ui-tag-list")
    edit_url = reverse("ui-tag-edit", kwargs={"tag_id": tag.id})

    admin_html = admin_browser.get(list_url).content.decode()
    assert edit_url in admin_html
    assert f'data-modal-open="edit-tag-{tag.id}"' in admin_html

    analyst_html = analyst_browser.get(list_url).content.decode()
    assert edit_url not in analyst_html
    assert f'data-modal-open="edit-tag-{tag.id}"' not in analyst_html

    response = analyst_browser.post(edit_url, {"name": "hijacked"})
    assert response.status_code == 404
    tag.refresh_from_db()
    assert tag.name == "phishing"


def test_create_form_accepts_colour_and_description(analyst_browser: Client) -> None:
    create_url = reverse("ui-tag-create")
    assert 'name="colour"' in analyst_browser.get(create_url).content.decode()
    assert 'name="description"' in analyst_browser.get(create_url).content.decode()

    response = analyst_browser.post(
        create_url,
        {
            "name": "  created-tag  ",
            "colour": "x" * 30,
            "description": "  created description  ",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("ui-tag-list")
    tag = Tag.objects.get(name="created-tag")
    assert tag.colour == "x" * 20
    assert tag.description == "created description"


def test_edit_validation_rejects_blank_and_duplicate_names(admin_browser: Client) -> None:
    tag = Tag.objects.create(name="phishing")
    Tag.objects.create(name="taken")
    edit_url = reverse("ui-tag-edit", kwargs={"tag_id": tag.id})
    list_url = reverse("ui-tag-list")

    response = admin_browser.post(edit_url, {"name": "   "}, follow=True)
    assert response.redirect_chain[-1][0] == list_url
    assert "Name is required." in response.content.decode()
    tag.refresh_from_db()
    assert tag.name == "phishing"

    response = admin_browser.post(edit_url, {"name": "taken"}, follow=True)
    assert response.redirect_chain[-1][0] == list_url
    assert "already exists" in response.content.decode()
    tag.refresh_from_db()
    assert tag.name == "phishing"


def test_old_detail_post_cannot_bypass_the_admin_only_edit(admin_browser: Client) -> None:
    tag = Tag.objects.create(name="phishing")
    detail_url = reverse("ui-tag-detail", kwargs={"tag_id": tag.id})

    response = admin_browser.post(detail_url, {"name": "hijacked"})

    assert response.status_code == 405
    tag.refresh_from_db()
    assert tag.name == "phishing"
