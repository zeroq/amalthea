"""ADR-009 — a feed is tenant data: org ownership, the token invariant, the source page.

PLAN-2026-10-16-source-field-mapping §8 P2, AC 7-10:

* AC7 — `ingest/0003` assigns each existing source the organisation of its tokens and *refuses to
  guess* when they disagree (two token orgs ⇒ RuntimeError, not a coin flip); the remedy named in the
  error message then lands. Driven through `MigrationExecutor`: pytest-django has already applied the
  migration by the time a test runs, so calling the body directly would assert nothing about the
  pre-state, and the pre-state is the only interesting input.
* AC8 — the source page is behind the ADR-006 role gate *and* the ADR-009 org gate: an `admin` of org
  A gets 404 for org B's feed (uniformly, so the response is not an existence oracle), a `superAdmin`
  gets both, and the cross-org read is audited (ADR-004 Q5).
* AC9 — a token whose organisation is not its source's is refused on all three paths: the model
  (`save()`), the service (`create_token`), and the Settings form.
* AC10 — `slug` is unique *per organisation*: the same slug in two orgs is two feeds, twice in one org
  is refused.

Plus the consequence the FK policy was chosen for (ADR-009): an organisation that owns a feed cannot
be deleted, and `organisation_blockers` names the reason instead of leaving a bare `ProtectedError`.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.models.deletion import ProtectedError
from django.test import Client
from django.urls import reverse

from alerts.models import Alert
from identity.models import AuditLog, IngestionToken, Organisation, User
from identity.services import organisation_blockers
from ingest.models import IngestionSource

pytestmark = pytest.mark.django_db

MIGRATION = ("ingest", "0003_ingestion_source_organisation")
BEFORE = ("ingest", "0002_ingestion_source_hardening")

#: A v2 config with two rules — the shape `read_rules` reads and the source page renders.
MAPPING = {
    "version": 2,
    "fields": {
        "title": {"paths": ["$.name"], "transform": "str"},
        "severity": {
            "paths": ["$.severity"],
            "transform": "severity_score",
            "bands": [[80, 4], [0, 2]],
        },
    },
}


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
    organisation: Organisation,
    *,
    slug: str = "src",
    name: str = "Source",
    mapping_config: dict | None = None,
) -> IngestionSource:
    """A feed needs an owner (ADR-009) — and there is deliberately no default here: the reviewer has
    to name one, because `create()` without an organisation cannot even be written."""
    return IngestionSource.objects.create(
        organisation=organisation, slug=slug, name=name, mapping_config=mapping_config or {}
    )


def _leaves() -> list[tuple[str, str]]:
    return MigrationLoader(None, ignore_no_migrations=True).graph.leaf_nodes()


def _migrate(targets: list[tuple[str, str]]) -> None:
    """A fresh executor per call — a reused one replans from stale applied-state."""
    MigrationExecutor(connection).migrate(targets)


def _before_state() -> list[tuple[str, str]]:
    """`ingest.0002` with identity left at head, so only the ingest migration moves.

    Naming identity's leaf rather than the `0006` this migration declares as its dependency keeps the
    test from rolling unrelated identity migrations (0007, 0008) back and up again.
    """
    return [BEFORE, next(node for node in _leaves() if node[0] == "identity")]


def _column_names(table: str) -> set[str]:
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table)
    return {column.name for column in description}


class TestMigrationAssignsTheOwner:
    """AC7 — the backfill derives the org from the tokens, and refuses when it cannot."""

    @pytest.mark.django_db(transaction=True, serialized_rollback=True)
    def test_a_source_takes_the_organisation_of_its_tokens(self) -> None:
        before = _before_state()
        _migrate(before)
        try:
            apps = MigrationExecutor(connection).loader.project_state(before).apps
            historical_source = apps.get_model("ingest", "IngestionSource")
            historical_token = apps.get_model("identity", "IngestionToken")

            assert "organisation_id" not in _column_names("ingestion_source"), (
                "precondition: without this the backfill below is never exercised"
            )
            org = _mk_org("Backfill Org")
            # The historical source has no `organisation` field at all: that is why this goes through
            # the executor instead of the live model, whose column is NOT NULL.
            source = historical_source.objects.create(slug="legacy-feed", name="Legacy feed")
            historical_token.objects.create(
                source_id=source.pk,
                organisation_id=org.pk,
                name="legacy token",
                prefix="abcdefghijkl",
                secret_hash="hash",
            )

            _migrate([MIGRATION])

            assert IngestionSource.objects.get(pk=source.pk).organisation_id == org.pk, (
                "the backfill did not take the organisation of the source's own tokens"
            )
            assert "organisation_id" in _column_names("ingestion_source"), (
                "the column must exist at the end state — the backfill is not the only operation"
            )
        finally:
            _migrate(_leaves())

    @pytest.mark.django_db(transaction=True, serialized_rollback=True)
    def test_two_token_organisations_raise_and_the_documented_remedy_lands(self) -> None:
        """Guessing here is a cross-tenant ingest, so the migration must stop and let a human decide."""
        before = _before_state()
        _migrate(before)
        try:
            apps = MigrationExecutor(connection).loader.project_state(before).apps
            historical_source = apps.get_model("ingest", "IngestionSource")
            historical_token = apps.get_model("identity", "IngestionToken")

            org_a, org_b = _mk_org("Split Org A"), _mk_org("Split Org B")
            source = historical_source.objects.create(slug="split-feed", name="Split feed")
            for org_id, prefix in ((org_a.pk, "aaaaaaaaaaaa"), (org_b.pk, "bbbbbbbbbbbb")):
                historical_token.objects.create(
                    source_id=source.pk,
                    organisation_id=org_id,
                    name=f"token-{prefix[:1]}",
                    prefix=prefix,
                    secret_hash="hash",
                )

            # Historical models carry no custom `save()`, so the ADR-009 invariant does not get in the
            # way of building the pre-state this test is about.
            with pytest.raises(RuntimeError, match="cannot decide which one owns it"):
                _migrate([MIGRATION])

            # The rejected migration rolled back whole: the column is still absent, which is what
            # makes the operator's remedy possible instead of leaving a half-migrated table.
            assert "organisation_id" not in _column_names("ingestion_source"), (
                "the failed migration left the column behind, so the remedy below proves nothing"
            )

            # Do what the error message says: keep one org's token, move the other out.
            moved = historical_token.objects.get(source_id=source.pk, organisation_id=org_b.pk)
            historical_token.objects.filter(pk=moved.pk).update(organisation_id=org_a.pk)

            _migrate([MIGRATION])

            assert IngestionSource.objects.get(pk=source.pk).organisation_id == org_a.pk, (
                "after the split the feed must land in the org that still has its tokens"
            )
        finally:
            _migrate(_leaves())


class TestSlugIsUniquePerOrganisation:
    """AC10 — the composite constraint, behaviourally (the DB columns are pinned in `test_integrity`)."""

    def test_the_same_slug_in_two_organisations_is_two_feeds(self) -> None:
        org_a, org_b = _mk_org("Slug Org A"), _mk_org("Slug Org B")

        a = _mk_source(org_a, slug="crowdstrike-edr", name="CrowdStrike (A)")
        b = _mk_source(org_b, slug="crowdstrike-edr", name="CrowdStrike (B)")

        assert IngestionSource.objects.filter(slug="crowdstrike-edr").count() == 2
        assert (a.organisation_id, b.organisation_id) == (org_a.pk, org_b.pk)

    def test_the_same_slug_twice_in_one_org_is_refused(self) -> None:
        org = _mk_org("One Org")
        _mk_source(org, slug="dup", name="First")

        with transaction.atomic(), pytest.raises(IntegrityError):
            IngestionSource.objects.create(organisation=org, slug="dup", name="Second")


class TestTokenMustMatchItsSourcesOwner:
    """AC9 — one test per path, because a cross-table CHECK cannot express this pair."""

    @pytest.fixture
    def two_orgs(self) -> tuple[Organisation, Organisation, User]:
        org_a, org_b = _mk_org("Token Org A"), _mk_org("Token Org B")
        return org_a, org_b, _mk_user("admin-a", org_a, role="admin")

    def test_the_model_refuses_the_pair(self, two_orgs) -> None:
        org_a, org_b, _ = two_orgs
        foreign = _mk_source(org_b, slug="theirs", name="Their feed")

        token = IngestionToken(
            source=foreign,
            organisation=org_a,
            name="wrong-side",
            prefix="mmmmmmmmmmmm",
            secret_hash="hash",
        )
        with pytest.raises(ValidationError, match="ADR-009"):
            token.save()

        assert not IngestionToken.objects.filter(name="wrong").exists()
        assert not IngestionToken.objects.filter(source=foreign).exists()

    def test_the_service_refuses_the_pair_before_hashing(self, two_orgs) -> None:
        org_a, org_b, _ = two_orgs
        source_b = _mk_source(org_b, slug="service-source", name="Theirs")

        with pytest.raises(ValidationError, match="ADR-009"):
            IngestionToken.create_token(source=source_b, organisation=org_a, name="sneaky")

        assert not IngestionToken.objects.filter(name="sneaky").exists()

    def test_the_settings_form_binds_the_token_to_the_actors_own_feed(self, two_orgs) -> None:
        """The form names a *feed name*, resolved inside the actor's org — so naming another tenant's
        feed provisions this tenant's own, and can never mint a token for the other one (AC9 + AC10).
        """
        org_a, org_b, admin_a = two_orgs
        theirs = _mk_source(org_b, slug="shared-name", name="Shared Name")

        response = _ui_client(admin_a).post(
            reverse("ui-settings-token-create"),
            {"source_name": theirs.name, "name": "minted-by-a"},
        )

        assert response.status_code == 200, "the create-POST success re-renders the Sources page"
        token = IngestionToken.objects.get(name="minted-by-a")
        assert token.organisation_id == org_a.pk
        assert token.source.organisation_id == org_a.pk, "A's token bound to A's own feed"
        assert token.source_id != theirs.pk
        assert not IngestionToken.objects.filter(source=theirs).exists(), (
            "an admin minted a token for another organisation's feed"
        )

    def test_the_matching_pair_is_accepted(self, two_orgs) -> None:
        """The non-vacuous half: the refusals above are not a blanket ban on creation."""
        org_a, _, _ = two_orgs
        source = _mk_source(org_a, slug="mine", name="My feed")

        token, plaintext = IngestionToken.create_token(
            source=source, organisation=org_a, name="allowed"
        )

        assert token.source_id == source.pk and token.organisation_id == org_a.pk
        assert token.verify(plaintext)


class TestSourcePageGates:
    """AC8 — role gate *and* org gate, both answering with a uniform 404."""

    @pytest.fixture
    def two_orgs_with_feeds(
        self,
    ) -> tuple[Organisation, Organisation, IngestionSource, IngestionSource]:
        org_a, org_b = _mk_org("Page Org A"), _mk_org("Page Org B")
        mine = _mk_source(org_a, slug="mine-feed", name="Mine", mapping_config=MAPPING)
        theirs = _mk_source(org_b, slug="theirs-feed", name="Theirs")
        return org_a, org_b, mine, theirs

    def _url(self, source: IngestionSource) -> str:
        return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})

    def test_an_admin_sees_their_owner_org_feed_with_its_rules(self, two_orgs_with_feeds) -> None:
        org_a, _, mine, _ = two_orgs_with_feeds
        admin = _mk_user("page-admin-a", org_a, role="admin")
        IngestionToken.create_token(source=mine, organisation=org_a, name="live token")

        response = _ui_client(admin).get(self._url(mine))
        body = response.content.decode()

        assert response.status_code == 200
        assert "Mine" in body
        assert org_a.name in body
        # The rules table, not just the shell: target, path and transform must all be on the page.
        assert "$.name" in body
        assert "severity_score" in body
        assert "2 rules" in body
        assert "live token" in body
        # An own-org read is routine, so it is *not* a break-glass read.
        assert not AuditLog.objects.filter(target_type="IngestionSource").exists()

    def test_another_orgs_feed_is_a_uniform_404(self, two_orgs_with_feeds) -> None:
        org_a, _, _, theirs = two_orgs_with_feeds
        admin = _mk_user("page-admin-a2", org_a, role="admin")

        response = _ui_client(admin).get(self._url(theirs))
        body = response.content.decode()

        assert response.status_code == 404, "an org admin reached another tenant's feed"
        assert theirs.slug not in body and theirs.name not in body, (
            "the 404 leaked the existence or the name of another tenant's feed"
        )

    def test_a_nonexistent_feed_is_the_same_404(self, two_orgs_with_feeds) -> None:
        org_a, _, _, _ = two_orgs_with_feeds
        admin = _mk_user("page-admin-a3", org_a, role="admin")

        # A well-formed UUID that exists nowhere must be indistinguishable from a foreign one.
        response = _ui_client(admin).get(
            reverse("ui-settings-source-detail", kwargs={"source_id": uuid.uuid4()})
        )
        assert response.status_code == 404

    def test_a_superadmin_reaches_both_and_the_foreign_read_is_audited(
        self, two_orgs_with_feeds
    ) -> None:
        org_a, org_b, mine, theirs = two_orgs_with_feeds
        superadmin = _mk_user("page-super", org_a, role="superAdmin")
        client = _ui_client(superadmin)

        assert client.get(self._url(mine)).status_code == 200

        foreign = client.get(self._url(theirs))
        assert foreign.status_code == 200
        assert "not your own organisation" in foreign.content.decode(), (
            "a break-glass read should say so on the page"
        )

        log = AuditLog.objects.get(action="cross_org_read", target_type="IngestionSource")
        assert log.target_id == str(theirs.id)
        assert log.target_org_id == org_b.pk

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_roles_never_reach_the_page(self, two_orgs_with_feeds, role: str) -> None:
        org_a, _, mine, _ = two_orgs_with_feeds
        user = _mk_user(f"page-{role}", org_a, role=role)

        response = _ui_client(user).get(self._url(mine))

        assert response.status_code == 404, "the ADR-006 role gate must answer before the org gate"
        assert mine.slug not in response.content.decode()


class TestTheOwnerIsProtected:
    """The FK policy ADR-009 chose: PROTECT, reported by name."""

    def test_an_org_that_owns_a_feed_cannot_be_deleted(self) -> None:
        org = _mk_org("Owner Org")
        _mk_source(org, slug="owned", name="Owned feed")

        assert organisation_blockers(org)["ingestionSources"] == 1

        with pytest.raises(ProtectedError):
            org.delete()

    def test_the_delete_warning_names_the_feed(self) -> None:
        from ui.views import _org_blockers_text

        # `_org_blockers_text` reads counts off an annotated organisation, so a stub is the honest
        # input here — the annotation itself is asserted by the Settings view tests.
        assert _org_blockers_text(SimpleNamespace(sources_count=1)) == "1 ingestion source"
        assert _org_blockers_text(SimpleNamespace(sources_count=3)) == "3 ingestion sources"


# --- AC11-AC14: the mapping editor ------------------------------------------


def _table_post(**rows: tuple[str, str, str]) -> dict[str, str]:
    """A table submission — `_table_post(title=("$.name", "str", ""))`, every other row empty."""
    from ingest.mapping import TARGETS

    data = {"form": "table"}
    for target in TARGETS:
        paths, transform, default = rows.get(target, ("", "", ""))
        data[f"paths__{target}"] = paths
        data[f"transform__{target}"] = transform
        data[f"default__{target}"] = default
    return data


def _post_from_page(config: dict) -> dict[str, str]:
    """Exactly what the browser would post if the operator pressed Save without touching anything.

    Built from `submitted_rows` — the editor's own view of the config — rather than hand-written, so
    this test cannot drift from what the page actually renders.
    """
    from ingest.editor import submitted_rows

    data = {"form": "table"}
    for target, row in submitted_rows(config).items():
        data[f"paths__{target}"] = row["paths"]
        data[f"transform__{target}"] = row["transform"]
        data[f"default__{target}"] = row["default"]
    return data


class _FeedFixture:
    """One org, one admin, one feed carrying `MAPPING` — the input every editor test needs."""

    @pytest.fixture
    def feed(self) -> tuple[Organisation, User, IngestionSource]:
        org = _mk_org("Edit Org")
        admin = _mk_user("edit-admin", org, role="admin")
        source = _mk_source(org, slug="edit-feed", name="Edit feed", mapping_config=MAPPING)
        return org, admin, source

    def _url(self, source: IngestionSource) -> str:
        return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})


class TestTheTableWritesTheMapping(_FeedFixture):
    """AC11 — the table is a writer: it validates, persists and records what it changed."""

    def test_a_valid_save_stores_v2_and_audits_the_changed_keys(self, feed) -> None:
        org, admin, source = feed
        updated_before = source.updated_at

        response = _ui_client(admin).post(
            self._url(source),
            _table_post(
                title=("$.name\n$.display_name", "str", ""),
                severity=("$.severity", "severity_score", "3"),
            ),
        )

        assert response.status_code == 302, "a successful save is PRG, so a reload cannot re-post"

        source.refresh_from_db()
        assert source.mapping_config == {
            "version": 2,
            "fields": {
                "title": {"paths": ["$.name", "$.display_name"]},
                "severity": {
                    "paths": ["$.severity"],
                    "transform": "severity_score",
                    "default": 3,
                    # Not rendered by the table, so it must not be lost: a hand-tuned score band.
                    "bands": [[80, 4], [0, 2]],
                },
            },
        }
        assert source.updated_at > updated_before

        log = AuditLog.objects.get(action="ingestion_source.updated")
        assert log.target_type == "IngestionSource"
        assert log.target_id == str(source.id)
        assert log.target_org_id == org.pk
        assert log.actor_id == admin.pk
        assert log.details["changed"] == [
            "fields.severity.default",
            "fields.title.paths",
            # The stored config spelled out `"transform": "str"` and the table omits the engine's own
            # default — a real key change, reported even though the rule behaves identically.
            "fields.title.transform",
        ]
        assert "$.name" not in str(log.details), "the audit records keys, never mapping values"

    def test_the_success_message_names_the_rule_count(self, feed) -> None:
        _, admin, source = feed

        response = _ui_client(admin).post(
            self._url(source),
            # A real edit: re-posting the rendered rows would be the AC12 no-op below.
            _table_post(
                title=("$.display_name\n$.name", "str", ""),
                severity=("$.severity", "severity_score", ""),
            ),
            follow=True,
        )

        assert "Mapping saved — 2 rules." in response.content.decode()

    def test_clearing_every_row_says_what_that_means(self, feed) -> None:
        _, admin, source = feed

        response = _ui_client(admin).post(self._url(source), _table_post(), follow=True)

        source.refresh_from_db()
        assert source.mapping_config == {}
        assert "payload literal keys are used" in response.content.decode()

    def test_an_invalid_path_is_a_200_that_echoes_the_input_and_writes_nothing(self, feed) -> None:
        """The reason the rejected save is a render and not a redirect: the operator's own text has to
        come back, or they have to retype a JSONPath to find out it was still wrong."""
        _, admin, source = feed
        updated_before = source.updated_at

        response = _ui_client(admin).post(self._url(source), _table_post(title=("name", "str", "")))
        body = response.content.decode()

        assert response.status_code == 200, "a rejected save must keep the input, not redirect"
        assert "name</textarea>" in body, "the submitted paths must be echoed back"
        assert "must start with $" in body

        source.refresh_from_db()
        assert source.mapping_config == MAPPING
        assert source.updated_at == updated_before
        assert not AuditLog.objects.exists(), (
            "a rejected save must not leave a write or an audit row"
        )

    def test_a_refused_severity_default_is_reported_on_its_row(self, feed) -> None:
        _, admin, source = feed

        response = _ui_client(admin).post(
            self._url(source), _table_post(severity=("$.severity", "severity_int", "High"))
        )

        assert response.status_code == 200
        assert "whole number 1-4" in response.content.decode()
        source.refresh_from_db()
        assert source.mapping_config == MAPPING

    def test_a_legacy_config_is_warned_about_before_it_is_converted(self, feed) -> None:
        """AC11's "announced, never silent" half: the page says what a save will do to a flat config."""
        org, admin, _ = feed
        legacy = _mk_source(
            org, slug="legacy-feed", name="Legacy", mapping_config={"title": "$.name"}
        )

        body = _ui_client(admin).get(self._url(legacy)).content.decode()

        assert "older flat form" in body
        assert "stop being tried" in body

    def test_a_legacy_config_saved_through_the_table_becomes_v2(self, feed) -> None:
        org, admin, _ = feed
        legacy = _mk_source(
            org, slug="legacy2", name="Legacy 2", mapping_config={"title": "$.name"}
        )

        response = _ui_client(admin).post(
            self._url(legacy), _table_post(title=("$.name", "str", ""))
        )

        assert response.status_code == 302
        legacy.refresh_from_db()
        assert legacy.mapping_config == {
            "version": 2,
            "fields": {"title": {"paths": ["$.name"]}},
        }
        log = AuditLog.objects.get(action="ingestion_source.updated")
        assert log.details["changed"] == ["fields", "title", "version"]


class TestTheNoOpSave(_FeedFixture):
    """AC12 — submitting what is already stored must not write, audit or bump `updated_at`."""

    def test_saving_the_rendered_page_unchanged_is_a_no_op(self, feed) -> None:
        _, admin, source = feed
        updated_before = source.updated_at

        response = _ui_client(admin).post(self._url(source), _post_from_page(MAPPING), follow=True)

        assert "No changes to save." in response.content.decode()

        source.refresh_from_db()
        assert source.mapping_config == MAPPING, "the column must not be rewritten"
        assert source.updated_at == updated_before
        assert not AuditLog.objects.exists()

    def test_an_equivalent_spelling_is_still_a_no_op(self, feed) -> None:
        """`{"paths": […], "transform": "str"}` and `{"paths": […"]}` are the same rule to the engine,
        so normalising one into the other is not an edit worth an audit row."""
        _, admin, source = feed
        stored = {"version": 2, "fields": {"title": {"paths": ["$.name"]}}}
        source.mapping_config = stored
        source.save(update_fields=["mapping_config", "updated_at"])
        updated_before = source.updated_at

        response = _ui_client(admin).post(
            self._url(source),
            _table_post(title=("$.name", "str", "")),
            follow=True,
        )

        assert "No changes to save." in response.content.decode()
        source.refresh_from_db()
        assert source.mapping_config == stored
        assert source.updated_at == updated_before
        assert not AuditLog.objects.exists()

    def test_a_real_edit_is_not_swallowed_as_a_no_op(self, feed) -> None:
        """The other half of AC12: the check must not be so eager that it drops a genuine change."""
        _, admin, source = feed

        response = _ui_client(admin).post(
            self._url(source),
            _table_post(
                title=("$.display_name", "str", ""),
                severity=("$.severity", "severity_score", ""),
            ),
        )

        assert response.status_code == 302
        source.refresh_from_db()
        assert source.mapping_config["fields"]["title"] == {"paths": ["$.display_name"]}


class TestTheAdvancedJsonBox(_FeedFixture):
    """AC13 — the escape hatch: the box accepts what the table cannot express, and refuses the rest."""

    def _post(self, source: IngestionSource, text: str):
        return reverse("ui-settings-source-detail", kwargs={"source_id": source.id}), {
            "form": "json",
            "mapping_json": text,
        }

    def test_a_valid_config_is_stored_verbatim(self, feed) -> None:
        _, admin, source = feed
        wanted = {
            "version": 2,
            "fields": {"title": {"paths": ["$.name"], "transform": "str"}},
            "value_maps": {"severity": {"Suspicious": 2}},
        }

        url, data = self._post(source, json.dumps(wanted))
        response = _ui_client(admin).post(url, data)

        assert response.status_code == 302
        source.refresh_from_db()
        assert source.mapping_config == wanted
        log = AuditLog.objects.get(action="ingestion_source.updated")
        assert "value_maps" in log.details["changed"]

    def test_an_empty_object_clears_the_mapping(self, feed) -> None:
        _, admin, source = feed

        url, data = self._post(source, "{}")

        assert _ui_client(admin).post(url, data).status_code == 302
        source.refresh_from_db()
        assert source.mapping_config == {}
        assert AuditLog.objects.get(action="ingestion_source.updated").details["changed"] == [
            "fields",
            "version",
        ]

    def test_an_empty_box_is_refused_rather_than_read_as_clear(self, feed) -> None:
        _, admin, source = feed
        updated_before = source.updated_at

        url, data = self._post(source, "")
        response = _ui_client(admin).post(url, data)

        assert response.status_code == 200
        assert "{} to clear the mapping" in response.content.decode()
        source.refresh_from_db()
        assert source.mapping_config == MAPPING
        assert source.updated_at == updated_before
        assert not AuditLog.objects.exists()

    def test_invalid_json_keeps_the_text_and_names_the_position(self, feed) -> None:
        _, admin, source = feed

        url, data = self._post(source, '{"version": 2,\n "fields": }')
        response = _ui_client(admin).post(url, data)
        body = response.content.decode()

        assert response.status_code == 200
        assert "Not valid JSON" in body and "line 2" in body
        assert "fields&quot;: }" in body, "the pasted text must survive the re-render"
        source.refresh_from_db()
        assert source.mapping_config == MAPPING

    def test_a_rule_level_failure_is_visible_on_the_json_card(self, feed) -> None:
        """A JSON config can fail on one rule; an error the operator cannot see is one they cannot fix,
        so the JSON card renders every message, not only the page-level ones."""
        _, admin, source = feed

        url, data = self._post(source, '{"version": 2, "fields": {"title": {"paths": ["name"]}}}')
        response = _ui_client(admin).post(url, data)

        assert response.status_code == 200
        assert "must start with $" in response.content.decode()
        source.refresh_from_db()
        assert source.mapping_config == MAPPING


class TestTheEditorGatesHoldOnPost(_FeedFixture):
    """AC14 — the write path is gated exactly like the read path (ADR-006 + ADR-009)."""

    def test_an_analyst_cannot_write_the_mapping(self, feed) -> None:
        org, _, source = feed
        analyst = _mk_user("edit-analyst", org, role="analyst")
        updated_before = source.updated_at

        response = _ui_client(analyst).post(
            self._url(source), _table_post(title=("$.name", "str", ""))
        )

        assert response.status_code == 404
        source.refresh_from_db()
        assert source.mapping_config == MAPPING
        assert source.updated_at == updated_before
        assert not AuditLog.objects.exists()

    def test_another_orgs_admin_cannot_write_the_mapping(self, feed) -> None:
        _, _, source = feed
        outsider = _mk_user("edit-outsider", _mk_org("Other Org"), role="admin")

        response = _ui_client(outsider).post(
            self._url(source), _table_post(title=("$.name", "str", ""))
        )

        assert response.status_code == 404
        source.refresh_from_db()
        assert source.mapping_config == MAPPING
        assert not AuditLog.objects.exists()

    def test_a_superadmin_can_write_across_orgs_and_the_row_says_so(self, feed) -> None:
        """ADR-004 Q5: the break-glass path is never silent — the audit carries both organisations."""
        _, _, source = feed
        home = _mk_org("Super Org")
        superadmin = _mk_user("edit-super", home, role="superAdmin")

        response = _ui_client(superadmin).post(
            self._url(source), _table_post(title=("$.name", "str", ""))
        )

        assert response.status_code == 302
        log = AuditLog.objects.get(action="ingestion_source.updated")
        assert log.actor_org_id == home.pk
        assert log.target_org_id == source.organisation_id


# --- P7: the "apply to unlinked open alerts" button ------------------------------------------

#: The mapping the apply tests save — two rules, so the config diff is visible on both halves.
APPLY_MAPPING = {
    "version": 2,
    "fields": {"title": {"paths": ["$.name"]}},
}


def _mk_open_alert(
    source: IngestionSource,
    *,
    source_ref: str,
    title: str = "Before",
    severity: int = 2,
    warnings: dict | None = None,
):
    """One *unlinked open* alert of a feed — the input a re-map is allowed to rewrite.

    Built through the ORM so these tests do not depend on the webhook, and so the candidate predicate
    is the only thing under test here (its exclusions are covered in `test_mapping_remap.py`).

    `warnings` is the stored `ingestion_warnings` column, which the P5 health card reads its `mapping`
    record out of — set to the shape ingest writes rather than produced by running a pass.
    """
    from alerts.models import AlertStatus

    status, _ = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New"})
    return Alert.objects.create(
        title=title,
        description="",
        summary="",
        severity=severity,
        type="phishing",
        source="sender-name",
        source_ref=source_ref,
        status=status,
        raw_payload={"name": "Renamed by the new rules"},
        ingestion_source=source,
        ingestion_warnings={} if warnings is None else warnings,
        owner_org=source.organisation,
    )


def _pending_config(body: str) -> str:
    """Read the confirm dialog's hidden `pending_config` out of the rendered page.

    The dialog hands the config back through the page rather than through a session (the P3 rule: one
    storage object, two views of it), so the value really is in the HTML and really is what the second
    request carries.
    """
    match = re.search(r'name="pending_config" value="([^"]*)"', body)
    assert match, "the confirmation dialog must carry `pending_config`"
    return html.unescape(match.group(1))


class TestApplyingTheMappingToExistingAlerts(_FeedFixture):
    """P7, AC20-AC23 — "Save & apply" is two steps, and the first step writes nothing."""

    def _client(self, feed):
        _, admin, source = feed
        return _ui_client(admin), source

    def test_both_cards_offer_the_apply_button(self, feed) -> None:
        client, source = feed and self._client(feed)

        body = client.get(self._url(source)).content.decode()

        assert body.count('name="intent" value="apply"') == 2, "one per writer (table, JSON)"

    def test_the_apply_intent_writes_nothing_at_all(self, feed) -> None:
        """AC20 — the first step validates and *counts*. No mapping, no alerts, no audit."""
        client, source = self._client(feed)
        alert = _mk_open_alert(source, source_ref="keep", title="Before")
        updated_before = source.updated_at

        response = client.post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "mapping-apply-modal" in body
        assert "Apply this mapping to existing alerts?" in body
        source.refresh_from_db()
        alert.refresh_from_db()
        assert source.mapping_config == MAPPING
        assert source.updated_at == updated_before
        assert alert.title == "Before"
        assert not AuditLog.objects.exists()

    def test_the_confirm_step_saves_and_remaps_and_audits(self, feed) -> None:
        """AC21 — one transaction: the rules that were saved are the rules that were applied."""
        client, source = self._client(feed)
        alert = _mk_open_alert(source, source_ref="apply-me", title="Before")

        preview = client.post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )
        assert "1 unlinked open alert" in preview.content.decode()

        response = client.post(
            self._url(source),
            {
                "intent": "apply_confirmed",
                "pending_config": _pending_config(preview.content.decode()),
            },
            follow=True,
        )

        assert response.status_code == 200
        source.refresh_from_db()
        alert.refresh_from_db()
        assert source.mapping_config == APPLY_MAPPING
        assert alert.title == "Renamed by the new rules"
        assert alert.severity == 2, "a field the mapping does not answer is left readable"

        applied = AuditLog.objects.get(action="ingestion_source.mapping_applied")
        # `refreshed` is a sub-count of `unchanged`: this row's fields changed, so it is 0 here. It is
        # recorded on every apply because "0 updated, 19 unchanged" alone reads as "nothing happened"
        # even when 19 records were rewritten (MAP-12).
        assert applied.details["alerts"] == {
            "candidates": 1,
            "updated": 1,
            "unchanged": 0,
            "refreshed": 0,
        }
        assert applied.details["changed"], "the config half of the row names the changed keys"
        assert applied.target_id == str(source.id)

    def test_a_tampered_pending_config_is_rejected_like_any_other_bad_config(self, feed) -> None:
        """The config comes back through the client, so it is validated again — not trusted."""
        client, source = self._client(feed)
        alert = _mk_open_alert(source, source_ref="tampered", title="Before")

        response = client.post(
            self._url(source),
            {
                "intent": "apply_confirmed",
                "pending_config": json.dumps(
                    {"version": 2, "fields": {"title": {"paths": ["name"]}}}
                ),
            },
        )

        assert response.status_code == 200
        assert "must start with $" in response.content.decode()
        source.refresh_from_db()
        alert.refresh_from_db()
        assert source.mapping_config == MAPPING
        assert alert.title == "Before"
        assert not AuditLog.objects.exists()

    def test_over_the_cap_the_dialog_disables_its_confirm(self, feed, monkeypatch) -> None:
        """AC21 — a "save & apply" that drops the apply half is worse than a refusal."""
        from ingest import remap

        client, source = self._client(feed)
        monkeypatch.setattr(remap, "MAX_REMAP_ALERTS", 1)
        _mk_open_alert(source, source_ref="one")
        _mk_open_alert(source, source_ref="two")

        response = client.post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )

        body = response.content.decode()
        assert response.status_code == 200
        assert "Nothing will be changed" in body or "over the" in body
        assert "disabled" in body, (
            "the confirm control must be disabled server-side, not just hidden"
        )

        follow = client.post(
            self._url(source),
            {"intent": "apply_confirmed", "pending_config": _pending_config(body)},
            follow=True,
        )
        assert follow.status_code == 200
        source.refresh_from_db()
        assert source.mapping_config == MAPPING, (
            "nothing is written over the cap, not even the config"
        )
        assert not AuditLog.objects.exists()

    def test_an_unchanged_mapping_still_remaps_but_only_audits_the_apply(self, feed) -> None:
        """AC12 holds under P7: re-posting the stored config is not an edit — yet the alerts move."""
        client, source = self._client(feed)
        alert = _mk_open_alert(source, source_ref="already-stored", title="Before")
        source.mapping_config = APPLY_MAPPING
        source.save(update_fields=["mapping_config"])

        response = client.post(
            self._url(source),
            {
                "intent": "apply_confirmed",
                "pending_config": json.dumps(APPLY_MAPPING, sort_keys=True),
            },
            follow=True,
        )

        assert response.status_code == 200
        alert.refresh_from_db()
        assert alert.title == "Renamed by the new rules"
        assert not AuditLog.objects.filter(action="ingestion_source.updated").exists()
        applied = AuditLog.objects.get(action="ingestion_source.mapping_applied")
        assert applied.details["changed"] == []

    def test_an_outsider_cannot_apply_across_orgs(self, feed) -> None:
        """AC23 — the apply path is gated exactly like the read path (ADR-006 + ADR-009)."""
        _, _, source = feed
        outsider = _mk_user("apply-outsider", _mk_org("Other Org"), role="admin")
        alert = _mk_open_alert(source, source_ref="outsider", title="Before")

        response = _ui_client(outsider).post(
            self._url(source),
            {"intent": "apply_confirmed", "pending_config": json.dumps(APPLY_MAPPING)},
        )

        assert response.status_code == 404
        alert.refresh_from_db()
        assert alert.title == "Before"
        assert not AuditLog.objects.exists()

    def test_an_analyst_reaches_neither_the_button_nor_the_post(self, feed) -> None:
        """AC21 — the button is the same gate as the page: uniform 404, before any config is read."""
        org, _, source = feed
        analyst = _mk_user("apply-analyst", org, role="analyst")

        get = _ui_client(analyst).get(self._url(source))
        post = _ui_client(analyst).post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )

        assert get.status_code == 404
        assert post.status_code == 404
        source.refresh_from_db()
        assert source.mapping_config == MAPPING
        assert not AuditLog.objects.exists()

    def test_a_superadmins_cross_org_apply_is_audited_with_both_orgs(self, feed) -> None:
        """ADR-004 Q5 — the break-glass path is never silent, on either half of the operation."""
        _, _, source = feed
        home = _mk_org("Super Org")
        superadmin = _mk_user("apply-super", home, role="superAdmin")
        _mk_open_alert(source, source_ref="super-seen", title="Before")

        preview = _ui_client(superadmin).post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )
        response = _ui_client(superadmin).post(
            self._url(source),
            {
                "intent": "apply_confirmed",
                "pending_config": _pending_config(preview.content.decode()),
            },
            follow=True,
        )

        assert response.status_code == 200
        applied = AuditLog.objects.get(action="ingestion_source.mapping_applied")
        assert applied.actor_org_id == home.pk
        assert applied.target_org_id == source.organisation_id

    def test_with_no_candidates_the_mapping_is_saved_and_the_flash_says_so(self, feed) -> None:
        """AC23 — "apply" never means "I refuse to save": zero alerts is a success, not an error."""
        client, source = self._client(feed)

        preview = client.post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )
        body = preview.content.decode()
        assert "there is nothing to re-apply it to" in body

        response = client.post(
            self._url(source),
            {"intent": "apply_confirmed", "pending_config": _pending_config(body)},
            follow=True,
        )

        assert response.status_code == 200
        source.refresh_from_db()
        assert source.mapping_config == APPLY_MAPPING
        assert "No unlinked open alerts to re-map." in response.content.decode()

    # --- P7.1 (MAP-12, AC24/AC25/AC26) — the dialog explains what a pass would do, and what it cannot.

    def test_the_confirm_dialog_shows_a_dry_run_per_field(self, feed) -> None:
        """AC24 — the numbers come from a read of the real alerts, not from the rule table.

        `_table_post(title=…)` leaves every other row empty, so the field under test is the only one with
        a rule: `title` would be rewritten and `severity` has no rule at all. Both halves matter, and the
        one an operator cannot get from "N alerts" is the second.
        """
        client, source = self._client(feed)
        _mk_open_alert(source, source_ref="previewed", title="Before")

        response = client.post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )

        body = response.content.decode()
        assert response.status_code == 200
        assert "Would change" in body
        assert "Produced no value" in body
        # `title`: the payload answers `$.name` and the alert stores "Before", so exactly one row moves.
        assert "1 of 1" in body
        # `severity`: no rule, so the engine falls back to the feed default and records why. This is the
        # line that turns a silent no-op into a diagnosis.
        assert "nothing at the configured path" in body

    def test_the_flash_names_the_rule_that_produced_no_value(self, feed) -> None:
        """AC25 — "0 updated, 1 unchanged" is not a result the operator can act on; the reason is."""
        client, source = self._client(feed)
        _mk_open_alert(source, source_ref="unreadable", title="Before")

        preview = client.post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )
        response = client.post(
            self._url(source),
            {
                "intent": "apply_confirmed",
                "pending_config": _pending_config(preview.content.decode()),
            },
            follow=True,
        )

        body = response.content.decode()
        assert response.status_code == 200
        assert "severity: the mapping produced no value on 1 of 1 alert (not_found)" in body

    def test_the_flash_says_the_record_moved_when_only_the_record_moved(self, feed) -> None:
        """MAP-12 — a pass that rewrote the alert's `mapping` record must not be reported as a no-op."""
        client, source = self._client(feed)
        alert = _mk_open_alert(source, source_ref="record-only", title="Renamed by the new rules")
        Alert.objects.filter(pk=alert.pk).update(
            ingestion_warnings={
                "mapping": [{"field": "title", "reason": "not_found"}],
                "warnings": ["severity_defaulted: $.severity → 2"],
            }
        )

        preview = client.post(
            self._url(source), {**_table_post(title=("$.name", "str", "")), "intent": "apply"}
        )
        response = client.post(
            self._url(source),
            {
                "intent": "apply_confirmed",
                "pending_config": _pending_config(preview.content.decode()),
            },
            follow=True,
        )

        body = response.content.decode()
        alert.refresh_from_db()
        assert response.status_code == 200
        assert alert.title == "Renamed by the new rules", "the values were already correct"
        assert "Re-applied to 1 unlinked open alert: 0 updated, 1 unchanged." in body
        assert "Rule state refreshed on 1 alert with no field values changed." in body
        # The stale `title` entry is replaced by the *live* state of this config: it has no severity rule,
        # so severity is the one thing left unresolved, and saying so is the point of the whole change.
        assert alert.ingestion_warnings["mapping"] == [{"field": "severity", "reason": "not_found"}]
        assert alert.ingestion_warnings["warnings"] == ["severity_defaulted: $.severity → 2"]
        applied = AuditLog.objects.get(action="ingestion_source.mapping_applied")
        assert applied.details["alerts"]["refreshed"] == 1

    def test_the_dialog_names_the_rules_a_remap_never_reapplies(self, feed) -> None:
        """AC26 — a `source_ref` rule is stored, works for new alerts, and is never applied to old ones.

        That is a real limitation (MAP-9, the dedupe/replay key), and an operator who adds a reference
        mapping and sees no change on existing alerts must be told so *before* confirming, not left to
        infer it from a count.
        """
        client, source = self._client(feed)
        _mk_open_alert(source, source_ref="old-row", title="Before")

        response = client.post(
            self._url(source),
            {
                **_table_post(
                    title=("$.name", "str", ""),
                    source_ref=("$.falcon_host_link", "str", ""),
                ),
                "intent": "apply",
            },
        )

        body = response.content.decode()
        assert response.status_code == 200
        assert "Not re-applied at all" in body
        assert "these are the alert's identity" in body
        # The rule is real and the operator must be able to see what it was, not just that it was skipped.
        assert "$.falcon_host_link" in body


#: The stored record `UNREADABLE_SEVERITY` leaves: a rule that ran, produced `"70"`, and could not be
#: read as a severity. Copied from what `ingest/pipeline.py` writes (`FieldWarning.as_dict`).
UNRESOLVED_SEVERITY_RECORD: dict = {
    "mapping": [
        {
            "field": "severity",
            "reason": "bad_type",
            "paths": ["$.severity"],
            "value_preview": "70",
        }
    ]
}


class TestTheMappingHealthCard(_FeedFixture):
    """P5 — the feed page summarises the records its alerts already carry.

    The alert page shows one row's mapping record (P7.1); this is the same record aggregated, so
    "19 of 19 alerts have a severity rule that produced nothing" is visible on the feed page instead of
    inside nineteen rows. It reads the *stored* record, so it can never disagree with the row it sends
    you to — and the confirm dialog stays the thing that previews a fresh pass.
    """

    def _client(self, feed):
        _, admin, source = feed
        return _ui_client(admin), source

    def test_a_clean_feed_says_so(self, feed) -> None:
        client, source = self._client(feed)
        _mk_open_alert(source, source_ref="fine")

        body = client.get(self._url(source)).content.decode()

        assert "Mapping health" in body
        assert "no rule in the stored mapping records is unresolved" in body

    def test_it_names_the_field_the_reason_and_the_count(self, feed) -> None:
        client, source = self._client(feed)
        for ref in ("a", "b"):
            _mk_open_alert(source, source_ref=ref, warnings=UNRESOLVED_SEVERITY_RECORD)
        _mk_open_alert(source, source_ref="fine")

        body = client.get(self._url(source)).content.decode()

        assert "<strong>2</strong> of 3 unlinked open" in body
        assert "severity" in body
        # Both halves: the sentence an operator reads, and the stable token the engine stores.
        assert "a value of the wrong type" in body
        assert "bad_type" in body

    def test_it_points_at_the_control_that_repairs_it(self, feed) -> None:
        client, source = self._client(feed)
        _mk_open_alert(source, source_ref="broken", warnings=UNRESOLVED_SEVERITY_RECORD)

        body = client.get(self._url(source)).content.decode()

        assert "Apply to unlinked alerts" in body
        assert "previews the result per field" in body

    def test_an_empty_feed_does_not_claim_health(self, feed) -> None:
        """Nothing to read is not the same as "everything is fine" — the card says which it is."""
        client, source = self._client(feed)

        body = client.get(self._url(source)).content.decode()

        assert "Mapping health" in body
        assert "No unlinked open alerts from this feed" in body

    def test_an_alert_this_card_cannot_count_is_not_counted(self, feed) -> None:
        """A linked alert keeps its record and its own page shows it — but a pass would not touch it."""
        from cases.models import Case, CaseStatus

        client, source = self._client(feed)
        status, _ = CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )
        case = Case.objects.create(title="Escalated", status=status, owner_org=source.organisation)
        alert = _mk_open_alert(source, source_ref="linked", warnings=UNRESOLVED_SEVERITY_RECORD)
        alert.case = case
        alert.save(update_fields=["case"])

        body = client.get(self._url(source)).content.decode()

        assert "No unlinked open alerts from this feed" in body
        assert "rule that produced no value" not in body
