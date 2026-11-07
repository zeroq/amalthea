"""Conformance tests for the Alert detail Tags metadata row."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import Tag
from identity.models import User

pytestmark = pytest.mark.django_db


def _tags_block(html: str) -> str:
    """Extract the contents of the Alert Tags <dd id="alert-tags">...</dd> block.

    Returns the inner HTML of the block so callers can assert on it directly,
    avoiding false positives from searching the whole document.
    """
    start = '<dd id="alert-tags">'
    end = "</dd>"
    idx = html.find(start)
    assert idx != -1, "Alert tags block not found in response HTML"
    body = html[idx + len(start) :]
    body = body[: body.find(end)]
    return body


def test_alert_detail_renders_tags_block(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """GET alert detail returns 200 and renders exactly one id="alert-tags"."""
    alert = make_alert()
    user = make_user(role="analyst")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert response.status_code == 200
    html = response.content.decode()
    # Exactly one Tags block should be present
    assert html.count('id="alert-tags"') == 1


def test_alert_detail_renders_two_tags_alphabetically(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """Two assigned tags appear inside the Tags block in alphabetical order."""
    alert = make_alert()
    # Create tags zeta then alpha; the template orders by name ASC
    tag_zeta = Tag.objects.create(name="zeta")
    tag_alpha = Tag.objects.create(name="alpha")
    alert.tags.add(tag_zeta, tag_alpha)

    user = make_user(role="analyst")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert response.status_code == 200
    html = response.content.decode()
    block = _tags_block(html)
    # Alpha should appear before zeta (alphabetical order)
    assert "alpha" in block
    assert "zeta" in block
    # Alpha must come before zeta in the block text
    assert block.index("alpha") < block.index("zeta")


def test_alert_detail_empty_state_no_tags(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """Empty state renders No tags yet. for an untagged alert."""
    alert = make_alert()

    user = make_user(role="analyst")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert response.status_code == 200
    html = response.content.decode()
    block = _tags_block(html)
    assert "No tags yet." in block


def test_viewer_see_readonly_tags_no_add_card(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """Viewer-role user can still see the read-only tags and does not get the Add tags card."""
    alert = make_alert()
    tag = Tag.objects.create(name="secret")
    alert.tags.add(tag)

    # Create a viewer user (role=viewer, not analyst)
    viewer = User.objects.create(
        login="viewer-alpha",
        username="viewer-alpha",
        email="viewer-alpha@example.net",
        org=default_org,
        role="viewer",
        is_active=True,
    )

    client = Client()
    client.force_login(viewer)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert response.status_code == 200
    html = response.content.decode()
    block = _tags_block(html)
    # Viewer should see the tag read-only
    assert "secret" in block
    # Count tag name appearances — should be exactly once
    assert block.count("secret") == 1
    # Viewer should NOT see the "Add tags" mutation card
    assert "Add tags</h2>" not in html
    # Also verify the block has no pill removal form controls
    assert 'href="' not in block  # no removal links
    assert "inline-form" not in block  # no removal forms


def test_alert_detail_tag_colour_valid_hex_renders_swatch(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """Valid hex colour on a tag renders a .tag-dot swatch with background-colour.

    Tests all four documented forms: #RGB, #RGBA, #RRGGBB, #RRGGBBAA.
    """
    alert = make_alert()
    # Test #RGB (3 digits)
    tag_rgb = Tag.objects.create(name="red", colour="#f00")
    alert.tags.add(tag_rgb)
    user = make_user(role="analyst")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert response.status_code == 200
    html = response.content.decode()
    block = _tags_block(html)
    assert "tag-dot" in block
    assert "background-color: #f00" in block

    # Test #RGBA (4 digits)
    tag_rgba = Tag.objects.create(name="transitive", colour="#ff000080")
    alert.tags.add(tag_rgba)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    html = response.content.decode()
    block = _tags_block(html)
    assert "tag-dot" in block
    assert "background-color: #ff000080" in block

    # Test #RRGGBB (6 digits)
    tag_hex = Tag.objects.create(name="orange", colour="#ffa500")
    alert.tags.add(tag_hex)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    html = response.content.decode()
    block = _tags_block(html)
    assert "tag-dot" in block
    assert "background-color: #ffa500" in block

    # Test #RRGGBBAA (8 digits)
    tag_aa = Tag.objects.create(name="purple-alpha", colour="#800080ff")
    alert.tags.add(tag_aa)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    html = response.content.decode()
    block = _tags_block(html)
    assert "tag-dot" in block
    assert "background-color: #800080ff" in block


def test_alert_detail_tag_invalid_colour_no_swatch(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """Invalid colour string does not render a .tag-dot or inline style payload."""
    alert = make_alert()
    # Create a tag with an invalid colour value
    tag = Tag.objects.create(name="cloudy", colour="not-a-colour")
    alert.tags.add(tag)

    user = make_user(role="analyst")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert response.status_code == 200
    html = response.content.decode()
    block = _tags_block(html)
    # No tag-dot should appear for an invalid colour
    assert "tag-dot" not in block


def test_alert_detail_remove_form_submit_type(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """Mutating user sees remove form with type='submit' button and correct action."""
    alert = make_alert()
    tag = Tag.objects.create(name="temp")
    alert.tags.add(tag)

    user = make_user(role="analyst")
    client = Client()
    client.force_login(user)
    response = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert response.status_code == 200
    html = response.content.decode()
    block = _tags_block(html)
    # Remove form should have method=post and correct action path
    assert 'method="post"' in block
    assert "/tag/remove" in block
    # Button should be type="submit", not type="button"
    assert 'type="submit"' in block
    assert 'type="button"' not in block
    # Hidden tag input should be present
    assert 'name="tag" value="temp"' in block


def test_alert_detail_remove_tag_post_deletes_link(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """POST removal deletes the AlertTagLink, redirects to detail, and tag disappears."""
    alert = make_alert()
    tag = Tag.objects.create(name="to-go")
    alert.tags.add(tag)

    user = make_user(role="analyst")
    client = Client()
    client.force_login(user)

    # Submit remove form
    remove_url = reverse("ui-alert-tag-remove", kwargs={"alert_id": alert.id})
    response = client.post(remove_url, {"tag": "to-go"})
    assert response.status_code == 302
    assert response.url == reverse("ui-alert-detail", kwargs={"alert_id": alert.id})

    # Verify tag is gone
    detail = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert detail.status_code == 200
    html = detail.content.decode()
    block = _tags_block(html)
    assert "to-go" not in block
    assert "No tags yet." in block


def test_alert_detail_viewer_cannot_remove_tag(
    make_alert,
    make_user,
    default_org,
    db,
) -> None:
    """Viewer POST to remove endpoint gets 404/405 and the tag link remains."""
    alert = make_alert()
    tag = Tag.objects.create(name="secret")
    alert.tags.add(tag)

    # Create a viewer user
    viewer = User.objects.create(
        login="viewer-beta",
        username="viewer-beta",
        email="viewer-beta@example.net",
        org=default_org,
        role="viewer",
        is_active=True,
    )

    client = Client()
    client.force_login(viewer)

    # Viewer POST to remove should be denied
    remove_url = reverse("ui-alert-tag-remove", kwargs={"alert_id": alert.id})
    response = client.post(remove_url, {"tag": "secret"})
    assert response.status_code in (404, 405)

    # Tag should still be present on the detail page
    detail = client.get(reverse("ui-alert-detail", kwargs={"alert_id": alert.id}))
    assert detail.status_code == 200
    html = detail.content.decode()
    block = _tags_block(html)
    assert "secret" in block
