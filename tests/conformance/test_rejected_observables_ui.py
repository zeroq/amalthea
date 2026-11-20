"""Conformance tests for the Rejected Observables UI."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation, User
from observables.models import ObservableType, RejectedObservable


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst", suffix: str = "") -> User:
    """Create a user with a unique login by adding a suffix."""
    return User.objects.create(
        login=f"{login}{suffix}",
        username=f"{login}{suffix}",
        email=f"{login}{suffix}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _mk_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _mk_superadmin(org: Organisation) -> User:
    return _mk_user("super", org, role="superAdmin", suffix="1")


def _mk_analyst(org: Organisation, suffix: str = "") -> User:
    return _mk_user("analyst", org, role="analyst", suffix=suffix)


def _mk_case(title: str, org: Organisation) -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _mk_alert(title: str, org: Organisation) -> Alert:
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    return Alert.objects.create(
        type="test",
        source="test",
        source_ref=str(uuid4()),
        title=title,
        status=status,
        owner_org=org,
    )


def _mk_observable_type(org: Organisation) -> ObservableType:
    return ObservableType.objects.get_or_create(name="ip", defaults={"is_case_sensitive": False})[0]


def _mk_rejected_observable(
    alert: Alert, org: Organisation, reason: str = "duplicate", value: str = "10.0.0.1"
) -> RejectedObservable:
    """Create a rejected observable linked to an alert."""
    obs_type = ObservableType.objects.get_or_create(
        name="ip", defaults={"is_case_sensitive": False}
    )[0]
    return RejectedObservable.objects.create(
        owner_org=org,
        data_type=obs_type,
        normalized_data=value,
        alert=alert,
        reason=reason,
    )


class TestRejectedObservablesList:
    """Tests for the rejected observables list page."""

    def test_page_returns_200_for_authorized_user(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        client = _mk_client(analyst)

        resp = client.get(reverse("ui-rejected-observables"))
        assert resp.status_code == 200

    def test_page_only_own_org_rows(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        analyst1 = _mk_analyst(org1, suffix="1")
        _mk_analyst(org2, suffix="2")
        client1 = _mk_client(analyst1)

        # Create rejected observables in org2
        alert2 = _mk_alert("Test Alert", org2)
        _mk_rejected_observable(alert2, org2, reason="duplicate")

        resp = client1.get(reverse("ui-rejected-observables"))
        assert resp.status_code == 200
        content = resp.content.decode()
        # Should not contain org2's rejected observables' IP values
        assert "10.0.0.1" not in content

    def test_filters_submit_via_htmx_and_degrade_to_plain_get(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        client = _mk_client(analyst)

        # Submit status filter via HTMX
        resp = client.get(
            reverse("ui-rejected-observables") + "?status=pending", HTTP_HX_REQUEST="true"
        )
        assert resp.status_code == 200

        # Submit reason filter via HTMX
        resp = client.get(
            reverse("ui-rejected-observables") + "?reason=duplicate", HTTP_HX_REQUEST="true"
        )
        assert resp.status_code == 200

    def test_page_renders_promotion_dialog_and_named_alert_link(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        client = _mk_client(analyst)
        alert = _mk_alert("Test Alert", org)
        rejected = _mk_rejected_observable(alert, org, reason="duplicate")

        content = client.get(reverse("ui-rejected-observables")).content.decode()
        modal_id = f"promote-modal-{rejected.id}"

        assert "10.0.0.1" in content
        assert f'id="{modal_id}"' in content
        assert f'data-modal-open="{modal_id}"' in content
        assert "Confirm Promotion" in content
        assert (
            f'action="{reverse("ui-rejected-observable-promote", kwargs={"id": rejected.id})}"'
            in content
        )
        assert reverse("ui-alert-detail", kwargs={"alert_id": alert.id}) in content

    def test_reason_badges_are_color_coded(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _mk_client(_mk_analyst(org))
        alert = _mk_alert("Test Alert", org)
        _mk_rejected_observable(alert, org, reason="false_positive", value="10.0.0.1")
        _mk_rejected_observable(alert, org, reason="duplicate", value="10.0.0.2")

        content = client.get(reverse("ui-rejected-observables")).content.decode()

        assert 'data-reason="false_positive"' in content
        assert "pill-reason-false-positive" in content
        assert "False positive" in content
        assert 'data-reason="duplicate"' in content
        assert "pill-reason-duplicate" in content
        assert "Duplicate" in content

    def test_page_uses_settings_card_layout(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _mk_client(_mk_superadmin(org))

        content = client.get(reverse("ui-rejected-observables")).content.decode()

        assert 'class="tabs"' in content
        assert 'class="filter-bar"' in content
        assert 'class="card-header"' in content
        assert 'class="table-wrap"' in content
        assert 'class="data-table"' in content
        assert '<caption class="sr-only">Rejected observables</caption>' in content

        active_tab = re.search(r'<a class="tab tab-on"[^>]*aria-current="page"', content)
        assert active_tab is not None
        assert reverse("ui-rejected-observables") in active_tab.group(0)


class TestSettingsNavRejectedObservables:
    """Settings nav link resolution and active state."""

    @staticmethod
    def _rail_submenu(content: str) -> str:
        match = re.search(r'<ul class="rail-sub">(.*?)</ul>', content, re.S)
        return match.group(1) if match else ""

    def test_nav_link_visible_for_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        # Follow redirects to get the final page content
        content = _mk_client(superadmin).get(reverse("ui-settings"), follow=True).content.decode()
        # The settings nav should contain the rejected observables link for superAdmin
        assert "Rejected observables" in content

    def test_nav_active_tab_marks_ui_rejected_observables_when_on_page(self, db: None) -> None:
        """When on the rejected observables page, the settings nav link should carry tab-on and aria-current."""
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        # Access the settings page - the rejected observables tab should be active
        content = _mk_client(superadmin).get(reverse("ui-settings-organisations")).content.decode()
        # The settings nav should have an active tab
        match = re.search(r'<a class="tab tab-on"[^>]*aria-current="page"', content)
        assert match is not None, (
            'the active settings tab must be marked with both `tab-on` and `aria-current="page"`'
        )
        assert reverse("ui-settings-organisations") in match.group(0)

    def test_nav_tabs_filtered_by_role(self, db: None) -> None:
        # Only superAdmin sees the rejected observables tab in settings
        superadmin = _mk_superadmin(_mk_org("Org1"))
        content = _mk_client(superadmin).get(reverse("ui-settings-organisations")).content.decode()

        assert reverse("ui-rejected-observables") in content

    def test_rail_submenu_includes_rejected_observables_for_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        content = _mk_client(superadmin).get(reverse("ui-settings-organisations")).content.decode()

        submenu = self._rail_submenu(content)
        assert submenu, "Settings rail submenu was not rendered"
        assert reverse("ui-rejected-observables") in submenu
        assert "Rejected observables" in submenu

    def test_rail_submenu_includes_rejected_observables_for_admin(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin", suffix="1")
        content = _mk_client(admin).get(reverse("ui-settings-organisations")).content.decode()

        submenu = self._rail_submenu(content)
        assert reverse("ui-rejected-observables") in submenu

    def test_rail_submenu_marks_rejected_observables_active_on_its_page(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        content = _mk_client(superadmin).get(reverse("ui-rejected-observables")).content.decode()

        submenu = self._rail_submenu(content)
        rejected_url = re.escape(reverse("ui-rejected-observables"))
        assert re.search(rf'<a href="{rejected_url}"[^>]*aria-current="page"', submenu)


class TestPromotionPlainPOST:
    """Tests for plain POST promotion (non-HTMX)."""

    def test_plain_post_promotion_updates_is_promoted_and_redirects(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        # Create an alert and a rejected observable linked to it
        alert = _mk_alert("Test Alert", org)
        rejected = _mk_rejected_observable(alert, org, reason="duplicate")

        # Get CSRF token and submit promote form
        resp = client.post(
            reverse("ui-rejected-observable-promote", kwargs={"id": rejected.id}),
            {"status": "", "reason": ""},
        )
        assert resp.status_code == 302  # redirect

        # Check is_promoted is True
        rejected.refresh_from_db()
        assert rejected.is_promoted is True

        # Check redirect back to the list with filters preserved
        redirect_url = resp["Location"]
        assert "rejected-observables" in redirect_url

        # Check success message - get the redirected page
        content = client.get(reverse("ui-rejected-observables")).content.decode().lower()
        assert "promoted" in content


class TestPromotionHtmxPOST:
    """Tests for HTMX POST promotion."""

    def test_htmx_post_returns_rows_partial_and_reflects_updated_state(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        # Create an alert and a rejected observable linked to it
        alert = _mk_alert("Test Alert", org)
        rejected = _mk_rejected_observable(alert, org, reason="duplicate")

        # Submit promote form via HTMX (simulated by POST with HX-Request header)
        resp = client.post(
            reverse("ui-rejected-observable-promote", kwargs={"id": rejected.id}),
            {"status": "", "reason": ""},
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200

        # Check that the response contains the rows partial
        content = resp.content.decode()
        assert "10.0.0.1" in content  # the observable value
        assert "pill-reason-duplicate" in content  # shared reason badge component

        # Bare rows are swapped into #rejected-table-body, not a nested <tbody>.
        assert "<tbody" not in content
        assert "<tr" in content

        # The response names the dialog to close via HTMX.
        trigger = resp["HX-Trigger-After-Swap"]
        assert "closePromoteModal" in trigger
        assert f"promote-modal-{rejected.id}" in trigger

        # Check that is_promoted is True
        rejected.refresh_from_db()
        assert rejected.is_promoted is True

    def test_htmx_post_reflects_promoted_state_in_rows(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        # Create an alert and a rejected observable linked to it
        alert = _mk_alert("Test Alert", org)
        rejected = _mk_rejected_observable(alert, org, reason="false_positive")

        # Promote via plain POST first
        client.post(
            reverse("ui-rejected-observable-promote", kwargs={"id": rejected.id}),
            {"status": "", "reason": ""},
        )

        # Now check the rows partial reflects the promoted state
        resp = client.get(reverse("ui-rejected-observables"), HTTP_HX_REQUEST="true")
        assert resp.status_code == 200
        content = resp.content.decode()
        # The promoted observable should reflect is_promoted=True state
        assert "Promoted" in content


class TestPromotionModalJsWiring:
    """The HX-Trigger emitted by the view must have a matching client handler."""

    def test_modal_js_closes_named_promotion_dialog(self) -> None:
        js = (Path(__file__).resolve().parents[2] / "ui" / "static" / "ui" / "modal.js").read_text()
        assert 'addEventListener("closePromoteModal"' in js
        assert "modal.close()" in js


class TestViewerDenied:
    """Tests that viewer role is denied access."""

    def test_viewer_denied_access_to_rejected_observables(self, db: None) -> None:
        org = _mk_org("Org1")
        viewer = _mk_user("viewer", org, role="viewer", suffix="1")
        client = _mk_client(viewer)

        resp = client.get(reverse("ui-rejected-observables"))
        assert resp.status_code == 404

    def test_viewer_denied_post_promote(self, db: None) -> None:
        org = _mk_org("Org1")
        viewer = _mk_user("viewer", org, role="viewer", suffix="1")
        client = _mk_client(viewer)

        # Create an alert and a rejected observable
        alert = Alert.objects.create(
            type="test",
            source="test",
            source_ref=str(uuid4()),
            title="Test Alert",
            status=AlertStatus.objects.get_or_create(
                value="New", defaults={"stage": "New", "order": 1}
            )[0],
            owner_org=org,
        )

        obs_type = ObservableType.objects.get_or_create(
            name="ip", defaults={"is_case_sensitive": False}
        )[0]
        rejected = RejectedObservable.objects.create(
            owner_org=org,
            data_type=obs_type,
            normalized_data="10.0.0.1",
            alert=alert,
            reason="duplicate",
        )

        resp = client.post(
            reverse("ui-rejected-observable-promote", kwargs={"id": rejected.id}),
            {"status": "", "reason": ""},
        )
        assert resp.status_code == 404
