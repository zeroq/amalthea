"""UI conformance tests for the alert-list severity filter toggles.

Frontend-focused: verifies the template HTML output, CSS classes,
accessible names, hidden inputs, and Clear behaviour.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User

# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.create(name="Severity UI test org")


@pytest.fixture
def other_org(db: None) -> Organisation:
    return Organisation.objects.create(name="Severity UI other org")


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
def analyst(org: Organisation) -> User:
    return _user(org, "alert-scope-analyst")


@pytest.fixture
def colleague(org: Organisation) -> User:
    return _user(org, "alert-scope-colleague")


def _status(value: str, stage: str, order: int = 1) -> AlertStatus:
    return AlertStatus.objects.get_or_create(
        value=value, defaults={"stage": stage, "order": order}
    )[0]


def _alert(
    org: Organisation,
    status: AlertStatus,
    source_ref: str,
    *,
    case: object | None = None,
    assignee: User | None = None,
    severity: int = 1,
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


def _body(response) -> str:
    return response.content.decode()


def _client(org: Organisation, user: User | None = None) -> Client:
    """Create a force-logged-in Client for the given org/user."""
    c = Client()
    if user is None:
        user = _user(org, "testuser")
    c.force_login(user)
    return c


def _find_anchors_with_class(body: str, cls: str) -> list[str]:
    """Return raw HTML strings of all <a> tags containing ``cls`` in class attr.

    Uses a permissive pattern that handles arbitrary attribute order and
    multiline HTML.
    """
    results: list[str] = []
    # Match <a ... class="...*cls*...* >...content...</a>
    pattern = r'<a\b[^>]*\bclass="[^"]*' + re.escape(cls) + r'[^"]*"[^>]*>(.*?)<\/a>'
    for m in re.finditer(pattern, body, re.S):
        results.append(m.group(0))
    return results


def _extract_inner_text(anchor_html: str) -> str:
    """Extract the text between > and </a> from an anchor tag."""
    m = re.search(r">(.*?)<\/a>", anchor_html, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def _extract_attr(anchor_html: str, attr: str) -> str:
    """Extract the value of a given attribute from an anchor tag.

    Returns empty string if not found.
    """
    m = re.search(rf'{attr}="([^"]*)"', anchor_html)
    return html.unescape(m.group(1)) if m else ""


def _find_hidden_inputs(body: str, name: str) -> list[dict]:
    """Extract hidden inputs with the given ``name``.

    Returns list of dicts with keys: 'name', 'value'.
    """
    results: list[dict] = []
    # Find all <input type="hidden" ... name="..." ...>
    for m in re.finditer(r'<input\b[^>]*type="hidden"[^>]*>', body, re.S):
        tag = m.group(0)
        name_m = re.search(r'name="([^"]*)"', tag)
        if not name_m or html.unescape(name_m.group(1)) != name:
            continue
        value_m = re.search(r'value="([^"]*)"', tag)
        if value_m:
            results.append({"name": name, "value": html.unescape(value_m.group(1))})
    return results


def _has_severity_filter_row(body: str) -> bool:
    """Check whether the body contains a severity filter row."""
    return bool(re.search(r'class="[^"]*severity-filter-row[^"]*"', body))


# --------------------------------------------------------------------------
# UI conformance tests
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_ui_four_severity_controls_in_fixed_order(analyst, org: Organisation) -> None:
    """Exactly four severity controls rendered in fixed Low/Medium/High/Critical order."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)
    _alert(org, new, "ui-test-high", severity=3)

    response = c.get(reverse("ui-alert-list"))
    assert response.status_code == 200
    body = _body(response)

    anchors = _find_anchors_with_class(body, "severity-filter")
    assert len(anchors) == 4, f"Expected 4 severity controls, got {len(anchors)}"

    # Verify fixed order: Low, Medium, High, Critical (from the anchor inner text)
    labels = [_extract_inner_text(a) for a in anchors]
    assert labels == ["Low", "Medium", "High", "Critical"], (
        f"Expected order Low/Medium/High/Critical, got {labels}"
    )


@pytest.mark.django_db
def test_ui_selected_has_aria_current_and_accessible_name(analyst, org: Organisation) -> None:
    """Selected button has aria-current="true" and accessible name 'Remove <Label> severity filter'.
    Inactive button has 'Add <Label> severity filter'."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)  # Create alert with severity=1

    # Fetch page with severity=1 selected - Low should be selected
    response = c.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    body = _body(response)

    anchors = _find_anchors_with_class(body, "severity-filter")
    assert len(anchors) == 4, f"Expected 4 anchors, got {len(anchors)}"

    # Low (selected) should have aria-current="true" and aria-label "Remove Low severity filter"
    low = anchors[0]
    aria_current = _extract_attr(low, "aria-current")
    aria_label = _extract_attr(low, "aria-label")

    assert aria_current == "true", (
        f"Low severity link missing aria-current=true, got aria-current='{aria_current}'"
    )
    assert aria_label == "Remove Low severity filter", (
        f"Expected 'Remove Low severity filter', got '{aria_label}'"
    )

    # Medium (inactive) should have aria-label "Add Medium severity filter"
    medium = anchors[1]
    aria_label_med = _extract_attr(medium, "aria-label")
    assert aria_label_med == "Add Medium severity filter", (
        f"Expected 'Add Medium severity filter', got '{aria_label_med}'"
    )


@pytest.mark.django_db
def test_ui_hidden_inputs_match_selected_and_inside_form(analyst, org: Organisation) -> None:
    """Hidden <input type="hidden" name="severity" value="<n>"> for each selected severity,
    inside the filter form."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)  # Create alert with severity=1

    # Fetch page with severity=1 selected - this should render a hidden input
    response = c.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    body = _body(response)

    hidden = _find_hidden_inputs(body, "severity")
    # With severity=1 selected, there should be one hidden input with value="1"
    assert len(hidden) == 1, f"Expected 1 hidden severity input, got {len(hidden)}: {hidden}"
    assert hidden[0] == {"name": "severity", "value": "1"}, (
        f"Expected ('severity', '1'), got {hidden}"
    )


@pytest.mark.django_db
def test_ui_no_counts_on_severity_buttons(analyst, org: Organisation) -> None:
    """No count/badges rendered inside severity filter buttons."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)

    response = c.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    body = _body(response)

    anchors = _find_anchors_with_class(body, "severity-filter")
    for a in anchors:
        inner = _extract_inner_text(a)
        # Should not contain patterns like " (3)" or similar count indicators
        assert "(" not in inner, f"Button inner HTML appears to contain a count: '{inner}'"


@pytest.mark.django_db
def test_ui_no_inline_script_style(analyst, org: Organisation) -> None:
    """No inline <script> or <style> blocks in the severity filter section.

    Checks that there are no bare <script> or <style> tags (as opposed to
    external resource includes like <script src="...">).
    """
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)

    response = c.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    body = _body(response)

    # No inline <script> blocks (tags without src attribute)
    inline_script = re.findall(r"<script(?!.*\bsrc\b)[^>]*>", body, re.S)
    assert not inline_script, f"Inline <script> blocks found: {inline_script}"

    # No inline <style> blocks (tags without media attribute or content)
    inline_style = re.findall(r"<style[^>]*>(?:(?!</style>).)*</style>", body, re.S)
    assert not inline_style, f"Inline <style> blocks found: {inline_style}"


@pytest.mark.django_db
def test_ui_clear_removes_all_filters(analyst, org: Organisation) -> None:
    """Clear link removes all filters including severity."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)

    # Fetch page with severity=1 selected
    response = c.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    body = _body(response)

    clear = re.search(r"<a\b[^>]*>Clear</a>", body)
    assert clear is not None, "Clear anchor not found in body"

    # Follow the Clear link - it should go to the base URL without any filters
    clear_href = html.unescape(re.search(r'href="([^"]+)"', clear.group(0)).group(1))
    clear_query = parse_qs(urlsplit(clear_href).query)
    # Clear should remove severity, mine, and scope parameters
    assert "severity" not in clear_query, (
        f"Clear link should remove severity param, but found: {clear_query}"
    )


@pytest.mark.django_db
def test_ui_visible_severity_label(analyst, org: Organisation) -> None:
    """Visible 'Severity' label is rendered in the filter row."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)

    response = c.get(reverse("ui-alert-list"), {"severity": "1"})
    assert response.status_code == 200
    body = _body(response)

    # The labelled group contains a visible 'Severity' label
    assert 'class="dim"' in body or 'class="severity-filter-label"' in body, (
        "Visible Severity label not found in body"
    )
    # Check for the text "Severity" in the label
    assert "Severity" in body, "Visible 'Severity' text not found in body"


@pytest.mark.django_db
def test_ui_multiple_selected_severities_two_hidden_inputs(analyst, org: Organisation) -> None:
    """Two selected severities render two hidden inputs inside the filter form."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "ui-test", severity=1)
    _alert(org, new, "ui-test-high", severity=3)

    # Select both Low and High via query string
    response = c.get(reverse("ui-alert-list"), {"severity": ["1", "3"]})
    assert response.status_code == 200
    body = _body(response)

    # Should have exactly two hidden inputs with values 1 and 3
    hidden = _find_hidden_inputs(body, "severity")
    assert len(hidden) == 2, f"Expected 2 hidden severity inputs, got {len(hidden)}: {hidden}"
    # Both should be inside the filter form (check they appear in the body)
    values = {h["value"] for h in hidden}
    assert "1" in values, "Hidden input with value='1' not found"
    assert "3" in values, "Hidden input with value='3' not found"


@pytest.mark.django_db
def test_ui_filter_form_low_high_url_integration(analyst, org: Organisation) -> None:
    """Existing Filter form / Low+High URL integration renders expected selected state."""
    new = _status("New", "New")
    c = _client(org, analyst)
    _alert(org, new, "low", severity=1)
    _alert(org, new, "high", severity=3)

    # Select Low + High via query string (as a list, not encoded string)
    response = c.get(reverse("ui-alert-list"), {"severity": ["1", "3"]})
    assert response.status_code == 200
    body = _body(response)

    # Find anchor tags with severity-filter class and check their aria-current
    anchors = _find_anchors_with_class(body, "severity-filter")
    assert len(anchors) == 4, f"Expected 4 anchors, got {len(anchors)}"

    # Low (first anchor) should have aria-current="true"
    low = anchors[0]
    low_aria_current = _extract_attr(low, "aria-current")
    assert low_aria_current == "true", (
        f"Low severity should have aria-current='true', got '{low_aria_current}'"
    )

    # High (third anchor) should have aria-current="true"
    high = anchors[2]
    high_aria_current = _extract_attr(high, "aria-current")
    assert high_aria_current == "true", (
        f"High severity should have aria-current='true', got '{high_aria_current}'"
    )

    # Low should have inner text "Low" and data-severity "1"
    low = anchors[0]
    low_inner = _extract_inner_text(low)
    low_ds = _extract_attr(low, "data-severity")
    assert low_inner == "Low", f"Expected 'Low' inner text, got '{low_inner}'"
    assert low_ds == "1", f"Expected data-severity='1', got '{low_ds}'"

    # High should have inner text "High" and data-severity "3"
    high = anchors[2]
    high_inner = _extract_inner_text(high)
    high_ds = _extract_attr(high, "data-severity")
    assert high_inner == "High", f"Expected 'High' inner text, got '{high_inner}'"
    assert high_ds == "3", f"Expected data-severity='3', got '{high_ds}'"
