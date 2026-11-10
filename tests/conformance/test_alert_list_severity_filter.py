"""Conformance tests for the alert-list severity filter toggles.

Backend-focused: verifies parse_severity_filter, build_severity_filter_links,
and the alert_list view's severity filtering + context contract.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.http import QueryDict
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from ui.severity_filter import build_severity_filter_links, parse_severity_filter

# --------------------------------------------------------------------------
# Fixtures - module-level so they're available to every test
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> object:
    from identity.models import Organisation

    return Organisation.objects.create(name="Severity test org")


@pytest.fixture
def other_org(db: None) -> object:
    from identity.models import Organisation

    return Organisation.objects.create(name="Severity test other org")


def _user(org, username: str, *, role: str = "analyst") -> object:
    from identity.models import User

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
def analyst(db: None, org: object) -> object:
    return _user(org, "sev-analyst")


def _make_client_from_fixture(analyst, org):
    """Build a client from the pytest fixtures."""
    client = Client()
    client.force_login(analyst)
    return client


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _status(value: str, stage: str, order: int = 1) -> AlertStatus:
    return AlertStatus.objects.get_or_create(
        value=value, defaults={"stage": stage, "order": order}
    )[0]


def _alert(
    org,
    status: AlertStatus,
    source_ref: str,
    *,
    case: object | None = None,
    assignee: object | None = None,
    severity: int = 2,
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
        severity=severity,
    )


def _titles(response) -> set[str]:
    return {alert.title for alert in response.context["page_obj"].object_list}


def _body(response) -> str:
    return response.content.decode()


def _find_anchor_with_label(body: str, label: str) -> str | None:
    import re

    match = re.search(rf"<a\b[^>]*>(.*?{re.escape(label)}[^<]*)</a>", body, re.S)
    return match.group(0) if match else None


def _anchor_href(anchor: str) -> str | None:
    m = re.search(r'href="([^"]+)"', anchor)
    return m.group(1) if m else None


def _query_from_href(href: str | None) -> dict[str, list[str]]:
    if href is None:
        return {}
    return parse_qs(urlsplit(href).query)


# --------------------------------------------------------------------------
# parse_severity_filter
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_parse_no_filter_returns_empty_list():
    q = QueryDict(mutable=True)
    assert parse_severity_filter(q) == []


@pytest.mark.django_db
def test_parse_single_valid():
    q = QueryDict("severity=2", mutable=True)
    assert parse_severity_filter(q) == [2]


@pytest.mark.django_db
def test_parse_multi_or():
    q = QueryDict("severity=1&severity=3", mutable=True)
    assert parse_severity_filter(q) == [1, 3]


@pytest.mark.django_db
def test_parse_ignores_invalid():
    q = QueryDict("severity=abc&severity=0&severity=5&severity=2", mutable=True)
    assert parse_severity_filter(q) == [2]


@pytest.mark.django_db
def test_parse_dedupe_and_ascending():
    q = QueryDict("severity=3&severity=1&severity=3&severity=2", mutable=True)
    assert parse_severity_filter(q) == [1, 2, 3]


# --------------------------------------------------------------------------
# build_severity_filter_links
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_links_four_items_fixed_order():
    from django.http import HttpRequest

    request = HttpRequest()
    request.path = "/ui/alerts/"
    request.GET = QueryDict("severity=1")
    links = build_severity_filter_links(request, [1])
    assert len(links) == 4
    assert links[0]["value"] == 1
    assert links[1]["value"] == 2
    assert links[2]["value"] == 3
    assert links[3]["value"] == 4
    assert all("label" in link for link in links)
    assert all("selected" in link for link in links)
    assert all("url" in link for link in links)


@pytest.mark.django_db
def test_links_selected_state():
    from django.http import HttpRequest

    request = HttpRequest()
    request.path = "/ui/alerts/"
    request.GET = QueryDict("severity=1&severity=3")
    links = build_severity_filter_links(request, [1, 3])
    assert links[0]["selected"] is True  # Low selected
    assert links[1]["selected"] is False  # Medium
    assert links[2]["selected"] is True  # High
    assert links[3]["selected"] is False  # Critical


@pytest.mark.django_db
def test_links_toggle_urls_deterministic():
    """Verify that toggle URLs are deterministic when sorted values go to setlist."""
    from django.http import HttpRequest

    # Start with two severities selected
    request = HttpRequest()
    request.path = "/ui/alerts/"
    request.GET = QueryDict("severity=1&severity=3")
    links = build_severity_filter_links(request, [1, 3])

    # Low (1) is selected; toggling it should remove it.
    low_link = links[0]
    # The url should not contain severity=1 (it should be removed)
    # and should contain severity=3 (the other selected value)
    url_has_1 = "severity=1" in low_link["url"]
    url_has_3 = "severity=3" in low_link["url"]
    # Since we sort before setlist, the order in the URL should be deterministic
    assert not url_has_1, f"Low toggle URL should not contain severity=1, got: {low_link['url']}"
    assert url_has_3, f"Low toggle URL should retain severity=3, got: {low_link['url']}"
    # High (3) is selected; toggling it should remove it.
    high_link = links[2]
    assert "severity=3" not in high_link["url"], (
        f"High toggle URL should not contain severity=3, got: {high_link['url']}"
    )
    # Low should remain
    assert "severity=1" in high_link["url"], (
        f"High toggle URL should retain severity=1, got: {high_link['url']}"
    )


# --------------------------------------------------------------------------
# alert_list view integration - using analyst/or fixtures
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_severity_filter_provides_context(analyst, org):
    """alert_list provides selected_severities and severity_filter_links in context."""
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel

    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]
    _alert = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref1",
        title="test-ref",
        owner_org=org,
        status=new,
        severity=1,
    )

    client = _make_client_from_fixture(analyst, org)
    response = client.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    assert "selected_severities" in response.context
    assert "severity_filter_links" in response.context
    assert isinstance(response.context["selected_severities"], list)
    assert len(response.context["severity_filter_links"]) == 4
    for link in response.context["severity_filter_links"]:
        assert "value" in link
        assert "label" in link
        assert "selected" in link
        assert "url" in link
        assert isinstance(link["value"], int)
        assert isinstance(link["url"], str)


@pytest.mark.django_db
def test_severity_filtering_applies_after_org_scope(analyst, org):
    """Severity filter runs after org and scope filters."""
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel

    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]
    _alert = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref1",
        title="low-alert",
        owner_org=org,
        status=new,
        severity=1,
    )
    _alert2 = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref2",
        title="high-alert",
        owner_org=org,
        status=new,
        severity=3,
    )

    # With severity=1, only low-alert should appear
    client = _make_client_from_fixture(analyst, org)
    response = client.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    titles = {a.title for a in response.context["page_obj"].object_list}
    assert "low-alert" in titles
    assert "high-alert" not in titles


@pytest.mark.django_db
def test_invalid_severity_no_500(analyst, org):
    """Invalid severity values never cause a 500 error."""
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel

    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]
    _alert = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref1",
        title="valid-alert",
        owner_org=org,
        status=new,
        severity=1,
    )

    for bad in ("abc", "0", "5", "", "3.5", "-1"):
        response = _make_client_from_fixture(analyst, org).get(
            reverse("ui-alert-list"), {"severity": bad}
        )
        assert response.status_code == 200, f"Bad severity '{bad}' returned {response.status_code}"
        assert response.context["selected_severities"] is not None


@pytest.mark.django_db
def test_organisation_isolation(analyst, org, other_org):
    """Severity filter does not leak across organisations."""
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel

    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]
    _alert = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref1",
        title="org-low",
        owner_org=org,
        status=new,
        severity=1,
    )
    _alert2 = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref2",
        title="other-org-low",
        owner_org=other_org,
        status=new,
        severity=1,
    )

    client = _make_client_from_fixture(analyst, org)
    response = client.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    titles = {a.title for a in response.context["page_obj"].object_list}
    assert titles == {"org-low"}


@pytest.mark.django_db
def test_fixed_order_when_no_matches(analyst, org):
    """All four severity controls present in fixed order even when zero alerts match."""
    client = _make_client_from_fixture(analyst, org)
    response = client.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    links = response.context["severity_filter_links"]
    assert len(links) == 4
    assert links[0]["value"] == 1
    assert links[1]["value"] == 2
    assert links[2]["value"] == 3
    assert links[3]["value"] == 4


@pytest.mark.django_db
def test_severity_or_filtering(analyst, org):
    """?severity=1&severity=3 returns Low OR High (OR semantics)."""
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel

    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]
    _alert = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref1",
        title="low-alert",
        owner_org=org,
        status=new,
        severity=1,
    )
    _alert2 = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref2",
        title="high-alert",
        owner_org=org,
        status=new,
        severity=3,
    )
    _alert3 = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref3",
        title="med-alert",
        owner_org=org,
        status=new,
        severity=2,
    )
    _alert4 = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref4",
        title="crit-alert",
        owner_org=org,
        status=new,
        severity=4,
    )

    client = _make_client_from_fixture(analyst, org)
    response = client.get(reverse("ui-alert-list"), {"severity": ["1", "3"]})
    assert response.status_code == 200
    titles = {a.title for a in response.context["page_obj"].object_list}
    assert titles == {"low-alert", "high-alert"}


@pytest.mark.django_db
def test_severity_intersects_with_scope(analyst, org):
    """Severity filter intersects with scope predicates.

    Creates one alert linked to a case (linked-open) and one unattached (unlinked-open),
    both with Low severity. Requesting linked_open+severity=1 should show only the linked one.
    """
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel
    from cases.models import Case, CaseStatus

    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]

    # Create a case for the linked alert
    case = Case.objects.create(
        title="linked case",
        status=CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
            0
        ],
        owner_org=org,
        tlp=2,
        pap=2,
    )

    # Linked alert (attached to case)
    _alert_linked = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref-linked",
        title="linked-low",
        owner_org=org,
        status=new,
        severity=1,
        case=case,
    )

    # Unlinked alert (no case)
    _alert_unlinked = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref-unlinked",
        title="unlinked-low",
        owner_org=org,
        status=new,
        severity=1,
        case=None,
    )

    client = _make_client_from_fixture(analyst, org)
    response = client.get(reverse("ui-alert-list"), {"scope": "linked_open", "severity": "1"})
    assert response.status_code == 200
    titles = _titles(response)
    assert titles == {"linked-low"}, f"Expected only linked-low, got {titles}"


@pytest.mark.django_db
def test_severity_intersects_with_mine(analyst, org):
    """Severity filter intersects with mine=1."""
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel

    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]
    _alert = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref1",
        title="mine-low",
        owner_org=org,
        status=new,
        severity=1,
        assignee=analyst,
    )
    _alert2 = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref2",
        title="mine-other",
        owner_org=org,
        status=new,
        severity=2,
        assignee=analyst,
    )

    client = _make_client_from_fixture(analyst, org)
    response = client.get(reverse("ui-alert-list"), {"mine": "1", "severity": "1"})
    assert response.status_code == 200
    titles = {a.title for a in response.context["page_obj"].object_list}
    assert titles == {"mine-low"}


@pytest.mark.django_db
def test_viewer_can_use_severity_filter(analyst, org):
    """Viewer access: viewer can see the list with severity filtering."""
    from alerts.models import Alert as AlertModel
    from alerts.models import AlertStatus as AlertStatusModel
    from identity.models import User

    viewer = User.objects.create_user(
        username="viewer1",
        login="viewer1",
        password="pw-viewer1",
        email="viewer1@example.net",
        org=org,
        role="viewer",
        is_active=True,
    )
    new = AlertStatusModel.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )[0]
    _alert = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref1",
        title="viewer-low",
        owner_org=org,
        status=new,
        severity=1,
    )
    _alert2 = AlertModel.objects.create(
        type="phishing",
        source="test",
        source_ref="ref2",
        title="viewer-other",
        owner_org=org,
        status=new,
        severity=2,
    )

    viewer_client = _make_client_from_fixture(viewer, org)
    response = viewer_client.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    assert "selected_severities" in response.context
    assert "severity_filter_links" in response.context
