"""Containment phase capture (PLAN-2026-11-10-containment-phase-capture).

Covers the service and the General-tab phase endpoint together: the action set,
the Contained status, the Page snapshot, the ledger entry, audit and taxonomy
deletion guard. The existing taxonomy suite covers vocabulary CRUD; this file
only exercises the case-level capture path.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from cases.containment import apply_containment_actions
from cases.models import Case, CaseContainmentAction, CaseStatus
from core.models import TaxonomyTerm
from core.taxonomy import TaxonomyError, delete_taxonomy_term
from identity.models import AuditLog, Organisation, User


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Containment Capture Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="capture-analyst",
        login="capture-analyst",
        password="hunter2-correct",
        email="capture-analyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="capture-analyst", password="hunter2-correct")
    return client


@pytest.fixture
def case(db: None, org: Organisation, analyst: User) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(
        title="Containment case",
        status=status,
        owner_org=org,
        assignee=analyst,
    )


def _url(case: Case) -> str:
    return reverse("ui-case-field-edit", kwargs={"case_id": case.number})


def _contain(case: Case, *, actions: list[str], other_text: str = "") -> None:
    result = apply_containment_actions(
        case,
        actor=case.assignee,
        action_codes=actions,
        other_text=other_text,
    )
    assert result.error is None


def test_containment_entry_applies_actions_status_page_ledger_and_audit(
    browser: Client, case: Case
) -> None:
    response = browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset", "other"],
            "other_text": "Disabled the mailbox",
        },
    )
    assert response.status_code == 302

    case.refresh_from_db()
    assert case.phase == "containment"
    assert case.status.value == "Contained"

    links = case.containment_action_links.select_related("term")
    assert set(links.values_list("term__code", flat=True)) == {"password_reset", "other"}
    assert links.get(term__code="other").other_text == "Disabled the mailbox"

    page = case.pages.get(category="containment")
    assert page.title.startswith("Containment actions — ")
    assert "Password reset" in page.content
    assert "Other: Disabled the mailbox" in page.content
    assert "Process Killed" not in page.content

    assert case.timeline_events.filter(kind="containment-actions").count() == 1
    audit = AuditLog.objects.get(action="case.containment_actions", target_id=str(case.id))
    assert audit.details["codes"] == ["other", "password_reset"]
    assert audit.details["other"] == "Disabled the mailbox"
    assert audit.details["page_id"] == str(page.id)


def test_missing_actions_is_rejected_without_writes(browser: Client, case: Case) -> None:
    response = browser.post(
        _url(case),
        {"field": "phase", "value": "containment", "tab": "general"},
    )
    assert response.status_code == 302

    case.refresh_from_db()
    assert case.phase != "containment"
    assert case.status.value == "New"
    assert not CaseContainmentAction.objects.filter(case=case).exists()
    assert not case.pages.filter(category="containment").exists()
    assert not AuditLog.objects.filter(
        action="case.containment_actions", target_id=str(case.id)
    ).exists()


@pytest.mark.parametrize("code", ["not_a_real_action"])
def test_unknown_action_is_rejected_without_writes(browser: Client, case: Case, code: str) -> None:
    response = browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": [code],
        },
    )
    assert response.status_code == 302

    case.refresh_from_db()
    assert case.phase != "containment"
    assert not CaseContainmentAction.objects.filter(case=case).exists()
    assert not case.pages.filter(category="containment").exists()


def test_hidden_action_is_rejected_without_writes(browser: Client, case: Case) -> None:
    TaxonomyTerm.objects.filter(category="containment_actions", code="process_killed").update(
        hidden=True
    )

    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["process_killed"],
        },
    )

    case.refresh_from_db()
    assert case.phase != "containment"
    assert not CaseContainmentAction.objects.filter(case=case).exists()


@pytest.mark.parametrize("other_text", ["", "   ", "x" * 501])
def test_other_action_requires_valid_text(browser: Client, case: Case, other_text: str) -> None:
    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["other"],
            "other_text": other_text,
        },
    )

    case.refresh_from_db()
    assert case.phase != "containment"
    assert not CaseContainmentAction.objects.filter(case=case).exists()
    assert not case.pages.filter(category="containment").exists()


def test_duplicate_codes_are_deduplicated(browser: Client, case: Case) -> None:
    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset", "password_reset"],
        },
    )

    case.refresh_from_db()
    assert case.containment_action_links.filter(term__code="password_reset").count() == 1
    page = case.pages.get(category="containment")
    assert page.content.count("Password reset") == 1


def test_missing_contained_status_writes_nothing(browser: Client, case: Case) -> None:
    CaseStatus.objects.filter(value="Contained").update(hidden=True)

    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset"],
        },
    )

    case.refresh_from_db()
    assert case.phase != "containment"
    assert not CaseContainmentAction.objects.filter(case=case).exists()
    assert not case.pages.filter(category="containment").exists()


def test_other_text_is_ignored_when_other_is_not_selected(browser: Client, case: Case) -> None:
    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset"],
            "other_text": "x" * 501,
        },
    )

    case.refresh_from_db()
    assert case.phase == "containment"
    assert not CaseContainmentAction.objects.filter(case=case, term__code="other").exists()
    page = case.pages.get(category="containment")
    assert "Other:" not in page.content


def test_closed_case_cannot_enter_containment(browser: Client, case: Case) -> None:
    closed, _ = CaseStatus.objects.get_or_create(
        value="Closed", defaults={"stage": "Closed", "order": 9}
    )
    case.status = closed
    case.save(update_fields=["status"])

    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset"],
        },
    )

    case.refresh_from_db()
    assert case.phase != "containment"
    assert not CaseContainmentAction.objects.filter(case=case).exists()


def test_non_containment_phase_creates_no_containment_records(browser: Client, case: Case) -> None:
    response = browser.post(
        _url(case),
        {"field": "phase", "value": "analysis", "tab": "general"},
    )
    assert response.status_code == 302

    case.refresh_from_db()
    assert case.phase == "analysis"
    assert not CaseContainmentAction.objects.filter(case=case).exists()
    assert not case.pages.filter(category="containment").exists()


def test_editing_actions_creates_updated_page_and_replaces_links(
    browser: Client, case: Case
) -> None:
    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset"],
        },
    )
    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["process_killed"],
        },
    )

    case.refresh_from_db()
    assert set(case.containment_action_links.values_list("term__code", flat=True)) == {
        "process_killed"
    }
    pages = case.pages.filter(category="containment").order_by("order")
    assert pages.count() == 2
    assert pages.last().title.startswith("Containment actions updated — ")
    assert "Process Killed" in pages.last().content
    assert "Password reset" not in pages.last().content


def test_leaving_and_reentering_creates_a_new_page_and_prechecks_old_actions(
    browser: Client, case: Case
) -> None:
    _contain(case, actions=["password_reset"])

    browser.post(_url(case), {"field": "phase", "value": "analysis", "tab": "general"})
    case.refresh_from_db()
    assert case.phase == "analysis"

    detail = browser.get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    assert detail.status_code == 200
    assert detail.context["containment_selected_codes"] == ["password_reset"]

    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset"],
        },
    )

    case.refresh_from_db()
    assert case.phase == "containment"
    pages = case.pages.filter(category="containment").order_by("order")
    assert pages.count() == 2
    assert pages.last().title.startswith("Containment actions — ")


def test_leaving_containment_does_not_revert_the_contained_status(
    browser: Client, case: Case
) -> None:
    _contain(case, actions=["password_reset"])
    browser.post(_url(case), {"field": "phase", "value": "analysis", "tab": "general"})

    case.refresh_from_db()
    assert case.phase == "analysis"
    assert case.status.value == "Contained"
    assert CaseContainmentAction.objects.filter(case=case).exists()


def test_same_selection_while_contained_does_not_duplicate_the_page(
    browser: Client, case: Case
) -> None:
    _contain(case, actions=["password_reset"])
    browser.post(
        _url(case),
        {
            "field": "phase",
            "value": "containment",
            "tab": "general",
            "containment_actions": ["password_reset"],
        },
    )

    case.refresh_from_db()
    assert case.pages.filter(category="containment").count() == 1
    assert case.timeline_events.filter(kind="containment-actions").count() == 1
    assert (
        AuditLog.objects.filter(action="case.containment_actions", target_id=str(case.id)).count()
        == 1
    )


def test_in_use_containment_term_cannot_be_deleted(browser: Client, case: Case) -> None:
    _contain(case, actions=["password_reset"])
    term = TaxonomyTerm.objects.get(category="containment_actions", code="password_reset")

    with pytest.raises(TaxonomyError):
        delete_taxonomy_term(term)
    assert TaxonomyTerm.objects.filter(pk=term.pk).exists()

    response = browser.get(reverse("ui-taxonomy"))
    assert response.status_code == 200
    terms = response.context["taxonomy_terms_by_category"]["containment_actions"]
    in_use = next(t for t in terms if t.code == "password_reset")
    assert in_use.in_use_count == 1


def test_phase_dialog_renders_containment_controls_and_preserves_no_js_fallback(
    browser: Client, case: Case
) -> None:
    response = browser.get(reverse("ui-case-detail", kwargs={"case_id": case.number}))
    assert response.status_code == 200
    body = response.content.decode()

    assert 'id="case-field-phase"' in body
    assert 'id="case-field-phase-value"' in body
    assert "data-containment-fieldset" in body
    assert 'name="containment_actions"' in body
    assert "Other detail" in body
    assert 'id="containment-other-text-case-field-phase"' in body
    assert "ui/case_phase.js" in body
    assert "containment_other_checkbox" not in body
    assert '<fieldset class="containment-fieldset" hidden' not in body
