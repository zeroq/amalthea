"""MCP Phase 2 scope separation tests.

Read/propose/summary separation; mcp:write/admin rejected; viewer/read-only denied.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from identity.models import Organisation, User
from mcp_server.models import McpCredential


def _mk_org(name: str = "Test Org") -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(
    login: str,
    org: Organisation,
    role: str = "analyst",
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


@pytest.mark.django_db
class TestReadOnlyScope:
    """Verify that mcp:read-only credentials work as in Phase 1."""

    def test_readonly_credential_mints_successfully(self) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-agent-access-create"),
            {"name": "test-agent", "user_login": service.login, "expires_at": ""},
        )
        assert resp.status_code == 200
        from mcp_server.models import McpCredential

        credential = McpCredential.objects.get(name="test-agent")
        assert credential.scopes == ["mcp:read"]

    def test_readonly_credential_cannot_propose(self) -> None:
        """mcp:read credential cannot create proposals (P2.1 AC)."""
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)
        client = _ui_client(admin)

        client.post(
            reverse("ui-settings-agent-access-create"),
            {"name": "propose-agent", "user_login": service.login, "expires_at": ""},
        )
        credential = McpCredential.objects.get(name="propose-agent")
        # Phase 1 only: should NOT have mcp:propose
        assert "mcp:propose" not in credential.scopes


@pytest.mark.django_db
class TestProposeScope:
    """Verify that mcp:propose credential has correct capabilities."""

    def test_propose_credential_cannot_summary(self) -> None:
        """mcp:propose credential cannot call post_case_summary (P2.4 AC)."""
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-agent-access-create"),
            {
                "name": "propose-agent",
                "user_login": service.login,
                "expires_at": "",
                "scopes": ["mcp:propose"],
            },
        )
        assert resp.status_code == 200
        credential = McpCredential.objects.get(name="propose-agent")
        assert "mcp:propose" in credential.scopes
        assert "mcp:summary" not in credential.scopes


@pytest.mark.django_db
class TestSummaryScope:
    """Verify that mcp:summary credential has correct capabilities."""

    def test_summary_credential_cannot_propose(self) -> None:
        """mcp:summary credential cannot create proposals (P2.1 AC)."""
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-agent-access-create"),
            {
                "name": "summary-agent",
                "user_login": service.login,
                "expires_at": "",
                "scopes": ["mcp:summary"],
            },
        )
        assert resp.status_code == 200
        credential = McpCredential.objects.get(name="summary-agent")
        assert "mcp:summary" in credential.scopes
        assert "mcp:propose" not in credential.scopes

    def test_summary_credential_cannot_execute_proposals(self) -> None:
        """mcp:summary credential cannot execute/decline proposals (P2.1 AC)."""
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)
        client = _ui_client(admin)

        client.post(
            reverse("ui-settings-agent-access-create"),
            {"name": "propose-agent", "user_login": service.login, "expires_at": ""},
        )
        credential = McpCredential.objects.get(name="propose-agent")
        # Should not have mcp:propose
        assert "mcp:propose" not in credential.scopes


@pytest.mark.django_db
class TestReservedScopesRejected:
    """Verify that mcp:write and mcp:admin are always rejected."""

    def test_mcp_write_rejected(self) -> None:
        """mcp:write scope is rejected (P2.1 AC)."""
        from mcp_server.models import McpCredential

        org = _mk_org("Org")
        service = _mk_user("agent", org)
        credential = McpCredential(
            organisation=org,
            user=service,
            name="bad-write",
            prefix="badwrite000000",
            secret_hash="x",
            scopes=["mcp:write"],
        )
        with pytest.raises(ValidationError):
            credential.full_clean()

    def test_mcp_admin_rejected(self) -> None:
        """mcp:admin scope is rejected (P2.1 AC)."""
        from mcp_server.models import McpCredential

        org = _mk_org("Org")
        service = _mk_user("agent", org)
        credential = McpCredential(
            organisation=org,
            user=service,
            name="bad-admin",
            prefix="badmin000000",
            secret_hash="x",
            scopes=["mcp:admin"],
        )
        with pytest.raises(ValidationError):
            credential.full_clean()

    def test_mcp_write_and_admin_combined_rejected(self) -> None:
        """Combined mcp:write + mcp:admin are rejected (P2.1 AC)."""
        from mcp_server.models import McpCredential

        org = _mk_org("Org")
        service = _mk_user("agent", org)
        credential = McpCredential(
            organisation=org,
            user=service,
            name="bad-combo",
            prefix="badcombo000000",
            secret_hash="x",
            scopes=["mcp:write", "mcp:admin"],
        )
        with pytest.raises(ValidationError):
            credential.full_clean()

    def test_unknown_scope_rejected(self) -> None:
        """Unknown scope is rejected (P2.1 AC)."""
        from mcp_server.models import McpCredential

        org = _mk_org("Org")
        service = _mk_user("agent", org)
        credential = McpCredential(
            organisation=org,
            user=service,
            name="bad-scope",
            prefix="badscope000000",
            secret_hash="x",
            scopes=["mcp:unknown"],
        )
        with pytest.raises(ValidationError):
            credential.full_clean()


@pytest.mark.django_db
class TestViewerDenied:
    """Verify that viewer/read-only users cannot access proposal/summary features."""

    def test_viewer_cannot_access_approvals(self) -> None:
        """Viewer gets 404 on /approvals (P2.6 AC)."""
        org = _mk_org("TestOrg")
        viewer = _mk_user("viewer", org, role="viewer")
        client = _ui_client(viewer)
        resp = client.get(reverse("ui-approvals-list"))
        assert resp.status_code == 404

    def test_viewer_cannot_access_proposal_detail(self) -> None:
        """Viewer gets 404 on proposal detail (P2.6 AC)."""
        from mcp_server.models import McpActionProposal, McpCredential

        org = _mk_org("TestOrg")
        analyst = _mk_user("analyst", org, role="analyst")
        viewer = _mk_user("viewer", org, role="viewer")
        credential, _ = McpCredential.mint(
            organisation=org,
            user=analyst,
            name="test-cred",
        )
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
            expires_at="2026-11-19T12:00:00Z",
            status="pending",
        )
        client = _ui_client(viewer)
        resp = client.get(reverse("ui-approvals-detail", kwargs={"proposal_id": str(proposal.id)}))
        assert resp.status_code == 404

    def test_viewer_cannot_create_proposal_via_ui(self) -> None:
        """Viewer cannot create proposals via UI (P2.1 AC)."""
        org = _mk_org("Org")
        viewer = _mk_user("viewer", org, role="viewer")
        service = _mk_user("agent", org)
        client = _ui_client(viewer)
        resp = client.post(
            reverse("ui-settings-agent-access-create"),
            {"name": "test-agent", "user_login": service.login, "expires_at": ""},
        )
        # Viewer should not be able to create credentials
        assert resp.status_code == 404
