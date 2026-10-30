"""Conformance tests for the Cases-list "Mine" assignee filter.

The case list exposes ``?mine=1`` (``ui/views.py:_render_case_list``) as a second,
independent filter beside ``?scope=open|all|closed``. Only the exact string ``"1"``
enables it; every other value is treated as off. It narrows the already
tenant-scoped ``case_visible_q`` set to ``assignee=request.user`` — it can never
reach another tenant's cases or another analyst's queue.

These tests pin the user-visible contract of PLAN-2026-10-29 (AC1-AC10):
visibility, scope intersections, toggle/pagination URL state, heading and empty
copy, the honest ``filtered_from`` count, and read-only viewer availability.
"""

from __future__ import annotations

import html
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from cases.models import Case, CaseStatus
from identity.models import Organisation, User


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Mine Filter Org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="Mine Filter Other Org")


def _make_user(org: Organisation, username: str, *, role: str = "analyst") -> User:
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
    return _make_user(org, "mine-analyst")


@pytest.fixture
def colleague(db: None, org: Organisation) -> User:
    return _make_user(org, "mine-colleague")


@pytest.fixture
def viewer(db: None, org: Organisation) -> User:
    return _make_user(org, "mine-viewer", role="viewer")


@pytest.fixture
def outsider(db: None, other_org: Organisation) -> User:
    return _make_user(other_org, "mine-outsider")


@pytest.fixture
def case_status(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


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


def _make_case(
    org: Organisation,
    status: CaseStatus,
    title: str,
    *,
    assignee: User | None = None,
    closed: bool = False,
) -> Case:
    return Case.objects.create(
        title=title,
        status=status,
        owner_org=org,
        assignee=assignee,
        tlp=2,
        pap=2,
        closed_date=(timezone.now() - timedelta(days=5)) if closed else None,
    )


def _make_cases(
    org: Organisation,
    status: CaseStatus,
    assignee: User,
    count: int,
    *,
    closed: bool = False,
    prefix: str = "Case",
) -> list[Case]:
    return Case.objects.bulk_create(
        [
            Case(
                title=f"{prefix} {i}",
                status=status,
                owner_org=org,
                assignee=assignee,
                tlp=2,
                pap=2,
                closed_date=(timezone.now() - timedelta(days=5)) if closed else None,
            )
            for i in range(count)
        ]
    )


def _titles(response) -> set[str]:
    return {case.title for case in response.context["page_obj"].object_list}


def _mine_anchor_tag(body: str) -> str:
    match = re.search(r"<a\b[^>]*>\s*Mine\s*</a>", body)
    assert match is not None, "Mine link was not rendered"
    return match.group(0)


def _attr(tag: str, name: str) -> str | None:
    match = re.search(rf'{re.escape(name)}="([^"]*)"', tag)
    return html.unescape(match.group(1)) if match else None


def _href_query(href: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(html.unescape(href)).query)


@pytest.mark.django_db
def test_default_list_has_no_mine_filter_and_mine_link_sits_after_clear_in_form(
    browser: Client, org: Organisation, analyst: User, colleague: User, case_status: CaseStatus
) -> None:
    """No ``mine`` param → all visible cases, no hidden input, Mine after Clear inside the form."""
    _make_case(org, case_status, "Mine default case", assignee=analyst)
    _make_case(org, case_status, "Colleague default case", assignee=colleague)
    _make_case(org, case_status, "Unassigned default case")

    response = browser.get(reverse("ui-case-list"))
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["mine"] is False
    assert _titles(response) == {
        "Mine default case",
        "Colleague default case",
        "Unassigned default case",
    }
    assert 'name="mine"' not in body

    form_match = re.search(
        r'<form\b[^>]*class="[^"]*filter-bar[^"]*"[^>]*>.*?</form>', body, re.DOTALL
    )
    assert form_match is not None, "filter form not found"
    form = form_match.group(0)
    assert ">Mine</a>" in form
    assert ">Clear</a>" in form
    assert form.index(">Clear</a>") < form.index(">Mine</a>")
    # New case is the last control; Mine must precede it (AC1).
    assert ">New case</a>" in form
    assert form.index(">Mine</a>") < form.index(">New case</a>")


@pytest.mark.django_db
def test_mine_one_returns_only_signed_in_users_cases(
    browser: Client, org: Organisation, analyst: User, colleague: User, case_status: CaseStatus
) -> None:
    """``?mine=1`` excludes another analyst's cases and unassigned cases (AC2)."""
    _make_case(org, case_status, "Mine only", assignee=analyst)
    _make_case(org, case_status, "Colleague only", assignee=colleague)
    _make_case(org, case_status, "Nobody owns me")

    response = browser.get(reverse("ui-case-list"), {"mine": "1"})

    assert response.status_code == 200
    assert response.context["mine"] is True
    assert _titles(response) == {"Mine only"}


@pytest.mark.django_db
def test_mine_combines_independently_with_each_scope(
    browser: Client, org: Organisation, analyst: User, colleague: User, case_status: CaseStatus
) -> None:
    """Mine is orthogonal to scope: open/all/closed each intersect with it (AC3)."""
    _make_case(org, case_status, "My open", assignee=analyst, closed=False)
    _make_case(org, case_status, "My closed", assignee=analyst, closed=True)
    _make_case(org, case_status, "Their open", assignee=colleague, closed=False)
    _make_case(org, case_status, "Their closed", assignee=colleague, closed=True)
    _make_case(org, case_status, "Unassigned open", closed=False)

    expected = {
        "open": {"My open"},
        "all": {"My open", "My closed"},
        "closed": {"My closed"},
    }
    for scope, titles in expected.items():
        response = browser.get(reverse("ui-case-list"), {"scope": scope, "mine": "1"})
        assert response.status_code == 200, scope
        assert response.context["scope"] == scope, scope
        assert _titles(response) == titles, scope


@pytest.mark.django_db
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"mine": "0"},
        {"mine": "true"},
        {"mine": "yes"},
        {"mine": ""},
    ],
)
def test_any_value_other_than_exact_one_is_off(
    browser: Client,
    org: Organisation,
    analyst: User,
    colleague: User,
    case_status: CaseStatus,
    params: dict[str, str],
) -> None:
    """Only the exact string ``1`` enables Mine; absent/0/true/yes/empty are off (AC8)."""
    _make_case(org, case_status, "Mine visible", assignee=analyst)
    _make_case(org, case_status, "Colleague visible", assignee=colleague)

    response = browser.get(reverse("ui-case-list"), params)

    assert response.status_code == 200, params
    assert response.context["mine"] is False, params
    assert _titles(response) == {"Mine visible", "Colleague visible"}, params
    assert 'name="mine"' not in response.content.decode(), params


@pytest.mark.django_db
def test_mine_does_not_surface_cross_org_case_assigned_to_user(
    browser: Client,
    org: Organisation,
    other_org: Organisation,
    analyst: User,
    case_status: CaseStatus,
) -> None:
    """Tenant isolation: Mine narrows ``case_visible_q`` and never widens it (AC9)."""
    _make_case(org, case_status, "Visible mine", assignee=analyst)
    # Assigned to the signed-in analyst, but owned by another organisation and unshared.
    _make_case(other_org, case_status, "Cross-org secret", assignee=analyst)

    response = browser.get(reverse("ui-case-list"), {"mine": "1"})

    assert response.status_code == 200
    assert _titles(response) == {"Visible mine"}
    assert "Cross-org secret" not in response.content.decode()


@pytest.mark.django_db
def test_inactive_mine_href_enables_filter_preserves_scope_and_resets_page(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """The inactive Mine link adds ``mine=1`` while keeping scope and resetting page."""
    _make_cases(org, case_status, analyst, 30, closed=True, prefix="Closed")
    response = browser.get(
        reverse("ui-case-list"), {"scope": "closed", "per_page": "25", "page": "2"}
    )
    assert response.status_code == 200

    tag = _mine_anchor_tag(response.content.decode())
    href = _attr(tag, "href")
    assert href is not None
    query = _href_query(href)

    assert query.get("mine") == ["1"]
    assert query.get("scope") == ["closed"]
    assert query.get("page") == ["1"]
    assert query.get("per_page") == ["25"]
    assert _attr(tag, "aria-current") is None
    classes = (_attr(tag, "class") or "").split()
    assert "btn" in classes
    assert "btn-primary" not in classes


@pytest.mark.django_db
def test_active_mine_href_toggles_off_preserves_scope_and_resets_page(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """Clicking Mine while active removes ``mine``, keeps scope, and resets page (AC4)."""
    _make_cases(org, case_status, analyst, 30, closed=True, prefix="Closed")
    response = browser.get(
        reverse("ui-case-list"),
        {"scope": "closed", "mine": "1", "per_page": "25", "page": "2"},
    )
    assert response.status_code == 200

    tag = _mine_anchor_tag(response.content.decode())
    href = _attr(tag, "href")
    assert href is not None
    query = _href_query(href)

    assert "mine" not in query
    assert query.get("scope") == ["closed"]
    assert query.get("page") == ["1"]
    assert query.get("per_page") == ["25"]
    assert _attr(tag, "aria-current") == "true"
    assert "btn-primary" in (_attr(tag, "class") or "").split()


@pytest.mark.django_db
def test_filter_form_hidden_mine_input_only_when_active(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """Active Mine renders a hidden input so submitting Filter preserves it (AC5)."""
    _make_case(org, case_status, "Mine hidden input", assignee=analyst)

    active = browser.get(reverse("ui-case-list"), {"mine": "1"}).content.decode()
    inactive = browser.get(reverse("ui-case-list")).content.decode()

    assert '<input type="hidden" name="mine" value="1">' in active
    assert 'name="mine"' not in inactive


@pytest.mark.django_db
def test_pager_and_per_page_links_preserve_scope_and_mine(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """Pager/per-page hrefs keep both predicates so paging never drops the filter (AC5)."""
    _make_cases(org, case_status, analyst, 60, closed=False, prefix="Paged mine")

    response = browser.get(
        reverse("ui-case-list"),
        {"scope": "open", "mine": "1", "per_page": "25", "page": "1"},
    )
    assert response.status_code == 200
    body = html.unescape(response.content.decode())

    pager = re.search(r'<nav class="pager".*?</nav>', body, re.DOTALL)
    assert pager is not None, "expected a pager on a 60-row list"
    pager_block = pager.group(0)
    assert re.search(r'<a\b[^>]*rel="next"[^>]*>', pager_block), "expected a next-page link"
    pager_hrefs = re.findall(r'href="([^"]*[?&]page=\d+[^"]*)"', pager_block)
    assert pager_hrefs, "expected pager links"

    sizes = re.search(r'<div class="pager-sizes".*?</div>', body, re.DOTALL)
    assert sizes is not None, "expected per-page size links"
    sizes_hrefs = re.findall(r'href="([^"]*[?&]page=\d+[^"]*)"', sizes.group(0))
    assert sizes_hrefs, "expected per-page size links"

    for href in pager_hrefs + sizes_hrefs:
        query = _href_query(href)
        assert query.get("scope") == ["open"], href
        assert query.get("mine") == ["1"], href


@pytest.mark.django_db
def test_heading_variants_for_all_scope_and_mine_combinations(
    browser: Client, org: Organisation, analyst: User, case_status: CaseStatus
) -> None:
    """Heading is honest for Open/All/Closed with and without Mine (AC7)."""
    _make_case(org, case_status, "Heading case", assignee=analyst)

    expected = {
        ("open", False): "Open cases",
        ("all", False): "All cases",
        ("closed", False): "Closed cases",
        ("open", True): "My open cases",
        ("all", True): "My cases",
        ("closed", True): "My closed cases",
    }
    for (scope, mine), heading in expected.items():
        params = {"scope": scope}
        if mine:
            params["mine"] = "1"
        response = browser.get(reverse("ui-case-list"), params)
        assert response.status_code == 200, (scope, mine)
        assert response.context["heading"] == heading, (scope, mine)
        assert f"<h2>{heading}</h2>" in response.content.decode(), (scope, mine)


@pytest.mark.django_db
def test_mine_filtered_empty_list_uses_generic_copy(
    browser: Client, org: Organisation, colleague: User, case_status: CaseStatus
) -> None:
    """A Mine-filtered empty list says "No cases match your filters", not "No cases open." (AC7)."""
    _make_case(org, case_status, "Someone else's case", assignee=colleague)

    response = browser.get(reverse("ui-case-list"), {"mine": "1"})
    body = response.content.decode()

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    assert "No cases match your filters." in body
    assert "Clear filters" in body
    assert "No cases open." not in body


@pytest.mark.django_db
def test_filtered_from_populated_when_mine_alone_narrows(
    browser: Client, org: Organisation, analyst: User, colleague: User, case_status: CaseStatus
) -> None:
    """``scope=all&mine=1`` narrows, so the honest "(filtered from N)" shows; plain all does not."""
    _make_case(org, case_status, "My all-scope case", assignee=analyst)
    _make_case(org, case_status, "Colleague all-scope case", assignee=colleague)

    filtered = browser.get(reverse("ui-case-list"), {"scope": "all", "mine": "1"})
    assert filtered.status_code == 200
    assert filtered.context["page_obj"].paginator.count == 1
    assert filtered.context["filtered_from"] == 2
    assert "(filtered from 2)" in filtered.content.decode()

    unfiltered = browser.get(reverse("ui-case-list"), {"scope": "all"})
    assert unfiltered.status_code == 200
    assert unfiltered.context["page_obj"].paginator.count == 2
    assert unfiltered.context["filtered_from"] is None
    assert "(filtered from" not in unfiltered.content.decode()


@pytest.mark.django_db
def test_viewer_sees_and_can_apply_mine_filter(
    viewer_browser: Client,
    org: Organisation,
    viewer: User,
    colleague: User,
    case_status: CaseStatus,
) -> None:
    """A viewer gets the read-only Mine control and can filter with it (AC1/AC10)."""
    _make_case(org, case_status, "Viewer's case", assignee=viewer)
    _make_case(org, case_status, "Analyst's case", assignee=colleague)

    default = viewer_browser.get(reverse("ui-case-list"))
    default_body = default.content.decode()
    assert default.status_code == 200
    assert re.search(r"<a\b[^>]*>\s*Mine\s*</a>", default_body)
    assert ">New case</a>" not in default_body

    filtered = viewer_browser.get(reverse("ui-case-list"), {"mine": "1"})
    filtered_body = filtered.content.decode()
    assert filtered.status_code == 200
    assert filtered.context["mine"] is True
    assert filtered.context["heading"] == "My open cases"
    assert _titles(filtered) == {"Viewer's case"}
    assert '<input type="hidden" name="mine" value="1">' in filtered_body
