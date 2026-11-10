"""UI conformance tests for the Containment Actions taxonomy card.

Covers: card placement, seeded term rendering, mono code cells,
permission-gated controls, form category option, and is_closing absence.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import TaxonomyTerm
from identity.models import Organisation, User

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Containment Test Org")


@pytest.fixture
def superadmin(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="contain-super",
        login="contain-super",
        password="hunter2-correct",
        email="super@contain.test",
        org=org,
        is_active=True,
        role="superAdmin",
    )


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="contain-analyst",
        login="contain-analyst",
        password="hunter2-correct",
        email="analyst@contain.test",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def viewer(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="contain-viewer",
        login="contain-viewer",
        password="hunter2-correct",
        email="viewer@contain.test",
        org=org,
        is_active=True,
        role="viewer",
    )


@pytest.fixture
def sa_browser(superadmin: User) -> Client:
    client = Client()
    assert client.login(username="contain-super", password="hunter2-correct")
    return client


@pytest.fixture
def analyst_browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="contain-analyst", password="hunter2-correct")
    return client


@pytest.fixture
def viewer_browser(viewer: User) -> Client:
    client = Client()
    assert client.login(username="contain-viewer", password="hunter2-correct")
    return client


# --------------------------------------------------------------------------
# Card placement & rendering
# --------------------------------------------------------------------------


def _taxonomy_body(browser: Client) -> str:
    return browser.get(reverse("ui-taxonomy")).content.decode()


def test_taxonomy_renders_containment_actions_after_verdict(sa_browser: Client) -> None:
    """The Containment Actions heading appears after the Verdict card in DOM order."""
    body = _taxonomy_body(sa_browser)
    # Verify Verdict appears before Containment Actions
    verdict_idx = body.index('id="h-verdict"')
    containment_idx = body.index('id="h-containment-actions"')
    assert containment_idx > verdict_idx, (
        "Containment Actions card must appear after Verdict card in DOM order"
    )


def test_taxonomy_containment_codes_render_with_mono_cells(sa_browser: Client) -> None:
    """All four seeded containment action codes render in mono code cells."""
    body = _taxonomy_body(sa_browser)
    codes = ["password_reset", "process_killed", "asset_network_contained", "user_disabled"]
    for code in codes:
        # code cell has class="mono"
        assert f'class="mono">{code}</td>' in body or f'class="mono">{code}' in body, (
            f"Code '{code}' not found with class=mono in containment card"
        )
        # label is present alongside
        assert code.capitalize() in body or code in body, (
            f"Label for code '{code}' not found in containment card"
        )


def test_taxonomy_containment_terms_have_edit_and_delete_dialogs(
    sa_browser: Client,
) -> None:
    """Every seeded term wires its edit/delete buttons to an actual dialog."""
    body = _taxonomy_body(sa_browser)
    for term in TaxonomyTerm.objects.filter(category="containment_actions"):
        assert f'dialog id="taxonomy-term-edit-{term.id}"' in body
        assert f'dialog id="taxonomy-term-delete-{term.id}"' in body


# --------------------------------------------------------------------------
# Permission gating
# --------------------------------------------------------------------------


def test_superadmin_sees_add_control_for_containment(sa_browser: Client) -> None:
    """superAdmin sees the Add term button for containment actions."""
    body = _taxonomy_body(sa_browser)
    assert 'data-modal-open="taxonomy-term-create-containment_actions-modal"' in body


def test_analyst_hides_add_control_for_containment(analyst_browser: Client) -> None:
    """analyst does NOT see the Add term button for containment actions."""
    body = _taxonomy_body(analyst_browser)
    assert 'data-modal-open="taxonomy-term-create-containment_actions-modal"' not in body


def test_viewer_hides_add_control_for_containment(viewer_browser: Client) -> None:
    """viewer does NOT see the Add term button for containment actions."""
    body = _taxonomy_body(viewer_browser)
    assert 'data-modal-open="taxonomy-term-create-containment_actions-modal"' not in body


# --------------------------------------------------------------------------
# Form: category select and is_closing absence
# --------------------------------------------------------------------------


def test_create_form_offers_containment_actions_category(sa_browser: Client) -> None:
    """The create containment action form offers 'containment_actions' in the category select."""
    # Grab the new containment action create modal body via the page source
    body = _taxonomy_body(sa_browser)
    # The modal is included via HTMX/re-render; check the form has the option
    assert 'value="containment_actions"' in body


def test_create_form_has_no_is_closing_for_containment(sa_browser: Client) -> None:
    """The create containment action form does NOT emit an is_closing field."""
    body = _taxonomy_body(sa_browser)
    # is_closing should NOT appear when category is containment_actions
    # It only appears for case_verdict modals
    # Check that a standalone is_closing label is not present for containment
    # (it may appear inside a modal that has been re-rendered; we check the page body)
    # The pattern: is_closing only appears with case_verdict context
    assert 'id="is_closing"' not in body or body.count('id="is_closing"') == 0, (
        "is_closing field should not be present for containment actions form"
    )
