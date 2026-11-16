"""MCP credential lifecycle tests: mint, verify, revoke, expiry and scope limits."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from identity.models import Organisation, User
from mcp_server.credentials import mint, revoke, verify
from mcp_server.models import McpCredential


@pytest.fixture
def org(db) -> Organisation:
    return Organisation.objects.create(name="Credential Org")


@pytest.fixture
def user(db, org) -> User:
    return User.objects.create(
        login="credential-user",
        username="credential-user",
        email="credential@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )


@pytest.mark.django_db
class TestMint:
    def test_plaintext_is_returned_once_and_only_hash_is_stored(self, org, user):
        credential, plaintext = mint(organisation=org, user=user, name="agent", created_by=user)

        assert credential.prefix == plaintext[:12]
        assert len(credential.prefix) == 12
        assert credential.secret_hash.startswith("$argon2")
        assert plaintext not in credential.secret_hash
        assert credential.scopes == ["mcp:read"]

        stored = McpCredential.objects.get(pk=credential.pk)
        assert stored.secret_hash == credential.secret_hash
        assert plaintext not in stored.secret_hash

    def test_prefixes_are_unique(self, org, user):
        first, _ = mint(organisation=org, user=user, name="one", created_by=user)
        second, _ = mint(organisation=org, user=user, name="two", created_by=user)
        assert first.prefix != second.prefix

    def test_rejects_cross_org_service_user(self, org, user):
        other = Organisation.objects.create(name="Other Org")
        with pytest.raises(ValidationError):
            mint(organisation=other, user=user, name="bad", created_by=user)

    def test_rejects_write_scope(self, org, user):
        credential = McpCredential(
            organisation=org,
            user=user,
            name="bad",
            prefix="abcdefghijkl",
            secret_hash="x",
            scopes=["mcp:write"],
        )
        with pytest.raises(ValidationError):
            credential.full_clean()


@pytest.mark.django_db
class TestVerify:
    def test_valid_token_verifies_and_touches_last_used(self, org, user):
        credential, plaintext = mint(organisation=org, user=user, name="agent", created_by=user)
        assert credential.last_used_at is None

        found = verify(plaintext)
        assert found is not None
        assert found.pk == credential.pk
        assert found.last_used_at is not None

    def test_wrong_and_short_tokens_fail(self, org, user):
        _credential, plaintext = mint(organisation=org, user=user, name="agent", created_by=user)
        assert verify(plaintext[:-1] + ("A" if plaintext[-1] != "A" else "B")) is None
        assert verify("short") is None
        assert verify("") is None

    def test_revoked_token_fails(self, org, user):
        credential, plaintext = mint(organisation=org, user=user, name="agent", created_by=user)
        assert revoke(credential.pk) is True
        assert verify(plaintext) is None
        assert revoke(credential.pk) is False

    def test_expired_token_fails(self, org, user):
        credential, plaintext = mint(
            organisation=org,
            user=user,
            name="agent",
            created_by=user,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        assert credential.is_expired is True
        assert verify(plaintext) is None
