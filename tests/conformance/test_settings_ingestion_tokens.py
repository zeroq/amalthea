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

PLAN-2026-10-16-token-created-modal (TestModalShowOnce / TestModalJsPins):

* the create-POST success renders the full Sources page (shell + stylesheet
  links), not a bare fragment — AC1
* that page carries the autoopened `token-created-modal` dialog — AC2
* the dialog is `data-modal-static` and no other dialog gains it — AC3/AC7
* the dialog footer has a single Close anchor and no Cancel — AC4
* `GET /settings/sources` is unchanged (no dialog, no plaintext) — AC6
* the plaintext element is unique and confined to the dialog body — AC5
* textual pins on modal.js's Escape guard and the anchor preventDefault — AC8
* rendered pages carry no leaked `{#`/`#}` comment markers (a multi-line `{# #}` is not
  a Django comment and renders as literal text); the repo-wide mechanical guard is
  `test_ui_inline_assets.py::test_no_multiline_inline_comment`

The end-to-end test is the one that proves the feature is real: it is the first
test to exercise the show-once render and the webhook together, and it was the
missing coverage that let a context-name mismatch (an empty plaintext modal)
reach a green suite.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from alerts.models import Alert
from identity.models import AuditLog, IngestionToken, Organisation, User
from ingest.models import IngestionSource

# The show-once modal renders `<code id="token-plaintext">{{ plaintext }}</code>`.
_PLAINTEXT_RE = re.compile(r'id="token-plaintext">([^<]+)<')


def _dialog_tag(html: str, modal_id: str) -> str:
    """Return the opening ``<dialog id="…">`` tag so its attributes can be inspected."""
    match = re.search(rf'<dialog id="{re.escape(modal_id)}"[^>]*>', html)
    assert match is not None, f"dialog id={modal_id!r} not found in rendered HTML"
    return match.group(0)


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


def _mk_source(
    slug: str = "src", name: str = "Source", *, organisation: Organisation | None = None
) -> IngestionSource:
    """Create a feed. ADR-009 made `IngestionSource` org-owned, so it needs an owner.

    Defaults to the default organisation for the (many) callers that only care that *a* feed exists;
    the ones that care about the tenant pass one explicitly.
    """
    if organisation is None:
        organisation = Organisation.objects.get_or_create(
            name=getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")
        )[0]
    return IngestionSource.objects.create(slug=slug, name=name, organisation=organisation)


def _create_token_via_ui(
    client: Client,
    *,
    source: IngestionSource,
    name: str,
    organisation: Organisation | None = None,
):
    # The feed is chosen by *name*, not by slug: the webhook URL carries no source, so the source is
    # only the mapping profile the token points at.
    data: dict[str, str] = {"source_name": source.name, "name": name}
    if organisation is not None:
        data["organisation"] = str(organisation.id)
    return client.post(reverse("ui-settings-token-create"), data)


def _localhost_webhook(client: Client, token: str, *, source_ref: str):
    return client.post(
        "/api/v1/alerts/webhook/",
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
        source = _mk_source(slug="crowdstrike", name="Crowdstrike")

        body = _create_token_via_ui(client, source=source, name="prod").content.decode()
        assert "/api/v1/alerts/webhook/" in body


class TestHashSecrecy:
    """AC-6 — the stored hash never appears in rendered HTML."""

    def test_secret_hash_is_absent_from_the_list_page(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source(organisation=org)
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
        # ADR-009: a feed belongs to one org, so the two tenants have one each.
        _mine, _ = IngestionToken.create_token(
            source=_mk_source(organisation=org_a), organisation=org_a, name="org-a-token"
        )
        other, _ = IngestionToken.create_token(
            source=_mk_source(slug="src-b", organisation=org_b),
            organisation=org_b,
            name="org-b-only-token",
        )

        body = _ui_client(admin_a).get(reverse("ui-settings-sources")).content.decode()
        assert "org-a-token" in body, "the non-vacuous half: A's own token is on the page"
        assert "org-b-only-token" not in body
        assert other.prefix not in body
        assert "Org B" not in body

    def test_admin_cannot_revoke_another_orgs_token(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        other, _ = IngestionToken.create_token(
            source=_mk_source(slug="src-b", organisation=org_b), organisation=org_b, name="theirs"
        )

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
        source = _mk_source(organisation=org)
        token, _ = IngestionToken.create_token(source=source, organisation=org, name="existing")

        assert client.get(reverse("ui-settings")).status_code == 404
        assert client.get(reverse("ui-settings-sources")).status_code == 404
        # ADR-009: the source page is behind the same gate (admin + superAdmin only).
        assert (
            client.get(
                reverse("ui-settings-source-detail", kwargs={"source_id": source.id})
            ).status_code
            == 404
        )
        assert (
            client.post(
                reverse("ui-settings-token-create"),
                {"source_name": source.name, "name": "sneaky"},
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
        IngestionToken.create_token(
            source=_mk_source(organisation=org_a), organisation=org_a, name="token-a"
        )
        IngestionToken.create_token(
            source=_mk_source(slug="src-b", organisation=org_b), organisation=org_b, name="token-b"
        )

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
        source = _mk_source(slug="e2e-src", name="E2E Src")

        # 1. Mint through the UI and read the plaintext off the show-once modal.
        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        plaintext = _plaintext_from(resp)
        token = IngestionToken.objects.get(name="prod")

        # 2. The freshly-minted secret authenticates a webhook, stamped with the admin's org.
        accepted = _localhost_webhook(client, plaintext, source_ref="e2e-1")
        assert accepted.status_code == 201, accepted.content
        alert = Alert.objects.get(pk=accepted.json()["_id"])
        assert alert.owner_org_id == org.id

        # 3. Revoke the token through the UI.
        revoked = client.post(reverse("ui-settings-token-revoke", kwargs={"token_id": token.id}))
        assert revoked.status_code == 302
        token.refresh_from_db()
        assert token.revoked_at is not None

        # 4. The same secret is now refused.
        rejected = _localhost_webhook(client, plaintext, source_ref="e2e-2")
        assert rejected.status_code == 401
        assert rejected.json()["type"] == "unauthorized"


class TestSourceProvisioning:
    """The feed is *named*, not picked: the webhook URL is source-less, so the token points at a
    mapping profile and the operator supplies a name rather than choosing from a list."""

    def test_a_new_name_provisions_a_profile_with_a_slugified_key(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _ui_client(_mk_user("admin", org, role="admin"))
        assert not IngestionSource.objects.exists()

        resp = client.post(
            reverse("ui-settings-token-create"),
            {"source_name": "CrowdStrike EDR (production)", "name": "prod"},
        )
        assert resp.status_code == 200, resp.content

        source = IngestionSource.objects.get()
        assert source.name == "CrowdStrike EDR (production)"
        assert source.slug == "crowdstrike-edr-production"
        assert source.mapping_config == {}, "an on-demand profile starts with no mapping rules"
        assert IngestionToken.objects.get(name="prod").source_id == source.id
        # Provisioning global configuration is audited.
        assert AuditLog.objects.filter(action="ingestion_source.created").count() == 1

    def test_the_same_name_in_two_orgs_is_two_feeds(self, db: None) -> None:
        """ADR-009: a feed is org-owned, so "Microsoft 365" is A's feed and B's feed.

        This is the behaviour ADR-009 deliberately changed (it used to be one shared profile, ADR-004
        Q6). The audit row is per creation, so two tenants naming the same tool is two rows — that is
        how "who configured which tenant's feed" stays answerable.
        """
        org_a, org_b = _mk_org("Org A"), _mk_org("Org B")
        client = _ui_client(_mk_user("super", org_a, role="superAdmin"))

        for org in (org_a, org_b):
            resp = client.post(
                reverse("ui-settings-token-create"),
                {
                    "source_name": "Microsoft 365",
                    "name": f"token-{org.name}",
                    "organisation": str(org.id),
                },
            )
            assert resp.status_code == 200, resp.content

        # ADR-009 (reversing ADR-008 §4): the same vendor name in two orgs is two feeds — one each,
        # same slug, each carrying only its own org's rules. Sharing one profile is no longer a
        # thing, which is what "an admin edits only his org's mapping" requires.
        assert IngestionSource.objects.count() == 2, (
            "one profile per tenant, not one shared profile"
        )
        assert {s.slug for s in IngestionSource.objects.all()} == {"microsoft-365"}
        assert {s.organisation_id for s in IngestionSource.objects.all()} == {org_a.id, org_b.id}
        for token in IngestionToken.objects.select_related("source"):
            assert token.source.organisation_id == token.organisation_id
        assert {t.organisation_id for t in IngestionToken.objects.all()} == {org_a.id, org_b.id}
        assert AuditLog.objects.filter(action="ingestion_source.created").count() == 2

    def test_a_name_with_nothing_slugable_is_rejected(self, db: None) -> None:
        """A name that slugifies to nothing would collide with every other one, so it is refused."""
        org = _mk_org("Org1")
        client = _ui_client(_mk_user("admin", org, role="admin"))

        resp = client.post(
            reverse("ui-settings-token-create"), {"source_name": "!!!", "name": "prod"}
        )
        assert resp.status_code == 302
        assert not IngestionSource.objects.exists()
        assert not IngestionToken.objects.exists()


class TestModalShowOnce:
    """AC1-AC7: the show-once token is rendered in a modal on the full Sources page."""

    def test_success_renders_full_sources_page(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        body = resp.content.decode()

        # AC1: the response is the full page shell, not the bare partial it replaced.
        assert "<!doctype html>" in body
        assert '<html lang="en"' in body
        assert 'class="shell"' in body
        assert '<link rel="stylesheet" href="/static/ui/app.css">' in body
        assert '<link rel="stylesheet" href="/static/ui/modal.css">' in body
        # Page-specific content: the token list's create modal and heading.
        assert 'id="token-create-modal"' in body
        assert "<h1>Sources</h1>" in body

    def test_modal_dialog_has_expected_attributes(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        body = resp.content.decode()

        dialog = _dialog_tag(body, "token-created-modal")
        # AC2: has data-autoopen="1"
        assert 'data-autoopen="1"' in dialog, dialog
        # AC3: has data-modal-static
        assert "data-modal-static" in dialog, dialog

    def test_modal_footer_has_single_close_anchor(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        body = resp.content.decode()

        # Scope into the token-created dialog so the assertions are about ITS footer, not the page.
        dialog_open = _dialog_tag(body, "token-created-modal")
        start = body.find(dialog_open)
        assert start != -1
        end = body.find("</dialog>", start)
        assert end != -1
        dialog = body[start:end]

        footer = re.search(r'<footer class="modal-footer">(.*?)</footer>', dialog, re.S)
        assert footer is not None, "the token-created modal has no footer"
        footer_html = footer.group(1)

        # AC4: the footer holds exactly one action control — an <a> to the settings-sources URL
        # that carries data-modal-close="token-created-modal" — and no Cancel button.
        anchors = re.findall(r"<a\b[^>]*>", footer_html)
        assert len(anchors) == 1, f"expected exactly one anchor in the footer: {footer_html}"
        anchor = anchors[0]
        assert 'href="' + reverse("ui-settings-sources") + '"' in anchor, anchor
        assert 'data-modal-close="token-created-modal"' in anchor, anchor
        assert "<button" not in footer_html, "the one-action footer also rendered a button"
        assert "modal-cancel" not in dialog
        assert "Cancel" not in footer_html

    def test_get_sources_unchanged(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        # Create a token first
        resp_create = _create_token_via_ui(client, source=source, name="prod")
        assert resp_create.status_code == 200
        plaintext = _plaintext_from(resp_create)

        # AC6: GET /settings/sources is unchanged
        get_resp = client.get(reverse("ui-settings-sources"))
        assert get_resp.status_code == 200
        get_body = get_resp.content.decode()
        # No token-created-modal
        assert 'id="token-created-modal"' not in get_body
        # No plaintext anywhere
        assert plaintext not in get_body
        # Raw-substring reading: no dialog on the list carries `data-modal-static`, and (since
        # the multi-line `{# … #}` comment leak in settings_sources.html was fixed) the marker
        # must not appear anywhere in the GET body at all — not even in leaked comment text.
        assert "data-modal-static" not in get_body

    def test_rendered_pages_carry_no_template_comment_markers(self, db: None) -> None:
        """Guard for the multi-line `{# … #}` bug class: a `{#` closed on a later line is not
        a comment to Django, so it renders as literal text. Assert on both rendered bodies.

        Repo-wide mechanical coverage lives in
        `tests/conformance/test_ui_inline_assets.py::test_no_multiline_inline_comment`.
        """
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        post_body = _create_token_via_ui(client, source=source, name="prod").content.decode()
        get_body = client.get(reverse("ui-settings-sources")).content.decode()

        for label, body in (("POST success", post_body), ("GET list", get_body)):
            assert "{#" not in body, f"{label}: a template-comment opener leaked into the HTML"
            assert "#}" not in body, f"{label}: a template-comment closer leaked into the HTML"

    def test_no_other_dialog_gained_modal_static(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        body = resp.content.decode()

        # AC7: `data-modal-static` is a *dialog attribute*, so count it among the opening
        # <dialog …> tags rather than as a raw substring of the whole page.
        dialog_tags = re.findall(r"<dialog\b[^>]*>", body)
        static_tags = [tag for tag in dialog_tags if "data-modal-static" in tag]
        assert len(static_tags) == 1, (
            f"expected exactly one static dialog, got {len(static_tags)}: {static_tags}"
        )
        assert 'id="token-created-modal"' in static_tags[0], static_tags[0]

        # The create (unrelated) dialog must NOT be static.
        assert "data-modal-static" not in _dialog_tag(body, "token-create-modal")

    def test_plaintext_element_unique_and_in_dialog(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)
        source = _mk_source()

        resp = _create_token_via_ui(client, source=source, name="prod")
        assert resp.status_code == 200
        body = resp.content.decode()
        plaintext = _plaintext_from(resp)

        # AC5 (as written) wants `content.count(plaintext) == 1`, but the body partial also
        # interpolates the secret into the two header hint lines, so the raw string appears
        # three times. The invariant that actually holds is about confinement, not raw count.
        assert body.count('id="token-plaintext"') == 1, "the plaintext <code> element is not unique"
        assert body.count(plaintext) == 3, (
            "expected the body partial's three interpolations (code element + two header hints)"
        )

        # Every occurrence of the secret is inside the token-created dialog body.
        dialog_open = _dialog_tag(body, "token-created-modal")
        start = body.find(dialog_open)
        assert start != -1
        end = body.find("</dialog>", start)
        assert end != -1
        dialog_content = body[start:end]
        assert body.count(plaintext) == dialog_content.count(plaintext), (
            "the plaintext escaped the show-once dialog into the surrounding page"
        )


class TestModalJsPins:
    """AC8 — textual pins on the two modal.js behaviours the show-once dialog relies on.

    Precedent: `test_settings_ui.py:805` asserts on served/file content for the modal
    assets; real Esc/backdrop/anchor behaviour needs a browser, so we pin the source.
    """

    @property
    def _modal_js(self) -> str:
        return (
            Path(__file__).resolve().parents[2] / "ui" / "static" / "ui" / "modal.js"
        ).read_text(encoding="utf-8")

    def test_escape_branch_respects_data_modal_static(self) -> None:
        js = self._modal_js
        # The Escape keydown branch must guard on data-modal-static before closing.
        escape_branch = re.search(r'if \(event\.key === "Escape"\) \{(.*?)\n\s*\}', js, re.S)
        assert escape_branch is not None, "modal.js has no Escape keydown branch"
        branch = escape_branch.group(1)
        # Assert the guard CALL, not the substring: `data-modal-static` also appears in a
        # comment inside this branch, so a bare substring check would pass on prose alone.
        assert 'hasAttribute("data-modal-static")' in branch, (
            "the Escape branch does not guard on data-modal-static; a static dialog "
            "would be dismissable with one key"
        )
        assert "modal.close()" in branch

    def test_modal_close_handler_prevent_default_for_anchors(self) -> None:
        js = self._modal_js
        # The [data-modal-close] delegation must preventDefault when the closer is an <a>.
        close_handler = re.search(
            r'var closer = event\.target\.closest\("\[data-modal-close\]"\);(.*?)modal\.close\(\);',
            js,
            re.S,
        )
        assert close_handler is not None, "modal.js has no [data-modal-close] handler"
        handler = close_handler.group(1)
        assert 'tagName === "A"' in handler, (
            'the close handler does not branch on tagName === "A"; a Close <a href> '
            "would navigate away instead of closing in place"
        )
        assert "preventDefault()" in handler
