"""General-tab verdict, tags and the enforced close flow (PLAN-2026-10-17 §12.8, Phase F).

Covers the frontend half of the wave: the Taxonomy page's Verdict card, the General tab's inline
edit affordances and Tags row, the whitelist on `ui-case-field-edit`, and the close dialog's
enforcement path. The *rules* the applier enforces live in `test_case_verdict_close.py`; this file
asserts the rendered wiring the templates depend on (control ids, form actions, select values)
and the UI endpoints' own 404/denial shapes — the two halves are built independently, and only the
rendered contract binds them.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus, Tag
from identity.models import Organisation, User

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Verdict Org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="Verdict Other Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="v-analyst",
        login="v-analyst",
        password="hunter2-correct",
        email="v-analyst@example.net",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def outsider(db: None, other_org: Organisation) -> User:
    return User.objects.create_user(
        username="v-outsider",
        login="v-outsider",
        password="hunter2-correct",
        email="v-outsider@example.net",
        org=other_org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def viewer_user(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="v-viewer",
        login="v-viewer",
        password="hunter2-correct",
        email="v-viewer@example.net",
        org=org,
        is_active=True,
        role="viewer",
    )


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="v-analyst", password="hunter2-correct")
    return client


@pytest.fixture
def admin_browser(db: None, org: Organisation) -> Client:
    """An admin, because the Classification edit icon is gated on `is_admin` (Q-D5)."""
    User.objects.create_user(
        username="v-admin",
        login="v-admin",
        password="hunter2-correct",
        email="v-admin@example.net",
        org=org,
        is_active=True,
        role="admin",
    )
    client = Client()
    assert client.login(username="v-admin", password="hunter2-correct")
    return client


@pytest.fixture
def outsider_browser(outsider: User) -> Client:
    client = Client()
    assert client.login(username="v-outsider", password="hunter2-correct")
    return client


@pytest.fixture
def viewer_browser(viewer_user: User) -> Client:
    client = Client()
    assert client.login(username="v-viewer", password="hunter2-correct")
    return client


@pytest.fixture
def case(db: None, org: Organisation, analyst: User) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(title="Verdict case", status=status, owner_org=org, assignee=analyst)


def _alert(case: Case, org: Organisation, status_value: str, ref: str) -> Alert:
    status, _ = AlertStatus.objects.get_or_create(
        value=status_value, defaults={"stage": status_value, "order": 1}
    )
    return Alert.objects.create(
        type="phishing",
        source="verdict-source",
        source_ref=ref,
        title=f"Alert {ref}",
        owner_org=org,
        status=status,
        case=case,
    )


# --------------------------------------------------------------------------
# Taxonomy (plan §9.4)
# --------------------------------------------------------------------------


def test_taxonomy_renders_the_verdict_card(browser: Client) -> None:
    """The Verdict card lists the token in the code cell and the human label beside it."""
    response = browser.get(reverse("ui-taxonomy"))
    assert response.status_code == 200
    body = response.content.decode()

    assert "Verdict" in body
    # Tokens (code) and labels (prose), both present.
    assert ">undefined<" in body
    assert "Undefined" in body
    assert "true_positive" in body
    assert "True Positive" in body
    assert "false_positive" in body
    assert "False Positive" in body


def test_taxonomy_verdict_card_uses_the_mono_code_cell(browser: Client) -> None:
    """Code-valued cells keep `class="mono"` (TODO §12.6 precedent)."""
    body = browser.get(reverse("ui-taxonomy")).content.decode()
    assert '<td class="mono">undefined</td>' in body
    assert '<td class="mono">true_positive</td>' in body


# --------------------------------------------------------------------------
# General tab rendering (plan §9.1)
# --------------------------------------------------------------------------


def _general(browser: Client, case: Case) -> str:
    return browser.get(
        reverse("ui-case-detail", kwargs={"case_id": case.number}), {"tab": "general"}
    ).content.decode()


def test_the_general_tab_renders_edit_icons_for_every_inline_field(
    browser: Client, case: Case
) -> None:
    body = _general(browser, case)
    # Classification is admin-gated, so it is not in the analyst's set (asserted separately).
    for slug in ("severity", "scope", "case_type", "phase", "verdict"):
        assert f'data-modal-open="case-field-{slug}"' in body, slug
        assert f'dialog id="case-field-{slug}"' in body, slug
    assert 'data-modal-open="case-field-data_classification"' not in body
    assert 'dialog id="case-field-data_classification"' not in body


def test_an_admin_sees_the_classification_edit_icon(admin_browser: Client, case: Case) -> None:
    body = _general(admin_browser, case)
    assert 'data-modal-open="case-field-data_classification"' in body
    assert 'dialog id="case-field-data_classification"' in body


def test_the_general_tab_renders_a_verdict_row(browser: Client, case: Case) -> None:
    body = _general(browser, case)
    # The row label and the value driven by `verdict_choices` (the token's human label).
    assert "<dt>Verdict</dt>" in body
    assert "Undefined" in body


def test_the_general_tab_always_renders_the_tags_row(browser: Client, case: Case) -> None:
    """Empty case: the row is still there, with an empty-state hint and the Add-tag button."""
    body = _general(browser, case)
    assert "No tags yet." in body
    assert 'data-modal-open="case-tag-add-modal"' in body
    assert 'dialog id="case-tag-add-modal"' in body


def test_the_tags_row_lists_tags_with_a_remove_control(browser: Client, case: Case) -> None:
    tag, _ = Tag.objects.get_or_create(name="phishing")
    case.tags.add(tag)
    body = _general(browser, case)

    assert "phishing" in body
    assert f'action="{reverse("ui-case-tag-remove", kwargs={"case_id": case.number})}"' in body
    assert 'name="tag" value="phishing"' in body


def test_the_plain_status_select_offers_no_closed_option(browser: Client, case: Case) -> None:
    """Closing is reachable only through the dedicated dialog (plan §9.1)."""
    body = _general(browser, case)
    select_start = body.index('name="stage"')
    select_end = body.index("</select>", select_start)
    select = body[select_start:select_end]
    assert 'value="Closed"' not in select
    # The reopen control still exists.
    assert 'value="InProgress"' in select
    assert 'value="New"' in select


def test_the_general_tab_renders_a_close_button_when_the_case_is_open(
    browser: Client, case: Case
) -> None:
    body = _general(browser, case)
    assert "Close case" in body
    assert 'dialog id="case-close-modal"' in body
    assert 'id="case-close-verdict"' in body
    assert 'name="comment"' in body


def test_the_close_dialog_only_offers_the_closing_verdicts(browser: Client, case: Case) -> None:
    body = _general(browser, case)
    start = body.index('id="case-close-verdict"')
    end = body.index("</select>", start)
    select = body[start:end]
    assert 'value="true_positive"' in select
    assert 'value="false_positive"' in select
    # `undefined` is never a valid *closing* verdict (CASE_CLOSING_VERDICTS).
    assert 'value="undefined"' not in select


def test_a_closed_case_offers_no_close_button(browser: Client, case: Case) -> None:
    closed = CaseStatus.objects.get_or_create(
        value="Closed", defaults={"stage": "Closed", "order": 4}
    )[0]
    case.status = closed
    case.save(update_fields=["status"])
    body = _general(browser, case)
    # No opener button and no dialog — a closed case cannot be closed again from the General tab.
    assert 'data-modal-open="case-close-modal"' not in body
    assert 'dialog id="case-close-modal"' not in body


# --------------------------------------------------------------------------
# ui-case-field-edit (plan §9.3)
# --------------------------------------------------------------------------


def test_field_edit_sets_a_field_through_the_shared_applier(browser: Client, case: Case) -> None:
    response = browser.post(
        reverse("ui-case-field-edit", kwargs={"case_id": case.number}),
        {"field": "phase", "value": "containment", "tab": "general"},
    )
    assert response.status_code == 302, response.content

    case.refresh_from_db()
    assert case.phase == "containment"
    assert case.timeline_events.filter(kind="classification-changed").exists()


def test_field_edit_can_set_the_verdict_before_close(browser: Client, case: Case) -> None:
    """Analysts may mark TP/FP before containing (plan §14, resolved in favour of editable)."""
    browser.post(
        reverse("ui-case-field-edit", kwargs={"case_id": case.number}),
        {"field": "verdict", "value": "false_positive"},
    )
    case.refresh_from_db()
    assert case.verdict == "false_positive"


def test_field_edit_rejects_an_unknown_field_with_a_404(browser: Client, case: Case) -> None:
    """The whitelist means an unknown key is a 404, never a write and never a 500."""
    response = browser.post(
        reverse("ui-case-field-edit", kwargs={"case_id": case.number}),
        {"field": "assignee", "value": "nobody"},
    )
    assert response.status_code == 404
    case.refresh_from_db()
    assert case.assignee_id != "nobody"


def test_field_edit_is_404_for_an_unknown_case(browser: Client) -> None:
    assert (
        browser.post(
            reverse("ui-case-field-edit", kwargs={"case_id": "999999"}), {"field": "phase"}
        ).status_code
        == 404
    )


def test_field_edit_is_404_across_organisations(outsider_browser: Client, case: Case) -> None:
    response = outsider_browser.post(
        reverse("ui-case-field-edit", kwargs={"case_id": case.number}),
        {"field": "phase", "value": "containment"},
    )
    assert response.status_code == 404
    case.refresh_from_db()
    assert case.phase != "containment"


def test_a_viewer_is_denied_every_new_endpoint(viewer_browser: Client, case: Case) -> None:
    """ADR-006: a uniform 404 for every mutating surface added by this plan."""
    for name in ("ui-case-close", "ui-case-field-edit", "ui-case-tag-add", "ui-case-tag-remove"):
        response = viewer_browser.post(
            reverse(name, kwargs={"case_id": case.number}), {"field": "phase"}
        )
        assert response.status_code == 404, name


# --------------------------------------------------------------------------
# ui-case-tag-add / ui-case-tag-remove (plan §9.3)
# --------------------------------------------------------------------------


def test_tag_add_attaches_an_existing_tag(browser: Client, case: Case) -> None:
    Tag.objects.get_or_create(name="urgent")
    browser.post(reverse("ui-case-tag-add", kwargs={"case_id": case.number}), {"tags": ["urgent"]})
    assert case.tags.filter(name="urgent").exists()


def test_tag_add_creates_on_assign(browser: Client, case: Case) -> None:
    """5b: a typed tag is minted and attached in the same write as a checked one."""
    browser.post(
        reverse("ui-case-tag-add", kwargs={"case_id": case.number}),
        {"new_tags": "brand-new, also-new"},
    )
    assert {t.name for t in case.tags.all()} == {"brand-new", "also-new"}


def test_tag_add_emits_a_tags_changed_event(browser: Client, case: Case) -> None:
    browser.post(reverse("ui-case-tag-add", kwargs={"case_id": case.number}), {"tags": []})
    # Nothing selected and nothing typed is a refusal, not a silent no-op event.
    assert not case.timeline_events.filter(kind="tags-changed").exists()

    browser.post(reverse("ui-case-tag-add", kwargs={"case_id": case.number}), {"new_tags": "solo"})
    assert case.timeline_events.filter(kind="tags-changed").count() == 1


def test_tag_remove_detaches_a_tag(browser: Client, case: Case) -> None:
    tag, _ = Tag.objects.get_or_create(name="removable")
    case.tags.add(tag)
    browser.post(
        reverse("ui-case-tag-remove", kwargs={"case_id": case.number}), {"tag": "removable"}
    )
    assert not case.tags.filter(name="removable").exists()


# --------------------------------------------------------------------------
# ui-case-close (plan §9.3) — the UI half of the enforced rule
# --------------------------------------------------------------------------


def test_close_is_refused_without_a_verdict(browser: Client, case: Case) -> None:
    browser.post(
        reverse("ui-case-close", kwargs={"case_id": case.number}),
        {"verdict": "undefined", "comment": "closing anyway"},
    )
    case.refresh_from_db()
    assert case.status.stage != "Closed"
    assert case.closed_date is None


def test_close_is_refused_without_a_closing_comment(browser: Client, case: Case) -> None:
    browser.post(
        reverse("ui-case-close", kwargs={"case_id": case.number}),
        {"verdict": "true_positive", "comment": "   "},
    )
    case.refresh_from_db()
    assert case.status.stage != "Closed"


def test_close_succeeds_with_a_verdict_and_a_comment(browser: Client, case: Case) -> None:
    browser.post(
        reverse("ui-case-close", kwargs={"case_id": case.number}),
        {"verdict": "true_positive", "comment": "Confirmed inbox rule."},
    )
    case.refresh_from_db()
    assert case.status.stage == "Closed"
    assert case.closed_date is not None
    assert case.end_date is not None
    assert case.verdict == "true_positive"
    assert case.timeline_events.filter(kind="status-changed").exists()


def test_closing_a_case_closes_every_linked_alert_including_imported(
    browser: Client, case: Case, org: Organisation
) -> None:
    """5a: the propagation includes an `Imported` alert, whose stage is not Closed."""
    _alert(case, org, "New", "ref-new")
    _alert(case, org, "Imported", "ref-imported")

    browser.post(
        reverse("ui-case-close", kwargs={"case_id": case.number}),
        {"verdict": "false_positive", "comment": "Benign."},
    )

    for ref in ("ref-new", "ref-imported"):
        alert = Alert.objects.get(source_ref=ref)
        assert alert.status.stage == "Closed", ref
        assert alert.verdict == "false_positive", ref


def test_closing_records_one_case_level_audit_row(
    browser: Client, case: Case, org: Organisation
) -> None:
    from identity.models import AuditLog

    _alert(case, org, "New", "audit-ref")
    browser.post(
        reverse("ui-case-close", kwargs={"case_id": case.number}),
        {"verdict": "true_positive", "comment": "Done."},
    )
    rows = AuditLog.objects.filter(action="case.alerts_closed", target_type="Case")
    assert rows.count() == 1
    # The Audit tab queries on `target_type="Case"`, so the row must be visible there.
    assert rows.filter(target_id=str(case.id)).exists()


def test_the_plain_status_endpoint_refuses_a_bare_closed(browser: Client, case: Case) -> None:
    """The rule reaches `ui-case-status` too — one rule, no bypass (D1)."""
    browser.post(reverse("ui-case-status", kwargs={"case_id": case.number}), {"stage": "Closed"})
    case.refresh_from_db()
    assert case.status.stage != "Closed"
