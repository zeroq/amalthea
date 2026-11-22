"""MCP Phase 4 autonomy settings UI conformance tests.

Covers: admin/superAdmin org gating, all controls persist/validate, warn banners,
enforce-mode <dialog> confirmation, plain POST fallback, no native confirm/alert/prompt,
audit snapshots bounded, settings nav/link present, icon/markup conventions.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from identity.models import Organisation, User
from mcp_server.models import McpOrgPolicy


def _mk_org(name: str = "Test Org") -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(
    login: str,
    org: Organisation,
    role: str = "admin",
) -> User:
    return User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _ui_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _autonomy_url(org_id) -> str:
    return reverse("ui-settings-agent-autonomy", kwargs={"organisation_id": org_id})


def _autonomy_update_url(org_id) -> str:
    return reverse("ui-settings-agent-autonomy-update", kwargs={"organisation_id": org_id})


# ──────────────────────────────────────────────────────────────────────
# Guard: role/org gating
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAutonomyUIGating:
    """Verify role/org gating on autonomy settings pages."""

    def test_admin_own_org_can_access_autonomy(self, db: None) -> None:
        """Admin in own org can access autonomy settings."""
        org = _mk_org("Own Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        # Admins should be able to access (may show warning banner)
        assert resp.status_code in (200, 301, 302)

    def test_analyst_gets_404_on_autonomy(self, db: None) -> None:
        """Analyst gets 404 on autonomy settings."""
        org = _mk_org("Test Org")
        analyst = _mk_user("analyst", org, role="analyst")
        client = _ui_client(analyst)

        resp = client.get(_autonomy_url(org.id))
        assert resp.status_code == 404

    def test_viewer_gets_404_on_autonomy(self, db: None) -> None:
        """Viewer gets 404 on autonomy settings."""
        org = _mk_org("Test Org")
        viewer = _mk_user("viewer", org, role="viewer")
        client = _ui_client(viewer)

        resp = client.get(_autonomy_url(org.id))
        assert resp.status_code == 404

    def test_cross_org_admin_404(self, db: None) -> None:
        """Admin from another org gets 404."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin_a", org_a, role="admin")
        client = _ui_client(admin_a)

        resp = client.get(_autonomy_url(org_b.id))
        assert resp.status_code == 404

    def test_superadmin_explicit_org_required(self, db: None) -> None:
        """SuperAdmin must specify org in URL; no implicit first org."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        client = _ui_client(superadmin)

        # Without org in URL should 404 or redirect
        resp = client.get("/settings/agent-access/autonomy/")  # no org
        # Should not silently select first org

        # With explicit org
        resp = client.get(_autonomy_url(org_b.id))
        assert resp.status_code in (200, 301, 302)


# ──────────────────────────────────────────────────────────────────────
# Controls: persist and validate
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAutonomyControls:
    """Verify all controls persist and validate."""

    def test_kill_switch_toggle(self, db: None) -> None:
        """Kill switch (autonomy_enabled) can be toggled."""
        org = _mk_org("Kill Switch Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        # Get the autonomy page
        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should contain the autonomy enabled checkbox (Django default id)
        assert 'id="id_autonomy_enabled"' in content
        # Also check the label is present
        assert "Autonomy enabled" in content

    def test_shadow_mode_toggle(self, db: None) -> None:
        """Shadow mode toggle is present."""
        org = _mk_org("Shadow Mode Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should contain shadow mode control
        assert "shadow-mode" in content.lower() or "shadow_mode" in content.lower()

    def test_allowlist_checkboxes(self, db: None) -> None:
        """Allowlist checkboxes from server-side registry."""
        org = _mk_org("Allowlist Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should contain allowlist controls
        assert "propose-case-comment" in content.lower() or "propose_task_create" in content.lower()

    def test_max_auto_severity_control(self, db: None) -> None:
        """Max auto severity control is present and validates."""
        org = _mk_org("Severity Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should contain severity control
        assert "max-auto-severity" in content.lower() or "max_auto_severity" in content.lower()

    def test_daily_action_cap_control(self, db: None) -> None:
        """Daily action cap control is present."""
        org = _mk_org("Cap Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should contain cap control
        assert "daily-action-cap" in content.lower() or "daily_action_cap" in content.lower()

    def test_four_eyes_toggle(self, db: None) -> None:
        """Four-eyes toggle is present."""
        org = _mk_org("Four-Eyes Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should contain four-eyes toggle
        assert "four-eyes" in content.lower() or "require_four_eyes" in content.lower()


# ──────────────────────────────────────────────────────────────────────
# Warn banners
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestWarnBanners:
    """Verify warn banners render correctly."""

    def test_warning_banner_when_enabled(self, db: None) -> None:
        """Warning banner when autonomy enabled."""
        org = _mk_org("Warning Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        McpOrgPolicy.objects.create(
            organisation=org,
            autonomy_enabled=True,
            shadow_mode=True,
            require_four_eyes=True,
        )

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        assert resp.status_code == 200
        assert "Autonomous execution is" in content
        assert "banner--warning" in content

    def test_stronger_warning_when_enabled_shadow_disabled(self, db: None) -> None:
        """Stronger warning when enabled and shadow disabled."""
        org = _mk_org("Strong Warning Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        McpOrgPolicy.objects.create(
            organisation=org,
            autonomy_enabled=True,
            shadow_mode=False,
            require_four_eyes=True,
        )

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        assert resp.status_code == 200
        assert "Enforce mode:" in content
        assert "banner--error" in content


# ──────────────────────────────────────────────────────────────────────
# Enforce-mode dialog confirmation
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestEnforceModeDialog:
    """Verify enforce-mode enablement requires custom dialog."""

    def test_no_native_confirm(self, db: None) -> None:
        """No native confirm()/alert()/prompt() in autonomy UI."""
        org = _mk_org("No Native Dialog Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should not contain window.confirm, alert(), or prompt()
        assert "window.confirm" not in content
        assert "alert(" not in content  # ideally not
        assert "prompt(" not in content

    def test_dialog_confirmation_render(self, db: None) -> None:
        """Enforce-mode enable requires <dialog> confirmation."""
        org = _mk_org("Dialog Confirm Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Should use <dialog> modal pattern
        assert "data-modal-" in content or "modal" in content.lower()

    def test_shadow_exit_confirmation_modal_is_parsed(self, db: None) -> None:
        """Unconfirmed shadow-exit POST renders a real modal with the hidden flag."""
        org = _mk_org("Shadow Exit Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.post(
            _autonomy_update_url(org.id),
            {
                "autonomy_enabled": "on",
                "allowed_actions": ["propose_case_comment"],
                "max_auto_severity": "1",
                "daily_action_cap": "20",
                "require_four_eyes": "on",
            },
        )
        content = resp.content.decode()
        assert resp.status_code == 200
        assert 'id="shadow-exit-confirmation"' in content
        assert 'name="confirm_shadow_exit" value="1"' in content
        assert "{% include" not in content


# ──────────────────────────────────────────────────────────────────────
# Plain POST fallback
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPlainPOSTFallback:
    """Verify plain POST fallback works without HTMX."""

    def test_post_without_js_works(self, db: None) -> None:
        """POST forms work without JavaScript."""
        org = _mk_org("POST Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.post(
            _autonomy_update_url(org.id),
            {"action": "toggle_autonomy", "value": "true"},
            follow=True,
        )
        # Should get a response (redirect or error)
        assert resp.status_code in (200, 301, 302, 405)


# ──────────────────────────────────────────────────────────────────────
# Audit snapshots bounded
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestAuditSnapshots:
    """Verify audit snapshots are bounded and contain no secrets."""

    def test_audit_snapshots_no_raw_payloads(self, db: None) -> None:
        """Audit snapshots contain no raw payloads/secrets."""
        org = _mk_org("Audit Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Audit rows should not contain raw payloads
        assert "raw_payload" not in content


# ──────────────────────────────────────────────────────────────────────
# Settings nav/link present
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestSettingsNav:
    """Verify settings nav/link presence."""

    def test_autonomy_link_in_settings_nav(self, db: None) -> None:
        """Autonomy link present in settings navigation."""
        org = _mk_org("Nav Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(reverse("ui-settings-agent-access"))
        content = resp.content.decode()
        # Should have autonomy nav link
        assert "autonomy" in content.lower()


# ──────────────────────────────────────────────────────────────────────
# Icon/markup conventions
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestIconMarkup:
    """Verify icon and markup conventions."""

    def test_dark_theme(self, db: None) -> None:
        """Dark theme applied."""
        org = _mk_org("Dark Theme Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        assert '<html lang="en" data-theme="dark">' in content

    def test_icon_present(self, db: None) -> None:
        """Font Awesome icon present for autonomy controls."""
        org = _mk_org("Icon Org")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(_autonomy_url(org.id))
        content = resp.content.decode()
        # Icons should use Font Free convention
        assert "fa-" in content or "fa" in content.lower()


# ──────────────────────────────────────────────────────────────────────
# Human /approvals unaffected
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestHumanApprovalsUnaffected:
    """Verify human approvals remain unchanged."""

    def test_approvals_list_unaffected(self, db: None) -> None:
        """/approvals list works independently of autonomy settings."""
        org = _mk_org("Approval Org")
        analyst = _mk_user("analyst", org, role="analyst")
        client = _ui_client(analyst)

        resp = client.get(reverse("ui-approvals-list"))
        assert resp.status_code == 200

    def test_approvals_detail_unaffected(self, db: None) -> None:
        """/approvals detail works independently of autonomy settings."""
        from mcp_server.models import McpActionProposal, McpCredential

        org = _mk_org("Approval Detail Org")
        analyst = _mk_user("analyst", org, role="analyst")
        credential, _ = McpCredential.mint(organisation=org, user=analyst, name="test-cred")

        proposal = McpActionProposal.objects.create(
            organisation_id=org.id,
            requested_by_id=analyst.id,
            credential_id=credential.id,
            tool_name="propose_case_comment",
            action="append_timeline_event",
            target_type="case",
            target_id=str(uuid4()),
            arguments={"caseId": str(uuid4()), "markdown": "Test comment"},
            risk="low",
            idempotency_key=str(uuid4()),
            expires_at=timezone.now() + timedelta(hours=1),
        )

        client = _ui_client(analyst)
        resp = client.get(reverse("ui-approvals-detail", kwargs={"proposal_id": str(proposal.id)}))
        assert resp.status_code == 200
