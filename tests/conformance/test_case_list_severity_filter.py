"""Conformance tests for the case-list severity filter toggles."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from cases.models import Case, CaseStatus
from identity.models import Organisation, User


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Case severity org")


@pytest.fixture
def analyst(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="case-sev-analyst",
        login="case-sev-analyst",
        password="pw-case-sev",
        email="case-sev-analyst@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )


@pytest.fixture
def colleague(db: None, org: Organisation) -> User:
    return User.objects.create_user(
        username="case-sev-colleague",
        login="case-sev-colleague",
        password="pw-case-sev",
        email="case-sev-colleague@example.net",
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


def _case(
    org: Organisation,
    status: CaseStatus,
    title: str,
    *,
    severity: int = 2,
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
        severity=severity,
        closed_date=timezone.now() if closed else None,
    )


def _titles(response) -> set[str]:
    return {case.title for case in response.context["page_obj"].object_list}


def _anchors(body: str) -> list[str]:
    return re.findall(r'<a\b[^>]*\bclass="[^"]*severity-filter[^"]*"[^>]*>.*?</a>', body, re.S)


def _attr(tag: str, name: str) -> str | None:
    match = re.search(rf'{name}="([^"]*)"', tag)
    return html.unescape(match.group(1)) if match else None


def _query(tag: str) -> dict[str, list[str]]:
    href = _attr(tag, "href")
    assert href is not None
    return parse_qs(urlsplit(href).query)


def _hidden_severities(body: str) -> list[str]:
    return re.findall(r'<input type="hidden" name="severity" value="([^"]+)">', body)


@pytest.mark.django_db
def test_case_severity_filter_ors_selected_values_and_exposes_context(
    browser: Client, org: Organisation, case_status: CaseStatus
) -> None:
    _case(org, case_status, "Low case", severity=1)
    _case(org, case_status, "Medium case", severity=2)
    _case(org, case_status, "High case", severity=3)
    _case(org, case_status, "Critical case", severity=4)

    response = browser.get(reverse("ui-case-list"), {"severity": ["1", "3"]})

    assert response.status_code == 200
    assert _titles(response) == {"Low case", "High case"}
    assert response.context["selected_severities"] == [1, 3]
    links = response.context["severity_filter_links"]
    assert [link["label"] for link in links] == ["Low", "Medium", "High", "Critical"]
    assert [link["selected"] for link in links] == [True, False, True, False]


@pytest.mark.django_db
def test_case_severity_filter_combines_with_scope_and_mine(
    browser: Client,
    org: Organisation,
    analyst: User,
    colleague: User,
    case_status: CaseStatus,
) -> None:
    _case(org, case_status, "My open high", severity=3, assignee=analyst)
    _case(org, case_status, "My closed high", severity=3, assignee=analyst, closed=True)
    _case(org, case_status, "My open low", severity=1, assignee=analyst)
    _case(org, case_status, "Colleague open high", severity=3, assignee=colleague)

    all_response = browser.get(
        reverse("ui-case-list"), {"scope": "all", "mine": "1", "severity": "3"}
    )
    open_response = browser.get(
        reverse("ui-case-list"), {"scope": "open", "mine": "1", "severity": "3"}
    )

    assert _titles(all_response) == {"My open high", "My closed high"}
    assert _titles(open_response) == {"My open high"}


@pytest.mark.django_db
def test_case_severity_toggle_links_are_accessible_and_preserve_filters(
    browser: Client, org: Organisation, case_status: CaseStatus
) -> None:
    _case(org, case_status, "Filtered case", severity=2, closed=True)

    response = browser.get(
        reverse("ui-case-list"),
        {"scope": "closed", "mine": "1", "per_page": "25", "page": "2", "severity": ["1", "3"]},
    )
    body = response.content.decode()
    anchors = _anchors(body)

    assert len(anchors) == 4
    assert [re.search(r">([^<]+)</a>", a).group(1).strip() for a in anchors] == [
        "Low",
        "Medium",
        "High",
        "Critical",
    ]
    assert _attr(anchors[0], "aria-current") == "true"
    assert _attr(anchors[0], "aria-label") == "Remove Low severity filter"
    assert _attr(anchors[1], "aria-label") == "Add Medium severity filter"
    assert _hidden_severities(body) == ["1", "3"]

    low_query = _query(anchors[0])
    assert low_query["severity"] == ["3"]
    assert low_query["scope"] == ["closed"]
    assert low_query["mine"] == ["1"]
    assert low_query["per_page"] == ["25"]
    assert "page" not in low_query

    medium_query = _query(anchors[1])
    assert medium_query["severity"] == ["1", "2", "3"]
    assert medium_query["scope"] == ["closed"]
    assert medium_query["mine"] == ["1"]
    assert medium_query["per_page"] == ["25"]
    assert "page" not in medium_query


@pytest.mark.django_db
def test_case_severity_filter_counts_as_filter_for_disclosure(
    browser: Client, org: Organisation, case_status: CaseStatus
) -> None:
    for i, severity in enumerate((1, 2, 2, 3), start=1):
        _case(org, case_status, f"Disclosure case {i}", severity=severity)

    response = browser.get(reverse("ui-case-list"), {"scope": "all", "severity": "2"})

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 2
    assert response.context["filtered_from"] == 4
    assert "(filtered from 4)" in response.content.decode()


@pytest.mark.django_db
def test_case_severity_filter_ignores_invalid_values(
    browser: Client, org: Organisation, case_status: CaseStatus
) -> None:
    _case(org, case_status, "Valid case", severity=2)

    response = browser.get(
        reverse("ui-case-list"), {"scope": "all", "severity": ["0", "5", "abc", "2"]}
    )

    assert response.status_code == 200
    assert _titles(response) == {"Valid case"}
    assert response.context["selected_severities"] == [2]


@pytest.mark.django_db
def test_case_severity_filter_empty_state_offers_clear_filters(
    browser: Client, org: Organisation, case_status: CaseStatus
) -> None:
    _case(org, case_status, "Open medium", severity=2)

    response = browser.get(reverse("ui-case-list"), {"severity": "4"})
    body = response.content.decode()

    assert response.context["page_obj"].paginator.count == 0
    assert "No cases match your filters." in body
    assert "Clear filters" in body
    assert "No cases open." not in body
