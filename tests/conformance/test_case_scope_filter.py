"""Conformance tests for the Cases-list tri-state scope filter (Open / All / Closed).

The list view exposes ``?scope=open|all|closed`` (``ui/views.py:_render_case_list``).
``open`` is the default; ``all`` is the un-filtered set; ``closed`` shows only closed
cases. An unrecognised value falls back to ``open`` so a malformed query never 500s.

These are the guards the pre-2026-10-20 ``closed=1`` scheme could not express: before
this change the "All" tab actually rendered the *closed-only* set (a mislabel), and
there was no way to see every case in one list.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from cases.models import Case, CaseStatus
from identity.models import Organisation, User


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Scope Filter Org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="scope-analyst",
        login="scope-analyst",
        password="pw-scope-analyst",
        email="scope-analyst@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )


@pytest.fixture
def case_status(db: None) -> CaseStatus:
    return CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    client.force_login(analyst)
    return client


def _make_cases(org: Organisation, status: CaseStatus, assignee: User, count: int, *, closed: bool):
    cases = [
        Case(
            title=f"{'Closed' if closed else 'Open'} case {i}",
            status=status,
            owner_org=org,
            assignee=assignee,
            tlp=2,
            pap=2,
            closed_date=(timezone.now() - timedelta(days=5)) if closed else None,
        )
        for i in range(count)
    ]
    return Case.objects.bulk_create(cases)


def _count(browser: Client, *, scope: str | None) -> int:
    params = {} if scope is None else {"scope": scope}
    response = browser.get(reverse("ui-case-list"), params)
    assert response.status_code == 200
    return response.context["page_obj"].paginator.count


@pytest.mark.django_db
def test_scope_default_is_open(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """No ``scope`` param at all → the open set (the triage default)."""
    _make_cases(org, case_status, analyst, 3, closed=False)
    _make_cases(org, case_status, analyst, 5, closed=True)
    assert _count(browser, scope=None) == 3


@pytest.mark.django_db
def test_scope_open_returns_only_open(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    _make_cases(org, case_status, analyst, 3, closed=False)
    _make_cases(org, case_status, analyst, 5, closed=True)
    assert _count(browser, scope="open") == 3


@pytest.mark.django_db
def test_scope_closed_returns_only_closed(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    _make_cases(org, case_status, analyst, 3, closed=False)
    _make_cases(org, case_status, analyst, 5, closed=True)
    assert _count(browser, scope="closed") == 5


@pytest.mark.django_db
def test_scope_all_returns_every_case(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """``all`` is the union — the state the old UI could not reach with any single tab."""
    _make_cases(org, case_status, analyst, 3, closed=False)
    _make_cases(org, case_status, analyst, 5, closed=True)
    assert _count(browser, scope="all") == 8


@pytest.mark.django_db
def test_scope_all_is_not_a_filter_so_no_disclosure(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """``all`` narrows nothing, so ``filtered_from`` must stay None (no "(filtered from N)")."""
    _make_cases(org, case_status, analyst, 3, closed=False)
    _make_cases(org, case_status, analyst, 5, closed=True)
    response = browser.get(reverse("ui-case-list"), {"scope": "all"})
    assert response.context["filtered_from"] is None
    assert "(filtered from" not in response.content.decode()


@pytest.mark.django_db
def test_scope_open_and_closed_are_filters_so_disclosure_shows(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """``open``/``closed`` narrow the set, so the honest "(filtered from 8)" disclosure appears."""
    _make_cases(org, case_status, analyst, 3, closed=False)
    _make_cases(org, case_status, analyst, 5, closed=True)
    for scope in ("open", "closed"):
        response = browser.get(reverse("ui-case-list"), {"scope": scope})
        assert response.context["filtered_from"] == 8, scope
        assert "(filtered from 8)" in response.content.decode(), scope


@pytest.mark.django_db
def test_unrecognised_scope_falls_back_to_open(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """A hostile/typo ``scope`` must not 500 and must behave as the default ``open``."""
    _make_cases(org, case_status, analyst, 3, closed=False)
    _make_cases(org, case_status, analyst, 5, closed=True)
    for bad in ("../etc", "ALL", "1", "closed' OR 1=1", ""):
        response = browser.get(reverse("ui-case-list"), {"scope": bad})
        assert response.status_code == 200, bad
        assert response.context["page_obj"].paginator.count == 3, bad


@pytest.mark.django_db
def test_scope_select_renders_three_options_with_correct_selection(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """The filter card exposes three options, and the active one carries ``selected``."""
    import re

    _make_cases(org, case_status, analyst, 1, closed=False)
    for scope in ("open", "all", "closed"):
        body = browser.get(reverse("ui-case-list"), {"scope": scope}).content.decode()
        select = re.search(r'<select id="f-case-scope".*?</select>', body, re.DOTALL)
        assert select, scope
        block = select.group(0)
        assert block.count("<option ") == 3, scope
        # The selected option must be the requested scope, and only that one.
        assert f'value="{scope}" selected' in block, scope
        for other in {"open", "all", "closed"} - {scope}:
            assert f'value="{other}" selected' not in block, (scope, other)


@pytest.mark.django_db
def test_heading_reflects_scope(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """The table-card heading reads Open/All/Closed cases for the active scope."""
    _make_cases(org, case_status, analyst, 1, closed=False)
    expected = {
        "open": "<h2>Open cases</h2>",
        "all": "<h2>All cases</h2>",
        "closed": "<h2>Closed cases</h2>",
    }
    for scope, heading in expected.items():
        body = browser.get(reverse("ui-case-list"), {"scope": scope}).content.decode()
        assert heading in body, scope


@pytest.mark.django_db
def test_scope_closed_survives_pagination(
    browser: Client, org: Organisation, analyst: User, case_status
) -> None:
    """Pager hrefs on ``?scope=closed`` keep ``scope=closed`` (no fall-back to ``open``)."""
    from urllib.parse import parse_qs

    _make_cases(org, case_status, analyst, 60, closed=True)
    response = browser.get(
        reverse("ui-case-list"), {"scope": "closed", "per_page": "25", "page": "2"}
    )
    assert response.status_code == 200
    body = response.content.decode()
    import re

    hrefs = re.findall(r'href="([^"]*page=\d+[^"]*)"', body)
    assert hrefs, "expected pager links on a 60-row closed list"
    for href in hrefs:
        query = parse_qs(href.split("?", 1)[1])
        assert query.get("scope") == ["closed"], href
