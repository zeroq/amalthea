"""Containment Actions taxonomy — seed, read exposure, page context and CRUD.

Scope is vocabulary management only; no case-level containment recording exists yet.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core import taxonomy_read
from core.models import TaxonomyTerm
from identity.models import AuditLog, Organisation, User

EXPECTED = [
    ("password_reset", "Password reset", 0),
    ("process_killed", "Process Killed", 1),
    ("asset_network_contained", "Asset network contained", 2),
    ("user_disabled", "User disabled", 3),
]


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Containment Taxonomy Org")


@pytest.fixture
def superadmin(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="containment-root",
        login="containment-root",
        password="hunter2-correct",
        email="containment-root@example.net",
        org=org,
        is_active=True,
        role="superAdmin",
    )


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="containment-analyst",
        login="containment-analyst",
        password="hunter2-correct",
        email="containment-analyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def sa_browser(superadmin: User) -> Client:
    client = Client()
    assert client.login(username="containment-root", password="hunter2-correct")
    return client


@pytest.fixture
def analyst_browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="containment-analyst", password="hunter2-correct")
    return client


def _seeded() -> list[tuple[str, str, int]]:
    return list(
        TaxonomyTerm.objects.filter(category="containment_actions")
        .order_by("order", "code")
        .values_list("code", "label", "order")
    )


def test_containment_actions_are_seeded(db: None) -> None:
    assert _seeded() == EXPECTED
    assert not TaxonomyTerm.objects.filter(
        category="containment_actions",
        protected=True,
    ).exists()
    assert not TaxonomyTerm.objects.filter(
        category="containment_actions",
        hidden=True,
    ).exists()
    assert not TaxonomyTerm.objects.filter(
        category="containment_actions",
        is_closing=True,
    ).exists()


def test_taxonomy_read_exposes_containment_actions(db: None) -> None:
    assert taxonomy_read.CATEGORY_CONTAINMENT_ACTIONS == "containment_actions"
    assert "containment_actions" in taxonomy_read.ALL_CATEGORIES
    assert taxonomy_read.choices("containment_actions") == tuple(
        (code, label) for code, label, _order in EXPECTED
    )

    hidden = TaxonomyTerm.objects.get(category="containment_actions", code="process_killed")
    hidden.hidden = True
    hidden.save(update_fields=["hidden", "updated_at"])

    assert ("process_killed", "Process Killed") not in taxonomy_read.choices("containment_actions")
    assert ("process_killed", "Process Killed") in taxonomy_read.choices(
        "containment_actions", include_hidden=True
    )


def test_taxonomy_page_groups_containment_actions(
    analyst_browser: Client,
) -> None:
    response = analyst_browser.get(reverse("ui-taxonomy"))
    assert response.status_code == 200
    terms = response.context["taxonomy_terms_by_category"]["containment_actions"]
    assert [(t.code, t.label) for t in terms] == [(code, label) for code, label, _o in EXPECTED]
    assert all(t.in_use_count == 0 for t in terms)


def test_superadmin_can_create_edit_and_delete_containment_term(
    sa_browser: Client,
) -> None:
    response = sa_browser.post(
        reverse("ui-taxonomy-term-create"),
        {
            "category": "containment_actions",
            "code": "mailbox_disabled",
            "label": "Mailbox disabled",
            "order": "4",
        },
    )
    assert response.status_code == 302
    assert response.url == reverse("ui-taxonomy")

    term = TaxonomyTerm.objects.get(category="containment_actions", code="mailbox_disabled")
    assert term.label == "Mailbox disabled"
    assert term.order == 4
    assert AuditLog.objects.filter(
        action="taxonomy.term.created",
        target_type="TaxonomyTerm",
        target_id=str(term.id),
    ).exists()

    response = sa_browser.post(
        reverse("ui-taxonomy-term-edit", kwargs={"term_id": term.id}),
        {"label": "Mailbox access revoked", "order": "5", "hidden": "on"},
    )
    assert response.status_code == 302
    term.refresh_from_db()
    assert term.code == "mailbox_disabled"
    assert term.label == "Mailbox access revoked"
    assert term.order == 5
    assert term.hidden is True
    assert AuditLog.objects.filter(
        action="taxonomy.term.updated",
        target_type="TaxonomyTerm",
        target_id=str(term.id),
    ).exists()

    response = sa_browser.post(
        reverse("ui-taxonomy-term-delete", kwargs={"term_id": term.id}),
    )
    assert response.status_code == 302
    assert not TaxonomyTerm.objects.filter(pk=term.id).exists()
    assert AuditLog.objects.filter(
        action="taxonomy.term.deleted",
        target_type="TaxonomyTerm",
        target_id=str(term.id),
    ).exists()


def test_non_superadmin_cannot_create_containment_term(analyst_browser: Client) -> None:
    response = analyst_browser.post(
        reverse("ui-taxonomy-term-create"),
        {
            "category": "containment_actions",
            "code": "forbidden_action",
            "label": "Forbidden action",
            "order": "9",
        },
    )
    assert response.status_code == 404
    assert not TaxonomyTerm.objects.filter(code="forbidden_action").exists()
