"""MCP OAuth bindings UI conformance tests."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from identity.models import AuditLog, Organisation, User
from mcp_server.models import McpOAuthBinding

ISSUER = "https://idp.example.com"


def _org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _user(login: str, org: Organisation, role: str = "analyst") -> User:
    return User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _binding(org: Organisation, user: User, client_id: str) -> McpOAuthBinding:
    binding = McpOAuthBinding(
        name=f"binding-{client_id}",
        issuer=ISSUER,
        client_id=client_id,
        subject=f"subject-{client_id}",
        organisation=org,
        service_user=user,
        allowed_scopes=["mcp:read"],
        is_active=True,
    )
    binding.full_clean()
    binding.save()
    return binding


@pytest.fixture(autouse=True)
def oauth_ui_environment(settings) -> None:
    settings.MCP_AUTH_MODE = "oauth"
    settings.MCP_OAUTH_ISSUER = ISSUER
    settings.MCP_BASE_URL = "https://mcp.example.com"
    settings.MCP_RESOURCE_URL = "https://mcp.example.com/mcp"


@pytest.mark.django_db
class TestOAuthBindingUI:
    def test_admin_lists_only_own_org_bindings(self) -> None:
        org = _org("Org A")
        other = _org("Org B")
        admin = _user("admin", org, role="admin")
        service = _user("service", org)
        other_service = _user("other-service", other)
        _binding(org, service, "own")
        _binding(other, other_service, "foreign")

        body = _client(admin).get(reverse("ui-settings-oauth-binding-list")).content.decode()
        assert "binding-own" in body
        assert "binding-foreign" not in body

    def test_superadmin_lists_cross_org_bindings(self) -> None:
        org = _org("Org A")
        other = _org("Org B")
        superadmin = _user("super", org, role="superAdmin")
        service = _user("service", other)
        _binding(other, service, "cross")

        body = _client(superadmin).get(reverse("ui-settings-oauth-binding-list")).content.decode()
        assert "binding-cross" in body
        assert "Org B" in body

    def test_admin_creates_binding_and_audits_it(self) -> None:
        org = _org("Org")
        admin = _user("admin", org, role="admin")
        service = _user("service", org)
        response = _client(admin).post(
            reverse("ui-settings-oauth-binding-create"),
            {
                "name": "new-binding",
                "issuer": ISSUER,
                "client_id": "client-1",
                "subject": "subject-1",
                "service_user_login": service.login,
                "scopes": ["mcp:read"],
            },
        )
        assert response.status_code == 200
        assert "new-binding" in response.content.decode()
        binding = McpOAuthBinding.objects.get(client_id="client-1")
        assert binding.organisation_id == org.id
        assert binding.service_user_id == service.id
        assert binding.allowed_scopes == ["mcp:read"]
        assert AuditLog.objects.filter(
            action="mcp_oauth_binding.created", target_id=str(binding.id)
        ).exists()

    def test_reserved_scope_is_rejected(self) -> None:
        org = _org("Org")
        admin = _user("admin", org, role="admin")
        service = _user("service", org)
        response = _client(admin).post(
            reverse("ui-settings-oauth-binding-create"),
            {
                "name": "bad-binding",
                "issuer": ISSUER,
                "client_id": "client-2",
                "subject": "subject-2",
                "service_user_login": service.login,
                "scopes": ["mcp:write"],
            },
        )
        assert response.status_code == 302
        assert not McpOAuthBinding.objects.filter(client_id="client-2").exists()

    def test_issuer_mismatch_is_rejected(self) -> None:
        org = _org("Org")
        admin = _user("admin", org, role="admin")
        service = _user("service", org)
        response = _client(admin).post(
            reverse("ui-settings-oauth-binding-create"),
            {
                "name": "bad-issuer",
                "issuer": "https://evil.example.com",
                "client_id": "client-3",
                "subject": "subject-3",
                "service_user_login": service.login,
                "scopes": ["mcp:read"],
            },
        )
        assert response.status_code == 302
        assert not McpOAuthBinding.objects.filter(client_id="client-3").exists()

    def test_disable_is_modal_and_audited(self) -> None:
        org = _org("Org")
        admin = _user("admin", org, role="admin")
        service = _user("service", org)
        binding = _binding(org, service, "disable")
        client = _client(admin)

        body = client.get(reverse("ui-settings-oauth-binding-list")).content.decode()
        assert "data-modal-open" in body
        assert "confirm(" not in body

        response = client.post(
            reverse("ui-settings-oauth-binding-disable", kwargs={"binding_id": binding.id})
        )
        assert response.status_code == 302
        binding.refresh_from_db()
        assert binding.is_active is False
        assert AuditLog.objects.filter(
            action="mcp_oauth_binding.revoked", target_id=str(binding.id)
        ).exists()

    def test_superadmin_must_select_organisation(self) -> None:
        org = _org("Org A")
        superadmin = _user("super", org, role="superAdmin")
        response = _client(superadmin).post(
            reverse("ui-settings-oauth-binding-create"),
            {
                "name": "no-org",
                "issuer": ISSUER,
                "client_id": "client-4",
                "subject": "subject-4",
                "scopes": ["mcp:read"],
            },
        )
        assert response.status_code == 302
        assert not McpOAuthBinding.objects.filter(client_id="client-4").exists()

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_denied(self, role: str) -> None:
        org = _org("Org")
        user = _user(role, org, role=role)
        client = _client(user)
        assert client.get(reverse("ui-settings-oauth-binding-list")).status_code == 404
        assert client.post(reverse("ui-settings-oauth-binding-create")).status_code == 404

    def test_create_form_has_no_client_material_fields(self) -> None:
        org = _org("Org")
        admin = _user("admin", org, role="admin")
        body = _client(admin).get(reverse("ui-settings-agent-access")).content.decode().lower()
        assert "client_secret" not in body
        assert "access_token" not in body
        assert "jwt" not in body
        assert 'name="client_secret"' not in body
