"""Conformance tests for the Alerts-list scope and "Mine" filters.

PLAN-2026-10-29 replaces the old alert scopes (``unlinked`` / ``linked`` / ``all``)
with five explicit, disjoint buckets:

* ``unlinked_open``    — no case, alert status stage ``New``
* ``unlinked_triaged`` — no case, alert status stage ``InProgress``
* ``unlinked_closed``  — no case, alert status stage ``Closed`` (includes ``Dismissed``)
* ``linked_open``      — linked to a case without a ``closed_date``
* ``linked_closed``    — linked to a case with a ``closed_date``

``?mine=1`` narrows any selected scope to alerts assigned to the signed-in user. It is
orthogonal to scope, tenant-scoped first, and read-only.

These tests pin the user-visible contract: scope predicates, linked-case lifecycle,
the dropdown, Mine toggle/URL state, queue counts, empty states and viewer access.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation, User

# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Alert scope org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="Alert scope other org")


def _user(org: Organisation, username: str, *, role: str = "analyst") -> User:
    return User.objects.create_user(
        username=username,
        login=username,
        password=f"pw-{username}",
        email=f"{username}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return _user(org, "alert-scope-analyst")


@pytest.fixture
def colleague(db: None, org: Organisation) -> User:
    return _user(org, "alert-scope-colleague")


@pytest.fixture
def viewer(db: None, org: Organisation) -> User:
    return _user(org, "alert-scope-viewer", role="viewer")


@pytest.fixture
def outsider(db: None, other_org: Organisation) -> User:
    return _user(other_org, "alert-scope-outsider")


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    client.force_login(analyst)
    return client


@pytest.fixture
def viewer_browser(viewer: User) -> Client:
    client = Client()
    client.force_login(viewer)
    return client


def _status(value: str, stage: str, order: int = 1) -> AlertStatus:
    return AlertStatus.objects.get_or_create(
        value=value, defaults={"stage": stage, "order": order}
    )[0]


@pytest.fixture
def open_status(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.fixture
def closed_status(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(
        value="Closed", defaults={"stage": "Closed", "order": 5}
    )[0]


def _alert(
    org: Organisation,
    status: AlertStatus,
    source_ref: str,
    *,
    case: Case | None = None,
    assignee: User | None = None,
) -> Alert:
    return Alert.objects.create(
        type="phishing",
        source="scope-test",
        source_ref=source_ref,
        title=source_ref,
        owner_org=org,
        status=status,
        case=case,
        assignee=assignee,
    )


def _case(
    org: Organisation,
    status: CaseStatus,
    title: str,
    *,
    closed: bool = False,
) -> Case:
    return Case.objects.create(
        title=title,
        status=status,
        owner_org=org,
        tlp=2,
        pap=2,
        closed_date=timezone.now() if closed else None,
    )


def _titles(response) -> set[str]:
    return {alert.title for alert in response.context["page_obj"].object_list}


def _body(response) -> str:
    return response.content.decode()


def _text(source: str) -> str:
    stripped = re.sub(r"<[^>]+>", " ", html.unescape(source))
    return re.sub(r"\s+", " ", stripped).strip()


def _options(body: str) -> list[tuple[str, str]]:
    return [
        (value, html.unescape(label).strip())
        for value, label in re.findall(r'<option value="([^"]+)"[^>]*>(.*?)</option>', body, re.S)
    ]


def _mine_anchor(body: str) -> str:
    match = re.search(r"<a\b[^>]*>\s*Mine\s*</a>", body)
    assert match, "the Mine control is missing"
    return match.group(0)


def _pager_queries(body: str) -> list[dict[str, list[str]]]:
    match = re.search(r'<nav class="pager".*?</nav>', body, re.S)
    if not match:
        return []
    nav = html.unescape(match.group(0))
    return [parse_qs(urlsplit(href).query) for href in re.findall(r'href="([^"]+)"', nav)]


# --------------------------------------------------------------------------
# Scope predicates
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_unlinked_open_is_new_stage_only(browser: Client, org: Organisation) -> None:
    new = _status("New", "New")
    triaged = _status("Triaged", "InProgress")
    dismissed = _status("Dismissed", "Closed")

    _alert(org, new, "unlinked-new")
    _alert(org, triaged, "unlinked-triaged")
    _alert(org, dismissed, "unlinked-dismissed")

    response = browser.get(reverse("ui-alert-list"), {"scope": "unlinked_open"})

    assert response.status_code == 200
    assert _titles(response) == {"unlinked-new"}


@pytest.mark.django_db
def test_unlinked_triaged_is_in_progress_stage_only(browser: Client, org: Organisation) -> None:
    new = _status("New", "New")
    triaged = _status("Triaged", "InProgress")
    dismissed = _status("Dismissed", "Closed")

    _alert(org, new, "unlinked-new")
    _alert(org, triaged, "unlinked-triaged")
    _alert(org, dismissed, "unlinked-dismissed")

    response = browser.get(reverse("ui-alert-list"), {"scope": "unlinked_triaged"})

    assert response.status_code == 200
    assert _titles(response) == {"unlinked-triaged"}


@pytest.mark.django_db
def test_unlinked_closed_includes_dismissed_and_closed_stages(
    browser: Client, org: Organisation
) -> None:
    new = _status("New", "New")
    triaged = _status("Triaged", "InProgress")
    dismissed = _status("Dismissed", "Closed")
    closed = _status("Closed", "Closed")

    _alert(org, new, "unlinked-new")
    _alert(org, triaged, "unlinked-triaged")
    _alert(org, dismissed, "unlinked-dismissed")
    _alert(org, closed, "unlinked-closed")

    response = browser.get(reverse("ui-alert-list"), {"scope": "unlinked_closed"})

    assert response.status_code == 200
    assert _titles(response) == {"unlinked-dismissed", "unlinked-closed"}


@pytest.mark.django_db
def test_linked_scopes_follow_the_case_lifecycle_not_alert_status(
    browser: Client, org: Organisation, open_status: CaseStatus, closed_status: CaseStatus
) -> None:
    new = _status("New", "New")
    imported = _status("Imported", "Imported")

    open_case = _case(org, open_status, "open case")
    closed_case = _case(org, closed_status, "closed case", closed=True)

    _alert(org, new, "open-case-new-status", case=open_case)
    _alert(org, imported, "open-case-imported-status", case=open_case)
    _alert(org, new, "closed-case-new-status", case=closed_case)
    _alert(org, imported, "closed-case-imported-status", case=closed_case)

    open_response = browser.get(reverse("ui-alert-list"), {"scope": "linked_open"})
    closed_response = browser.get(reverse("ui-alert-list"), {"scope": "linked_closed"})

    assert open_response.status_code == 200
    assert closed_response.status_code == 200
    assert _titles(open_response) == {"open-case-new-status", "open-case-imported-status"}
    assert _titles(closed_response) == {"closed-case-new-status", "closed-case-imported-status"}


@pytest.mark.django_db
def test_five_scopes_are_disjoint_and_cover_the_dataset(
    browser: Client, org: Organisation, open_status: CaseStatus, closed_status: CaseStatus
) -> None:
    new = _status("New", "New")
    triaged = _status("Triaged", "InProgress")
    dismissed = _status("Dismissed", "Closed")
    imported = _status("Imported", "Imported")

    open_case = _case(org, open_status, "open case")
    closed_case = _case(org, closed_status, "closed case", closed=True)

    _alert(org, new, "unlinked-open")
    _alert(org, triaged, "unlinked-triaged")
    _alert(org, dismissed, "unlinked-closed")
    _alert(org, imported, "linked-open", case=open_case)
    _alert(org, imported, "linked-closed", case=closed_case)

    assert _titles(browser.get(reverse("ui-alert-list"), {"scope": "unlinked_open"})) == {
        "unlinked-open"
    }
    assert _titles(browser.get(reverse("ui-alert-list"), {"scope": "unlinked_triaged"})) == {
        "unlinked-triaged"
    }
    assert _titles(browser.get(reverse("ui-alert-list"), {"scope": "unlinked_closed"})) == {
        "unlinked-closed"
    }
    assert _titles(browser.get(reverse("ui-alert-list"), {"scope": "linked_open"})) == {
        "linked-open"
    }
    assert _titles(browser.get(reverse("ui-alert-list"), {"scope": "linked_closed"})) == {
        "linked-closed"
    }


# --------------------------------------------------------------------------
# Dropdown and removed scopes
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_dropdown_has_exactly_the_five_title_case_options(browser: Client) -> None:
    response = browser.get(reverse("ui-alert-list"))

    assert response.status_code == 200
    assert _options(_body(response)) == [
        ("unlinked_open", "Unlinked (open)"),
        ("unlinked_triaged", "Unlinked (triaged)"),
        ("unlinked_closed", "Unlinked (closed)"),
        ("linked_open", "Linked (open)"),
        ("linked_closed", "Linked (closed)"),
    ]
    assert 'value="all"' not in _body(response)
    assert 'value="unlinked"' not in _body(response)
    assert 'value="linked"' not in _body(response)


@pytest.mark.django_db
def test_selected_scope_is_the_current_scope(browser: Client) -> None:
    response = browser.get(reverse("ui-alert-list"), {"scope": "linked_closed"})

    assert response.status_code == 200
    body = _body(response)
    assert re.search(r'<option value="linked_closed"[^>]*selected', body)


@pytest.mark.django_db
@pytest.mark.parametrize("removed", ["unlinked", "linked", "all", "hostile"])
def test_removed_or_invalid_scope_falls_back_to_unlinked_open(
    browser: Client, org: Organisation, removed: str
) -> None:
    new = _status("New", "New")
    triaged = _status("Triaged", "InProgress")

    _alert(org, new, "unlinked-open")
    _alert(org, triaged, "unlinked-triaged")

    response = browser.get(reverse("ui-alert-list"), {"scope": removed})

    assert response.status_code == 200
    assert response.context["scope"] == "unlinked_open"
    assert _titles(response) == {"unlinked-open"}


# --------------------------------------------------------------------------
# Mine
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_mine_narrows_to_the_signed_in_user(
    browser: Client,
    org: Organisation,
    other_org: Organisation,
    analyst: User,
    colleague: User,
) -> None:
    new = _status("New", "New")

    _alert(org, new, "mine", assignee=analyst)
    _alert(org, new, "colleague", assignee=colleague)
    _alert(org, new, "unassigned")
    _alert(other_org, new, "other-org-assigned-to-me", assignee=analyst)

    response = browser.get(reverse("ui-alert-list"), {"mine": "1"})

    assert response.status_code == 200
    assert _titles(response) == {"mine"}


@pytest.mark.django_db
def test_mine_combines_with_every_scope(
    browser: Client,
    org: Organisation,
    analyst: User,
    colleague: User,
    open_status: CaseStatus,
    closed_status: CaseStatus,
) -> None:
    new = _status("New", "New")
    triaged = _status("Triaged", "InProgress")
    dismissed = _status("Dismissed", "Closed")
    imported = _status("Imported", "Imported")

    open_case = _case(org, open_status, "open case")
    closed_case = _case(org, closed_status, "closed case", closed=True)

    for ref, status, case in [
        ("unlinked-open", new, None),
        ("unlinked-triaged", triaged, None),
        ("unlinked-closed", dismissed, None),
        ("linked-open", imported, open_case),
        ("linked-closed", imported, closed_case),
    ]:
        _alert(org, status, f"{ref}-mine", case=case, assignee=analyst)
        _alert(org, status, f"{ref}-colleague", case=case, assignee=colleague)

    for scope, ref in [
        ("unlinked_open", "unlinked-open"),
        ("unlinked_triaged", "unlinked-triaged"),
        ("unlinked_closed", "unlinked-closed"),
        ("linked_open", "linked-open"),
        ("linked_closed", "linked-closed"),
    ]:
        response = browser.get(reverse("ui-alert-list"), {"scope": scope, "mine": "1"})
        assert response.status_code == 200
        assert _titles(response) == {f"{ref}-mine"}, scope


@pytest.mark.django_db
def test_only_exact_mine_one_enables_the_filter(
    browser: Client, org: Organisation, analyst: User
) -> None:
    new = _status("New", "New")
    _alert(org, new, "mine", assignee=analyst)
    _alert(org, new, "unassigned")

    for value in ("0", "true", "yes", ""):
        response = browser.get(reverse("ui-alert-list"), {"mine": value})
        assert response.status_code == 200
        assert response.context["mine"] is False
        assert _titles(response) == {"mine", "unassigned"}, value


@pytest.mark.django_db
def test_mine_toggle_markup_and_url_state(
    browser: Client, org: Organisation, analyst: User
) -> None:
    new = _status("New", "New")
    _alert(org, new, "mine", assignee=analyst)

    inactive_body = _body(browser.get(reverse("ui-alert-list"), {"scope": "linked_closed"}))
    inactive = _mine_anchor(inactive_body)
    inactive_href = html.unescape(re.search(r'href="([^"]+)"', inactive).group(1))
    inactive_query = parse_qs(urlsplit(inactive_href).query)
    assert inactive_query["mine"] == ["1"]
    assert inactive_query["scope"] == ["linked_closed"]
    assert inactive_query["page"] == ["1"]
    assert 'aria-current="true"' not in inactive

    active_body = _body(
        browser.get(
            reverse("ui-alert-list"),
            {"scope": "linked_closed", "mine": "1", "page": "2"},
        )
    )
    active = _mine_anchor(active_body)
    active_href = html.unescape(re.search(r'href="([^"]+)"', active).group(1))
    active_query = parse_qs(urlsplit(active_href).query)
    assert "mine" not in active_query
    assert active_query["scope"] == ["linked_closed"]
    assert active_query["page"] == ["1"]
    assert 'aria-current="true"' in active
    assert '<input type="hidden" name="mine" value="1">' in active_body


@pytest.mark.django_db
def test_pagination_preserves_scope_and_mine(
    browser: Client, org: Organisation, analyst: User
) -> None:
    new = _status("New", "New")
    for i in range(60):
        _alert(org, new, f"mine-{i}", assignee=analyst)

    response = browser.get(
        reverse("ui-alert-list"),
        {"scope": "unlinked_open", "mine": "1", "per_page": "25", "page": "1"},
    )

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 60
    queries = _pager_queries(_body(response))
    assert queries
    for query in queries:
        assert query.get("scope") == ["unlinked_open"]
        assert query.get("mine") == ["1"]
        assert query.get("per_page") == ["25"]


# --------------------------------------------------------------------------
# Queue counts
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_nav_and_dashboard_counts_use_unlinked_open(
    browser: Client, org: Organisation, open_status: CaseStatus
) -> None:
    new = _status("New", "New")
    triaged = _status("Triaged", "InProgress")
    dismissed = _status("Dismissed", "Closed")

    _alert(org, new, "waiting-1")
    _alert(org, new, "waiting-2")
    _alert(org, triaged, "triaged")
    _alert(org, dismissed, "dismissed")
    _alert(org, new, "linked", case=_case(org, open_status, "open case"))

    list_response = browser.get(reverse("ui-alert-list"))
    dashboard_response = browser.get(reverse("ui-dashboard"))

    assert list_response.context["nav_alert_count"] == 2
    assert dashboard_response.context["nav_alert_count"] == 2
    assert dashboard_response.context["counts"]["alerts_unlinked"] == 2
    assert {alert.title for alert in dashboard_response.context["recent_alerts"]} == {
        "waiting-1",
        "waiting-2",
    }


# --------------------------------------------------------------------------
# Empty states and filtered-from
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_genuinely_empty_default_scope(browser: Client) -> None:
    response = browser.get(reverse("ui-alert-list"))

    assert response.status_code == 200
    assert response.context["filtered_from"] is None
    text = _text(_body(response))
    assert "No alerts in this scope." in text
    assert "Clear filters" not in text


@pytest.mark.django_db
def test_filtered_empty_discloses_unfiltered_count(
    browser: Client, org: Organisation, open_status: CaseStatus
) -> None:
    new = _status("New", "New")
    _alert(org, new, "linked", case=_case(org, open_status, "open case"))

    response = browser.get(reverse("ui-alert-list"))

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    assert response.context["filtered_from"] == 1
    text = _text(_body(response))
    assert "No alerts match your filters (filtered from 1)." in text
    assert "Clear filters" in text


@pytest.mark.django_db
def test_filtered_from_is_absent_when_scope_does_not_narrow(
    browser: Client, org: Organisation
) -> None:
    new = _status("New", "New")
    _alert(org, new, "one")
    _alert(org, new, "two")

    response = browser.get(reverse("ui-alert-list"))

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 2
    assert response.context["filtered_from"] is None
    assert "(filtered from" not in _text(_body(response))


# --------------------------------------------------------------------------
# Viewer access
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_viewer_can_use_scope_and_mine(
    viewer_browser: Client, org: Organisation, viewer: User, colleague: User
) -> None:
    new = _status("New", "New")
    _alert(org, new, "viewer-alert", assignee=viewer)
    _alert(org, new, "colleague-alert", assignee=colleague)

    response = viewer_browser.get(reverse("ui-alert-list"), {"scope": "unlinked_open", "mine": "1"})

    assert response.status_code == 200
    assert _titles(response) == {"viewer-alert"}
