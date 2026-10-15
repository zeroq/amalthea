"""Conformance tests for the Settings → Sources ingestion-token surface.

Covers PLAN-2026-10-15-settings-sources-ingestion-tokens AC-5..AC-17:

* cross-org invisibility for admins (the owner's hard requirement) — AC-5/AC-16
* show-once plaintext, stored only as a hash, never re-rendered — AC-8/AC-10
* the `secret_hash` never reaches the list page — AC-6
* analyst/viewer are locked out of all four routes — AC-7
* the audit trail records create/revoke without leaking secrets — AC-13/AC-17
* an admin can only ever mint for their own org; a superAdmin must name an
  org (and can target any) — AC-11/AC-12
* the end-to-end loop: mint in the UI → webhook accepted (201, stamped with the
  org) → revoke in the UI → the same secret is now rejected (401) — AC-9/AC-15

The end-to-end test is the one that proves the feature is real: it is the first
test to exercise the show-once render and the webhook together, and it was the
missing coverage that let a context-name mismatch (an empty plaintext modal)
reach a green suite.
"""

from __future__ import annotations

import json
import re

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from alerts.models import Alert
from identity.models import AuditLog, IngestionToken, Organisation, User
from ingest.models import IngestionSource

# The show-once modal renders `<code id="token-plaintext">{{ plaintext }}</code>`.
_PLAINTEXT_RE = re.compile(r'id="token-plaintext">([^<]+)<')


@pytest.fixture(autouse=True)
def _clear_webhook_throttle():
    """The webhook throttle keys partly off REMOTE_ADDR, which every test client shares.

    Clearing the cache keeps these tests independent of the order and count of
    webhook posts made elsewhere in the suite (WEBHOOK_RATE_IP_PER_MIN).
    """
    cache.clear()
    yield
    cache.clear()


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


def _mk_source(slug: str = "src", name: str = "Source") -> IngestionSource:
    return IngestionSource.objects.create(slug=slug, name=name)


def _create_token_via_ui(
    client: Client,
    *,
    source: IngestionSource,
    name: str,
    organisation: Organisation | None = None,
):
    data: dict[str, str] = {"source": source.slug, "name": name}
    if organisation is not None:
        data["organisation"] = str(organisation.id)
    return client.post(reverse("ui-settings-token-create"), data)


def _localhost_webhook(client: Client, slug: str, token: str, *, source_ref: str):
    return client.post(
        f"/api/v1/alerts/webhook/{slug}/",
        data=json.dumps({"title": "Test alert", "sourceRef": source_ref}),
        content_type="application/json",
        HTTP_X_WEBHOOK_SECRET=token,
    )


def _plaintext_from(response) -> str:
    match = _PLAINTEXT_RE.search(response.content.decode())
    assert match, "the show-once response did not render a plaintext token"
    return match.group(1).strip()


class TestShowOnce:
    """AC-8/AC-10 — the secret is shown exactly once, then only ever a hash."""

    def test_show_once_displays_the_plaintext_then_persists_only_a_hash(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        plaintext = _plaintext_from(resp)

        token = IngestionToken.objects.get(name="prod")
        assert token.secret_hash != plaintext
        assert plaintext not in token.secret_hash
        assert token.verify(plaintext) is True
        assert plaintext[:12] == token.prefix

        # The list page (the only other page that renders the token) never shows it again.
        list_body = client.get(reverse("ui-settings-sources")).content.decode()
        assert plaintext not in list_body

    def test_show_once_modal_names_the_concrete_webhook_endpoint(self, db: None) -> None:
        """The modal must show the real endpoint, not an empty string (regression guard)."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source(slug="crowdstrike")

        body = _create_token_via_ui(client, source=source, name="prod").content.decode()
        assert "/api/v1/alerts/webhook/crowdstrike/" in body


class TestHashSecrecy:
    """AC-6 — the stored hash never appears in rendered HTML."""

    def test_secret_hash_is_absent_from_the_list_page(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()
        token, _ = IngestionToken.create_token(source=source, organisation=org, name="prod")

        body = client.get(reverse("ui-settings-sources")).content.decode()
        assert token.secret_hash not in body
        # Non-vacuous: the token row really is on the page.
        assert token.prefix in body


class TestCrossOrgIsolation:
    """AC-5/AC-16 — an admin of one org can neither see nor revoke another's tokens."""

    def test_admin_list_excludes_other_orgs_tokens(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        source = _mk_source()
        other, _ = IngestionToken.create_token(
            source=source, organisation=org_b, name="org-b-only-token"
        )

        body = _ui_client(admin_a).get(reverse("ui-settings-sources")).content.decode()
        assert "org-b-only-token" not in body
        assert other.prefix not in body
        assert "Org B" not in body

    def test_admin_cannot_revoke_another_orgs_token(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        source = _mk_source()
        other, _ = IngestionToken.create_token(source=source, organisation=org_b, name="theirs")

        resp = _ui_client(admin_a).post(
            reverse("ui-settings-token-revoke", kwargs={"token_id": other.id})
        )
        assert resp.status_code == 404
        other.refresh_from_db()
        assert other.revoked_at is None, "a cross-org revoke mutated another tenant's token"


class TestRoleGates:
    """AC-7 — analyst and viewer get a uniform 404 on every route in the surface."""

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_roles_are_locked_out(self, db: None, role: str) -> None:
        org = _mk_org("Org1")
        user = _mk_user(f"user-{role}", org, role=role)
        client = _ui_client(user)
        source = _mk_source()
        token, _ = IngestionToken.create_token(source=source, organisation=org, name="existing")

        assert client.get(reverse("ui-settings")).status_code == 404
        assert client.get(reverse("ui-settings-sources")).status_code == 404
        assert (
            client.post(
                reverse("ui-settings-token-create"), {"source": source.slug, "name": "sneaky"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                reverse("ui-settings-token-revoke", kwargs={"token_id": token.id})
            ).status_code
            == 404
        )
        # And the blocked POST had no side effect.
        assert not IngestionToken.objects.filter(name="sneaky").exists()
        token.refresh_from_db()
        assert token.revoked_at is None


class TestAuditTrail:
    """AC-13/AC-17 — create and revoke are audited, with no secret material."""

    def test_create_and_revoke_are_audited_without_secrets(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        resp = _create_token_via_ui(client, source=source, name="prod")
        plaintext = _plaintext_from(resp)
        token = IngestionToken.objects.get(name="prod")

        created = AuditLog.objects.get(action="ingestion_token.created")
        assert created.actor_id == admin.id
        assert created.target_type == "IngestionToken"
        assert created.target_id == str(token.id)
        assert created.target_org_id == org.id
        assert plaintext not in str(created.__dict__)

        client.post(reverse("ui-settings-token-revoke", kwargs={"token_id": token.id}))

        revoked = AuditLog.objects.get(action="ingestion_token.revoked")
        assert revoked.actor_id == admin.id
        assert revoked.target_type == "IngestionToken"
        assert revoked.target_id == str(token.id)
        assert revoked.target_org_id == org.id
        assert plaintext not in str(revoked.__dict__)


class TestSuperadminOrgHandling:
    """AC-12 — a superAdmin must name an org, and can target any org."""

    def test_superadmin_without_organisation_is_rejected(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_user("super", org, role="superAdmin")
        source = _mk_source()

        resp = _create_token_via_ui(_ui_client(superadmin), source=source, name="prod")
        assert resp.status_code == 302
        assert not IngestionToken.objects.filter(name="prod").exists()

    def test_superadmin_can_mint_for_a_chosen_organisation(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        source = _mk_source()

        resp = _create_token_via_ui(
            _ui_client(superadmin), source=source, name="prod", organisation=org_b
        )
        assert resp.status_code == 200
        assert IngestionToken.objects.get(name="prod").organisation_id == org_b.id

    def test_superadmin_list_shows_tokens_from_every_org(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        source = _mk_source()
        IngestionToken.create_token(source=source, organisation=org_a, name="token-a")
        IngestionToken.create_token(source=source, organisation=org_b, name="token-b")

        body = _ui_client(superadmin).get(reverse("ui-settings-sources")).content.decode()
        assert "token-a" in body
        assert "token-b" in body
        assert "Org A" in body
        assert "Org B" in body


class TestAdminOrgBinding:
    """AC-11 — an admin's org is taken from the session, never from the POST body."""

    def test_admin_cannot_mint_for_another_org(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        source = _mk_source()

        resp = _create_token_via_ui(
            _ui_client(admin_a), source=source, name="prod", organisation=org_b
        )
        assert resp.status_code == 200
        token = IngestionToken.objects.get(name="prod")
        assert token.organisation_id == org_a.id, "an admin minted a token for another tenant"


class TestEndToEnd:
    """AC-9/AC-15 — mint in the UI, use it on the webhook, revoke it, and watch it fail."""

    def test_mint_then_webhook_then_revoke_then_rejected(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source(slug="e2e-src")

        # 1. Mint through the UI and read the plaintext off the show-once modal.
        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        plaintext = _plaintext_from(resp)
        token = IngestionToken.objects.get(name="prod")

        # 2. The freshly-minted secret authenticates a webhook, stamped with the admin's org.
        accepted = _localhost_webhook(client, source.slug, plaintext, source_ref="e2e-1")
        assert accepted.status_code == 201, accepted.content
        alert = Alert.objects.get(pk=accepted.json()["_id"])
        assert alert.owner_org_id == org.id

        # 3. Revoke the token through the UI.
        revoked = client.post(reverse("ui-settings-token-revoke", kwargs={"token_id": token.id}))
        assert revoked.status_code == 302
        token.refresh_from_db()
        assert token.revoked_at is not None

        # 4. The same secret is now refused.
        rejected = _localhost_webhook(client, source.slug, plaintext, source_ref="e2e-2")
        assert rejected.status_code == 401
        assert rejected.json()["type"] == "unauthorized"
