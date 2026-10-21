"""Conformance tests for Primary Rail Visual Grouping (PLAN-2026-10-21-rail-grouping).

Tests:
- Exactly two .rail-sep rows render for a logged-in user
- Each separator is aria-hidden="true" and contains no <a> (non-focusable)
- Group boundaries by ordered labels: sep #1 after Cases / before Observables;
  sep #2 after Playbooks / before Sources
- Falsifiability proof: with separators removed, positive assertions fail
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import Client
from django.urls import reverse

from identity.models import Organisation, User


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
    user = User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )
    return user


def _mk_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _mk_superadmin(org: Organisation) -> User:
    return _mk_user("super", org, role="superAdmin")


def _mk_analyst(org: Organisation) -> User:
    return _mk_user("analyst", org, role="analyst")


def _extract_rail_sequence(content: str) -> list[dict]:
    """Extract the ordered sequence of rail items and separators from rendered HTML.

    Returns a list of dicts with keys:
      - 'type': 'item' or 'sep'
      - 'label': for items, the visible label text (e.g., 'Cases', 'Observables')
      - 'has_aria_hidden': for separators, whether aria-hidden="true" is present
      - 'has_anchor': for separators, whether an <a> tag is nested inside
    """
    # Find the <ul class="rail-links"> block
    rail_start = content.find('<ul class="rail-links">')
    if rail_start == -1:
        return []

    # Find the closing </ul> for this list (not nested rail-sub)
    rail_end = content.find("</ul>", rail_start)
    if rail_end == -1:
        return []

    rail_html = content[rail_start:rail_end]

    # Find all <li> elements at the top level of rail-links (not inside rail-sub)
    # We'll parse by looking for <li> tags and their content
    sequence = []

    # Pattern to find each <li>...</li> at the top level
    # This is a simple regex approach; for robustness we look for <li> not preceded by rail-sub
    li_pattern = re.compile(r"<li([^>]*)>(.*?)</li>", re.DOTALL)
    for match in li_pattern.finditer(rail_html):
        li_attrs = match.group(1)
        li_content = match.group(2).strip()

        # Skip if this is inside a nested rail-sub (indented differently in practice)
        # But in the flat HTML, we just check if it's a separator or a link
        if 'class="rail-sep"' in li_attrs:
            has_aria_hidden = 'aria-hidden="true"' in li_attrs
            has_anchor = "<a " in li_content
            sequence.append(
                {
                    "type": "sep",
                    "has_aria_hidden": has_aria_hidden,
                    "has_anchor": has_anchor,
                }
            )
        else:
            # Extract label from the anchor text
            anchor_match = re.search(r"<a[^>]*>(.*?)</a>", li_content, re.DOTALL)
            if anchor_match:
                anchor_content = anchor_match.group(1)
                # Remove icons and kbd spans, keep visible text
                label = re.sub(r"<[^>]+>", "", anchor_content).strip()
                # The label ends at the first whitespace after the shortcut, but we want full text
                # e.g., "g Dashboard" -> "Dashboard", "a Alerts (12)" -> "Alerts"
                # Extract the word after the shortcut character
                label = re.sub(r"^\w\s*", "", label).strip()
                # Remove badge numbers
                label = re.sub(r"\s*\(\d+\)$", "", label).strip()
                sequence.append({"type": "item", "label": label})

    return sequence


class TestRailGrouping:
    """Test the visual grouping separators in the primary navigation rail."""

    def test_exactly_two_separators_render(self, db: None) -> None:
        """Exactly two rail-sep rows render for a logged-in user."""
        org = _mk_org("Org1")
        user = _mk_analyst(org)
        client = _mk_client(user)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        content = resp.content.decode()

        seq = _extract_rail_sequence(content)
        separators = [item for item in seq if item["type"] == "sep"]
        assert len(separators) == 2, f"Expected 2 separators, got {len(separators)}"

    def test_separators_are_aria_hidden_and_non_focusable(self, db: None) -> None:
        """Each separator has aria-hidden="true" and contains no <a> tag."""
        org = _mk_org("Org1")
        user = _mk_analyst(org)
        client = _mk_client(user)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        content = resp.content.decode()

        seq = _extract_rail_sequence(content)
        separators = [item for item in seq if item["type"] == "sep"]

        for sep in separators:
            assert sep["has_aria_hidden"] is True, "Separator must have aria-hidden='true'"
            assert sep["has_anchor"] is False, (
                "Separator must not contain an <a> tag (non-focusable)"
            )

    def test_separator_group_boundaries(self, db: None) -> None:
        """Verify separator positions: #1 after Cases/before Observables, #2 after Playbooks/before Sources."""
        org = _mk_org("Org1")
        user = _mk_analyst(org)
        client = _mk_client(user)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        content = resp.content.decode()

        seq = _extract_rail_sequence(content)

        # Extract just the item labels in order, with separator markers
        labels_and_seps = []
        for item in seq:
            if item["type"] == "item":
                labels_and_seps.append(item["label"])
            else:
                labels_and_seps.append("---SEP---")

        # Expected sequence with separators at correct positions
        # Dashboard, Alerts, Cases, ---SEP---, Observables, Automation, Playbooks, ---SEP---, Sources, Tags, Case templates, Taxonomy, Settings
        assert "Cases" in labels_and_seps, "Cases label must be present"
        assert "Observables" in labels_and_seps, "Observables label must be present"
        assert "Playbooks" in labels_and_seps, "Playbooks label must be present"
        assert "Sources" in labels_and_seps, "Sources label must be present"

        # Find positions
        cases_idx = labels_and_seps.index("Cases")
        observables_idx = labels_and_seps.index("Observables")
        playbooks_idx = labels_and_seps.index("Playbooks")
        sources_idx = labels_and_seps.index("Sources")

        # Separator #1 must be between Cases and Observables
        sep1_idx = labels_and_seps.index("---SEP---")
        assert cases_idx < sep1_idx < observables_idx, (
            f"First separator must be after Cases (idx {cases_idx}) and before Observables (idx {observables_idx}), "
            f"but found at idx {sep1_idx}. Full sequence: {labels_and_seps}"
        )

        # Separator #2 must be between Playbooks and Sources
        sep2_idx = labels_and_seps.index("---SEP---", sep1_idx + 1)
        assert playbooks_idx < sep2_idx < sources_idx, (
            f"Second separator must be after Playbooks (idx {playbooks_idx}) and before Sources (idx {sources_idx}), "
            f"but found at idx {sep2_idx}. Full sequence: {labels_and_seps}"
        )

        # Ensure there are exactly 2 separators
        assert labels_and_seps.count("---SEP---") == 2, "Must have exactly 2 separators"

    def test_falsifiability_separators_removed_assertions_fail(self, db: None) -> None:
        """Falsifiability proof: remove separators from *rendered HTML*, confirm assertions fail.

        This test:
        1. Renders the real page (with separators)
        2. Removes the two <li class="rail-sep"> elements from the HTML string
        3. Runs the three positive assertions on the sabotaged HTML — they MUST fail
        4. Verifies the original template file is unchanged by this test (byte-identical before/after)
        """
        import hashlib

        # Read the template file BEFORE any rendering (source of truth)
        template_path = (
            Path(__file__).resolve().parents[2] / "ui" / "templates" / "ui" / "base.html"
        )
        template_content_before = template_path.read_bytes()
        template_hash_before = hashlib.sha256(template_content_before).hexdigest()

        # 1. Render the real page
        org = _mk_org("Org1")
        user = _mk_analyst(org)
        client = _mk_client(user)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        original_html = resp.content.decode()

        # 2. Sabotage the rendered HTML by removing the two separator <li> elements
        sabotaged_html = original_html.replace('<li class="rail-sep" aria-hidden="true"></li>', "")

        # Verify sabotage worked
        assert sabotaged_html.count('class="rail-sep"') == 0, "Sabotage should remove all rail-sep"

        # 3. Run assertions on sabotaged HTML — they MUST fail

        # Assertion 1: exactly two separators — should FAIL (0 separators)
        seq = _extract_rail_sequence(sabotaged_html)
        separators = [item for item in seq if item["type"] == "sep"]
        assert len(separators) != 2, (
            "FALSIFIABILITY FAIL: Sabotaged HTML has 0 separators, but assertion "
            "'exactly 2 separators' would incorrectly pass"
        )

        # Assertion 2: separators have aria-hidden and no anchor — vacuously passes with 0
        # separators, so we explicitly check that there are NO separators at all
        assert len(separators) == 0, "Sabotaged HTML should have no separators"

        # Assertion 3: group boundaries — should FAIL because no separators exist
        labels_and_seps = []
        for item in seq:
            if item["type"] == "item":
                labels_and_seps.append(item["label"])
            else:
                labels_and_seps.append("---SEP---")

        sep_count = labels_and_seps.count("---SEP---")
        assert sep_count != 2, (
            "FALSIFIABILITY FAIL: Sabotaged HTML has 0 separators, but group boundary "
            "assertion would incorrectly pass"
        )

        # 4. Verify the original template file is unchanged BY THIS TEST (byte-identical before/after)
        template_content_after = template_path.read_bytes()
        template_hash_after = hashlib.sha256(template_content_after).hexdigest()

        assert template_hash_before == template_hash_after, (
            f"Test modified the template file! Before: {template_hash_before[:16]}..., "
            f"After: {template_hash_after[:16]}..."
        )

        # Note: git diff may show changes from the feature implementation itself, which is expected.
        # This test only ensures the test doesn't further modify the file.


class TestRailGroupingSuperAdmin:
    """Same tests for superAdmin user (who sees Settings item)."""

    def test_exactly_two_separators_render_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        user = _mk_superadmin(org)
        client = _mk_client(user)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        content = resp.content.decode()

        seq = _extract_rail_sequence(content)
        separators = [item for item in seq if item["type"] == "sep"]
        assert len(separators) == 2

    def test_separator_boundaries_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        user = _mk_superadmin(org)
        client = _mk_client(user)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        content = resp.content.decode()

        seq = _extract_rail_sequence(content)
        labels_and_seps = []
        for item in seq:
            if item["type"] == "item":
                labels_and_seps.append(item["label"])
            else:
                labels_and_seps.append("---SEP---")

        cases_idx = labels_and_seps.index("Cases")
        observables_idx = labels_and_seps.index("Observables")
        playbooks_idx = labels_and_seps.index("Playbooks")
        sources_idx = labels_and_seps.index("Sources")

        sep1_idx = labels_and_seps.index("---SEP---")
        assert cases_idx < sep1_idx < observables_idx

        sep2_idx = labels_and_seps.index("---SEP---", sep1_idx + 1)
        assert playbooks_idx < sep2_idx < sources_idx

        assert labels_and_seps.count("---SEP---") == 2
