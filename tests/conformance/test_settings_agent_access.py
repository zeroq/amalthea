"""Conformance tests for Settings → Agent access (MCP credentials)."""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from identity.models import AuditLog, Organisation, User
from mcp_server.models import McpCredential

_PLAINTEXT_RE = re.compile(r'id="agent-credential-plaintext">([^<]+)<')


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
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


def _create_via_ui(
    client: Client,
    *,
    name: str = "crewai",
    user_login: str = "agent",
    organisation: Organisation | None = None,
    expires_at: str = "",
):
    data = {"name": name, "user_login": user_login, "expires_at": expires_at}
    if organisation is not None:
        data["organisation"] = str(organisation.id)
    return client.post(reverse("ui-settings-agent-access-create"), data)


def _plaintext(response) -> str:
    match = _PLAINTEXT_RE.search(response.content.decode())
    assert match, "the show-once response did not render a plaintext credential"
    return match.group(1).strip()


def _mk_credential(org: Organisation, user: User, name: str) -> tuple[McpCredential, str]:
    return McpCredential.mint(
        organisation=org,
        user=user,
        name=name,
        created_by=user,
    )


class TestCreate:
    def test_admin_mints_a_read_only_org_bound_credential_shown_once(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)
        client = _ui_client(admin)

        resp = _create_via_ui(client, user_login=service.login)
        assert resp.status_code == 200
        assert resp["Cache-Control"] == "no-store"
        plaintext = _plaintext(resp)

        credential = McpCredential.objects.get(name="crewai")
        assert credential.organisation_id == org.id
        assert credential.user_id == service.id
        assert credential.created_by_id == admin.id
        assert credential.scopes == ["mcp:read"]
        assert credential.secret_hash != plaintext
        assert credential.verify(plaintext) is True
        assert plaintext[:12] == credential.prefix

        page = client.get(reverse("ui-settings-agent-access")).content.decode()
        assert plaintext not in page
        assert "crewai" in page

    def test_admin_cannot_target_another_orgs_service_user(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin = _mk_user("admin", org_a, role="admin")
        outsider = _mk_user("outsider", org_b)
        client = _ui_client(admin)

        resp = _create_via_ui(client, user_login=outsider.login)
        assert resp.status_code == 302
        assert not McpCredential.objects.filter(name="crewai").exists()

    def test_inactive_service_user_is_rejected(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        inactive = _mk_user("inactive", org)
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

        resp = _create_via_ui(_ui_client(admin), user_login=inactive.login)
        assert resp.status_code == 302
        assert not McpCredential.objects.exists()

    @pytest.mark.parametrize("expires_at", ["not-a-date", "2000-01-01T00:00"])
    def test_invalid_or_past_expiry_is_rejected(self, db: None, expires_at: str) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)

        resp = _create_via_ui(_ui_client(admin), user_login=service.login, expires_at=expires_at)
        assert resp.status_code == 302
        assert not McpCredential.objects.exists()

    def test_superadmin_must_choose_an_org_and_can_mint_for_another_org(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        service_b = _mk_user("agent-b", org_b)
        client = _ui_client(superadmin)

        missing = _create_via_ui(client, user_login=service_b.login)
        assert missing.status_code == 302
        assert not McpCredential.objects.exists()

        created = _create_via_ui(client, user_login=service_b.login, organisation=org_b)
        assert created.status_code == 200
        assert McpCredential.objects.get(name="crewai").organisation_id == org_b.id


class TestListingAndIsolation:
    def test_admin_list_excludes_other_orgs_credentials(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin = _mk_user("admin", org_a, role="admin")
        service_a = _mk_user("agent-a", org_a)
        service_b = _mk_user("agent-b", org_b)
        _mk_credential(org_a, service_a, "visible")
        hidden, _ = _mk_credential(org_b, service_b, "hidden")
        credential_b_id = hidden.id

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()
        assert "visible" in body
        assert f'data-agent-credential-id="{credential_b_id}"' not in body

    def test_superadmin_list_shows_every_org(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        _mk_credential(org_a, _mk_user("agent-a", org_a), "cred-a")
        _mk_credential(org_b, _mk_user("agent-b", org_b), "cred-b")

        body = _ui_client(superadmin).get(reverse("ui-settings-agent-access")).content.decode()
        assert "cred-a" in body
        assert "cred-b" in body

    def test_admin_cannot_revoke_another_orgs_credential(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin = _mk_user("admin", org_a, role="admin")
        other, _ = _mk_credential(org_b, _mk_user("agent-b", org_b), "other")

        resp = _ui_client(admin).post(
            reverse("ui-settings-agent-access-revoke", kwargs={"credential_id": other.id})
        )
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.revoked_at is None

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_roles_are_locked_out(self, db: None, role: str) -> None:
        org = _mk_org("Org")
        user = _mk_user(f"user-{role}", org, role=role)
        service = _mk_user("agent", org)
        credential, _ = _mk_credential(org, service, "existing")
        client = _ui_client(user)

        assert client.get(reverse("ui-settings-agent-access")).status_code == 404
        assert _create_via_ui(client, user_login=service.login).status_code == 404
        assert (
            client.post(
                reverse("ui-settings-agent-access-revoke", kwargs={"credential_id": credential.id})
            ).status_code
            == 404
        )
        assert not McpCredential.objects.filter(name="crewai").exists()
        credential.refresh_from_db()
        assert credential.revoked_at is None


class TestRevokeAndAudit:
    def test_revoke_is_terminal_and_audited_without_the_secret(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)
        client = _ui_client(admin)

        created = _create_via_ui(client, user_login=service.login)
        plaintext = _plaintext(created)
        credential = McpCredential.objects.get(name="crewai")

        created_audit = AuditLog.objects.get(action="mcp_credential.created")
        assert created_audit.actor_id == admin.id
        assert created_audit.target_org_id == org.id
        assert created_audit.target_type == "McpCredential"
        assert created_audit.target_id == str(credential.id)
        assert plaintext not in str(created_audit.__dict__)

        resp = client.post(
            reverse("ui-settings-agent-access-revoke", kwargs={"credential_id": credential.id})
        )
        assert resp.status_code == 302
        credential.refresh_from_db()
        assert credential.revoked_at is not None
        assert credential.verify(plaintext) is False

        revoked = AuditLog.objects.get(action="mcp_credential.revoked")
        assert revoked.actor_id == admin.id
        assert revoked.target_org_id == org.id
        assert revoked.target_id == str(credential.id)

        # A second revoke is a no-op and does not create another audit row.
        client.post(
            reverse("ui-settings-agent-access-revoke", kwargs={"credential_id": credential.id})
        )
        assert AuditLog.objects.filter(action="mcp_credential.revoked").count() == 1

    def test_revoke_form_is_not_rendered_for_a_revoked_credential(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        credential, _ = _mk_credential(org, _mk_user("agent", org), "old")
        credential.revoked_at = credential.created_at
        credential.save(update_fields=["revoked_at"])

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()
        assert "Revoked" in body
        assert f'data-modal-open="revoke-agent-{credential.id}"' not in body


class TestShowOnceTemplate:
    def test_created_modal_is_autoopened_static_and_unique(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("agent", org)

        body = _create_via_ui(_ui_client(admin), user_login=service.login).content.decode()
        assert body.count('id="agent-credential-plaintext"') == 1
        assert 'id="credential-created-modal"' in body
        assert 'data-autoopen="1"' in body
        assert "data-modal-static" in body
        assert "You will not see this credential again." in body
        assert reverse("ui-settings-agent-access") in body

    def test_get_page_never_contains_a_plaintext_or_show_once_modal(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()
        assert "agent-credential-plaintext" not in body
        assert "credential-created-modal" not in body
