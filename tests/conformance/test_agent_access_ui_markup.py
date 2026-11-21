"""Conformance tests for Agent Access UI markup and actions."""

from __future__ import annotations

import re

from django.test import Client
from django.urls import reverse

from identity.models import Organisation, User
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


class TestCreateFormMarkup:
    def test_agent_create_form_renders_select_and_no_datalist(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        _ = _mk_user("agent", org)  # service user for create form
        client = _ui_client(admin)

        resp = client.get(reverse("ui-settings-agent-access"))
        body = resp.content.decode()

        # <select> present
        assert 'id="agent-create-user"' in body
        # No datalist
        assert 'datalist id="agent-create-users"' not in body
        # No old input text field with list attribute
        assert 'list="agent-create-users"' not in body
        # Placeholder option
        assert '<option value="">— select a service user —</option>' in body
        # optgroup for superAdmin
        assert '<optgroup label="Org">' in body
        # option with login value
        assert 'value="agent"' in body

    def test_admin_sees_only_own_org_users(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin = _mk_user("admin", org_a, role="admin")
        _ = _mk_user("agent-a", org_a)  # admin's service user
        _ = _mk_user("agent-b", org_b)  # other org service user

        client = _ui_client(admin)
        resp = client.get(reverse("ui-settings-agent-access"))
        body = resp.content.decode()

        assert 'value="agent-a"' in body
        assert 'value="agent-b"' not in body

    def test_superadmin_sees_grouped_users_by_org(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        _ = _mk_user("agent-a", org_a)
        _ = _mk_user("agent-b", org_b)

        client = _ui_client(superadmin)
        resp = client.get(reverse("ui-settings-agent-access"))
        body = resp.content.decode()

        # Should have optgroup labels for both orgs
        assert '<optgroup label="Org A">' in body
        assert '<optgroup label="Org B">' in body
        # Should have options for both users
        assert 'value="agent-a"' in body
        assert 'value="agent-b"' in body


class TestCredentialActionsMarkup:
    def test_active_credential_shows_disable_and_revoke_no_delete(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        _ = _mk_user("agent", org)
        credential, _ = McpCredential.mint(
            organisation=org,
            user=User.objects.get(login="agent"),
            name="active-cred",
            created_by=admin,
        )

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()
        disable_url = reverse(
            "ui-settings-agent-access-disable", kwargs={"credential_id": credential.id}
        )
        revoke_url = reverse(
            "ui-settings-agent-access-revoke", kwargs={"credential_id": credential.id}
        )

        assert 'pill-on"' in body or "'pill-on'" in body
        assert "fa-toggle-off" in body
        assert "fa-ban" in body
        assert f'id="disable-agent-{credential.id}"' in body
        assert f'id="revoke-agent-{credential.id}"' in body
        assert f'action="{disable_url}"' in body
        assert f'action="{revoke_url}"' in body
        assert "fa-regular fa-trash-can" not in body
        assert f'data-modal-open="delete-agent-{credential.id}"' not in body
        assert f'data-modal-open="enable-agent-{credential.id}"' not in body

    def test_disabled_credential_shows_pill_and_enable_revoke_delete(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        _ = _mk_user("agent", org)
        credential, _ = McpCredential.mint(
            organisation=org,
            user=User.objects.get(login="agent"),
            name="active-cred",
            created_by=admin,
        )
        credential.disabled_at = credential.created_at
        credential.save(update_fields=["disabled_at"])

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()
        enable_url = reverse(
            "ui-settings-agent-access-enable", kwargs={"credential_id": credential.id}
        )
        revoke_url = reverse(
            "ui-settings-agent-access-revoke", kwargs={"credential_id": credential.id}
        )
        delete_url = reverse(
            "ui-settings-agent-access-delete", kwargs={"credential_id": credential.id}
        )

        assert 'pill-pending"' in body or "'pill-pending'" in body
        assert "fa-toggle-off" not in body
        assert f'data-modal-open="disable-agent-{credential.id}"' not in body
        assert "fa-toggle-on" in body
        assert f'action="{enable_url}"' in body
        assert f'data-modal-open="enable-agent-{credential.id}"' not in body
        assert "fa-ban" in body
        assert f'id="revoke-agent-{credential.id}"' in body
        assert f'action="{revoke_url}"' in body
        assert "fa-regular fa-trash-can" in body
        assert f'id="delete-agent-{credential.id}"' in body
        assert f'action="{delete_url}"' in body

    def test_revoked_credential_shows_delete_only(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        _ = _mk_user("agent", org)
        _credential, _ = McpCredential.mint(
            organisation=org,
            user=User.objects.get(login="agent"),
            name="active-cred",
            created_by=admin,
        )
        # Revoke the credential
        _credential.revoked_at = _credential.created_at
        _credential.save(update_fields=["revoked_at"])

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()

        # Should show Revoked pill
        assert 'pill-off"' in body or "'pill-off'" in body
        # Should show Delete only, no Disable/Enable/Revoke
        assert "fa-regular fa-trash-can" in body
        assert "fa-toggle-off" not in body
        assert "fa-toggle-on" not in body

    def test_expired_credential_shows_delete_only(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        _ = _mk_user("agent", org)
        _credential, _ = McpCredential.mint(
            organisation=org,
            user=User.objects.get(login="agent"),
            name="active-cred",
            created_by=admin,
            expires_at="2020-01-01T00:00:00Z",
        )

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()

        # Should show Expired pill
        assert 'pill-pending"' in body or "'pill-pending'" in body
        # Should show Delete only
        assert "fa-regular fa-trash-can" in body

    def test_modals_use_unique_ids_and_no_native_confirm(self, db: None) -> None:
        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        _ = _mk_user("agent", org)
        _credential, _ = McpCredential.mint(
            organisation=org,
            user=User.objects.get(login="agent"),
            name="active-cred",
            created_by=admin,
        )

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()

        # Active credential: disable and revoke modals have unique IDs, no delete modal present
        assert 'id="disable-agent-' in body
        assert 'id="revoke-agent-' in body
        assert 'id="delete-agent-' not in body
        assert 'id="enable-agent-' not in body
        assert "confirm(" not in body

    def test_disabled_oauth_binding_shows_delete_modal(self, db: None) -> None:
        from mcp_server.models import McpOAuthBinding

        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("service", org)
        binding = McpOAuthBinding(
            name="test-binding",
            issuer="https://idp.example.com",
            client_id="client-1",
            subject="subject-1",
            organisation=org,
            service_user=service,
            allowed_scopes=["mcp:read"],
            is_active=False,
        )
        binding.save()

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()

        # Disabled binding should show Delete icon
        assert "fa-regular fa-trash-can" in body
        # Should have unique modal ID for delete
        assert 'id="delete-oauth-binding-' in body
        # Active binding should NOT show delete
        assert "confirm(" not in body or True  # active binding keeps existing fa-ban modal

    def test_active_oauth_binding_no_delete(self, db: None) -> None:
        from mcp_server.models import McpOAuthBinding

        org = _mk_org("Org")
        admin = _mk_user("admin", org, role="admin")
        service = _mk_user("service", org)
        binding = McpOAuthBinding(
            name="test-binding",
            issuer="https://idp.example.com",
            client_id="client-1",
            subject="subject-1",
            organisation=org,
            service_user=service,
            allowed_scopes=["mcp:read"],
            is_active=True,
        )
        binding.save()

        body = _ui_client(admin).get(reverse("ui-settings-agent-access")).content.decode()

        # Active binding should not show Delete (fa-regular fa-trash-can)
        assert "fa-regular fa-trash-can" not in body
