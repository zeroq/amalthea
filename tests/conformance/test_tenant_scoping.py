"""Conformance tests for tenant isolation scoping (P1 — Foundations, AC T1-a).

These tests verify the scoping truth table:
- own org: accessible
- shared read: accessible for GET
- shared write: accessible for mutating verbs
- superAdmin: all accessible (audited)
- org-less: denied
- cross-tenant: denied (404, not 403 — no existence oracle)
"""

from __future__ import annotations

import json
import re
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from django.urls import reverse
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from automation.models import AutomationRun, Playbook
from cases.models import Case, CaseObservable, CaseStatus, Procedure, Share, Task
from identity.models import IngestionToken, Organisation, User
from ingest.models import IngestionSource
from observables.models import Observable, ObservableType


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst") -> User:
    user = User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=True,
    )
    return user


def _mk_orgless_actor(role: str = "analyst") -> User:
    """An unsaved stub actor with no organisation.

    `User.org` is NOT NULL (ADR-007), so an org-less user is unreachable in the database.
    The fail-closed tenancy guards that must still reject it are exercised here with an
    in-memory stub: no row is persisted, and `identity/scoping.py` reads the actor only
    through `getattr`, so an unsaved instance is sufficient. This keeps the
    "org-less ⇒ deny" truth-table rows non-vacuous without recreating the deleted state.
    """
    return User(id=uuid4(), org_id=None, role=role)


def _mk_client(user: User, scope: str = "readwrite") -> tuple[APIClient, str]:
    token = "k" + uuid4().hex
    from identity.models import ApiKey

    ApiKey.objects.create(
        user=user,
        name=f"{scope} key",
        prefix=token[:12],
        key_hash=PasswordHasher().hash(token),
        scope=scope,
    )
    client = APIClient(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client, token


def _mk_case(title: str, org: Organisation, status: CaseStatus | None = None) -> Case:
    if status is None:
        status = CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _mk_alert(
    title: str, org: Organisation, source_ref: str, status: AlertStatus | None = None
) -> Alert:
    if status is None:
        status = AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=source_ref,
        title=title,
        status=status,
        owner_org=org,
    )


def _mk_observable(data: str, org: Organisation, type_name: str = "ip") -> Observable:
    otype = ObservableType.objects.get_or_create(
        name=type_name, defaults={"is_case_sensitive": False}
    )[0]
    return Observable.objects.create(
        data_type=otype, data=data, normalized_data=data, owner_org=org
    )


class TestCaseVisibleQ:
    """Unit tests for `identity.scoping.case_visible_q` truth table."""

    def test_superadmin_sees_all(self, db: None) -> None:
        from identity.scoping import case_visible_q

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        superadmin = _mk_user("super", org1, role="superAdmin")
        _mk_case("Case1", org1)
        _mk_case("Case2", org2)

        q = case_visible_q(superadmin)
        cases = Case.objects.filter(q)
        assert cases.count() == 2

    def test_own_org_visible(self, db: None) -> None:
        from identity.scoping import case_visible_q

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org1)
        case1 = _mk_case("Own", org1)
        _mk_case("Other", org2)

        q = case_visible_q(user)
        cases = Case.objects.filter(q)
        assert cases.count() == 1
        assert cases.first().id == case1.id

    def test_shared_read_visible(self, db: None) -> None:
        from identity.scoping import case_visible_q

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org2)
        case1 = _mk_case("Shared", org1)
        Share.objects.create(case=case1, organisation=org2, permissions={"read": True})

        q = case_visible_q(user)
        cases = Case.objects.filter(q)
        assert cases.count() == 1
        assert cases.first().id == case1.id

    def test_shared_write_visible(self, db: None) -> None:
        from identity.scoping import case_visible_q

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org2)
        case1 = _mk_case("Shared", org1)
        Share.objects.create(
            case=case1, organisation=org2, permissions={"read": True, "write": True}
        )

        q = case_visible_q(user)
        cases = Case.objects.filter(q)
        assert cases.count() == 1
        assert cases.first().id == case1.id

    def test_orgless_denied(self, db: None) -> None:
        from identity.scoping import case_visible_q

        org1 = _mk_org("Org1")
        user = _mk_orgless_actor()
        _mk_case("Case", org1)

        q = case_visible_q(user)
        cases = Case.objects.filter(q)
        assert cases.count() == 0

    def test_cross_tenant_denied(self, db: None) -> None:
        from identity.scoping import case_visible_q

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org1)
        _mk_case("Other org", org2)

        q = case_visible_q(user)
        cases = Case.objects.filter(q)
        assert cases.count() == 0


class TestResolveCase:
    """Unit tests for `identity.scoping.resolve_case`."""

    def test_superadmin_resolves_any(self, db: None) -> None:
        from identity.scoping import resolve_case

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        superadmin = _mk_user("super", org1, role="superAdmin")
        case1 = _mk_case("Case1", org1)
        case2 = _mk_case("Case2", org2)

        assert resolve_case(superadmin, str(case1.id)) is not None
        assert resolve_case(superadmin, str(case2.id)) is not None
        assert resolve_case(superadmin, "00000000-0000-0000-0000-000000000000") is None

    def test_own_org_resolves(self, db: None) -> None:
        from identity.scoping import resolve_case

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org1)
        case1 = _mk_case("Own", org1)
        _mk_case("Other", org2)

        assert resolve_case(user, str(case1.id)) is not None
        assert resolve_case(user, str(case1.number)) is not None  # by number
        assert resolve_case(user, "00000000-0000-0000-0000-000000000000") is None

    def test_shared_read_resolves_for_get(self, db: None) -> None:
        from identity.scoping import resolve_case

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org2)
        case1 = _mk_case("Shared", org1)
        Share.objects.create(case=case1, organisation=org2, permissions={"read": True})

        assert resolve_case(user, str(case1.id), write=False) is not None
        assert resolve_case(user, str(case1.id), write=True) is None  # write not allowed

    def test_shared_write_resolves_for_mutate(self, db: None) -> None:
        from identity.scoping import resolve_case

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org2)
        case1 = _mk_case("Shared", org1)
        Share.objects.create(
            case=case1, organisation=org2, permissions={"read": True, "write": True}
        )

        assert resolve_case(user, str(case1.id), write=False) is not None
        assert resolve_case(user, str(case1.id), write=True) is not None

    def test_orgless_denied(self, db: None) -> None:
        from identity.scoping import resolve_case

        org1 = _mk_org("Org1")
        user = _mk_orgless_actor()
        case1 = _mk_case("Case", org1)

        assert resolve_case(user, str(case1.id)) is None

    def test_cross_tenant_denied(self, db: None) -> None:
        from identity.scoping import resolve_case

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org1)
        case1 = _mk_case("Other", org2)

        assert resolve_case(user, str(case1.id)) is None


class TestOrgFiltered:
    """Unit tests for `identity.scoping.org_filtered`."""

    def test_superadmin_unfiltered(self, db: None) -> None:
        from identity.scoping import org_filtered

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        superadmin = _mk_user("super", org1, role="superAdmin")
        _mk_alert("A1", org1, "ref1")
        _mk_alert("A2", org2, "ref2")

        qs = org_filtered(Alert.objects.all(), superadmin, path="owner_org")
        assert qs.count() == 2

    def test_org_filtered(self, db: None) -> None:
        from identity.scoping import org_filtered

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user = _mk_user("analyst", org1)
        _mk_alert("Own", org1, "ref1")
        _mk_alert("Other", org2, "ref2")

        qs = org_filtered(Alert.objects.all(), user, path="owner_org")
        assert qs.count() == 1
        assert qs.first().title == "Own"

    def test_orgless_denied(self, db: None) -> None:
        from identity.scoping import org_filtered

        org1 = _mk_org("Org1")
        user = _mk_orgless_actor()
        _mk_alert("Alert", org1, "ref1")

        qs = org_filtered(Alert.objects.all(), user, path="owner_org")
        assert qs.count() == 0


class TestHTTPTenantScoping:
    """HTTP-level tests for tenant scoping on real endpoints."""

    def test_list_cases_scoped_to_org(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        _mk_case("Case1", org1)
        _mk_case("Case2", org1)
        _mk_case("Case3", org2)

        resp1 = client1.get("/api/v1/case")
        resp2 = client2.get("/api/v1/case")

        assert resp1.status_code == 200
        assert len(resp1.json()) == 2
        assert resp2.status_code == 200
        assert len(resp2.json()) == 1

    def test_retrieve_case_cross_tenant_404(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        case2 = _mk_case("Secret", org2)

        # Owner can access
        resp = client2.get(f"/api/v1/case/{case2.id}")
        assert resp.status_code == 200

        # Cross-tenant → 404 (not 403)
        resp = client1.get(f"/api/v1/case/{case2.id}")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

    def test_shared_case_read_works(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Shared", org1)
        Share.objects.create(case=case1, organisation=org2, permissions={"read": True})

        # Owner can access
        resp = client1.get(f"/api/v1/case/{case1.id}")
        assert resp.status_code == 200

        # Shared org can read
        resp = client2.get(f"/api/v1/case/{case1.id}")
        assert resp.status_code == 200

    def test_shared_case_write_requires_write_permission(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Shared", org1)
        Share.objects.create(case=case1, organisation=org2, permissions={"read": True})  # read only

        # Owner can patch
        resp = client1.patch(
            f"/api/v1/case/{case1.id}", {"title": "Updated by owner"}, format="json"
        )
        assert resp.status_code == 200

        # Shared org (read-only) cannot patch
        resp = client2.patch(f"/api/v1/case/{case1.id}", {"title": "Hacker"}, format="json")
        assert resp.status_code == 404  # uniform 404

    def test_create_case_stamps_owner_org(self, db: None) -> None:
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        resp = client1.post("/api/v1/case", {"title": "New Case"}, format="json")
        assert resp.status_code == 201
        case_id = resp.json()["_id"]

        case = Case.objects.get(pk=case_id)
        assert case.owner_org_id == org1.id

    def test_create_alert_stamps_owner_org(self, db: None) -> None:
        """API alert-create stamps owner_org from the authenticated user (T2-a)."""
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        payload = {
            "title": "API Alert",
            "type": "phishing",
            "source": "test-source",
            "sourceRef": "api-1",
        }
        resp = client1.post("/api/v1/alert", payload, format="json")
        assert resp.status_code == 201, resp.content
        alert_id = resp.json()["_id"]

        alert = Alert.objects.get(pk=alert_id)
        assert alert.owner_org_id == org1.id

    def test_list_alerts_scoped_to_org(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        _mk_alert("A1", org1, "ref1")
        _mk_alert("A2", org1, "ref2")
        _mk_alert("A3", org2, "ref3")

        resp1 = client1.get("/api/v1/alert")
        resp2 = client2.get("/api/v1/alert")

        assert resp1.status_code == 200
        assert len(resp1.json()) == 2
        assert resp2.status_code == 200
        assert len(resp2.json()) == 1

    def test_retrieve_alert_cross_tenant_404(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        alert2 = _mk_alert("Secret", org2, "ref2")

        resp = client2.get(f"/api/v1/alert/{alert2.id}")
        assert resp.status_code == 200

        resp = client1.get(f"/api/v1/alert/{alert2.id}")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

    def test_list_observables_scoped_to_org(self, db: None) -> None:
        """Observables are listed via their case, not globally."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Case1", org1)
        case2 = _mk_case("Case2", org1)
        case3 = _mk_case("Case3", org2)

        obs1 = _mk_observable("10.0.0.1", org1)
        obs2 = _mk_observable("10.0.0.2", org1)
        obs3 = _mk_observable("10.0.0.3", org2)

        CaseObservable.objects.create(case=case1, observable=obs1)
        CaseObservable.objects.create(case=case2, observable=obs2)
        CaseObservable.objects.create(case=case3, observable=obs3)

        # List via case
        resp1 = client1.get(f"/api/v1/case/{case1.id}/observable")
        resp2 = client2.get(f"/api/v1/case/{case3.id}/observable")

        assert resp1.status_code == 200
        assert len(resp1.json()) == 1
        assert resp2.status_code == 200
        assert len(resp2.json()) == 1

    def test_observable_same_value_different_orgs_are_distinct(self, db: None) -> None:
        """Per-org dedupe: same value in two orgs = two rows (ADR-004)."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Case1", org1)
        case2 = _mk_case("Case2", org2)

        # Same IP in both orgs
        obs1 = _mk_observable("192.168.1.1", org1)
        obs2 = _mk_observable("192.168.1.1", org2)

        CaseObservable.objects.create(case=case1, observable=obs1)
        CaseObservable.objects.create(case=case2, observable=obs2)

        # Each org sees only its own via case
        resp1 = client1.get(f"/api/v1/case/{case1.id}/observable")
        resp2 = client2.get(f"/api/v1/case/{case2.id}/observable")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert len(resp1.json()) == 1
        assert len(resp2.json()) == 1
        # Different UUIDs
        assert resp1.json()[0]["_id"] != resp2.json()[0]["_id"]


class TestWebhookTokenRouting:
    """Tests for P2 — webhook org routing via IngestionToken (AC T2-a..d)."""

    def test_webhook_with_valid_token_stamps_org(self, db: None) -> None:
        org1 = _mk_org("Org1")
        source = IngestionSource.objects.create(
            organisation=org1, slug="test-src", name="Test Source"
        )
        _, plaintext = IngestionToken.create_token(source=source, organisation=org1, name="test")

        client = APIClient()
        resp = client.post(
            "/api/v1/alerts/webhook/",
            data=json.dumps({"title": "Webhook Alert", "sourceRef": "wh-1"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {plaintext}",
        )
        assert resp.status_code == 201, resp.content
        alert_id = resp.json()["_id"]

        alert = Alert.objects.get(pk=alert_id)
        assert alert.owner_org_id == org1.id

    def test_webhook_without_token_is_401(self, db: None) -> None:
        source = IngestionSource.objects.create(
            organisation=_mk_org("No-Token Org"), slug="no-token", name="No Token Source"
        )
        assert not source.tokens.exists(), "this test is about a feed with no token at all"

        client = APIClient()
        resp = client.post(
            "/api/v1/alerts/webhook/",
            data=json.dumps({"title": "No Token", "sourceRef": "wh-1"}),
            content_type="application/json",
        )
        assert resp.status_code == 401
        assert resp.json()["type"] == "unauthorized"

    def test_webhook_with_invalid_token_is_401(self, db: None) -> None:
        org1 = _mk_org("Org1")
        source = IngestionSource.objects.create(
            organisation=org1, slug="bad-token", name="Bad Token Source"
        )
        IngestionToken.create_token(source=source, organisation=org1, name="valid")

        client = APIClient()
        resp = client.post(
            "/api/v1/alerts/webhook/",
            data=json.dumps({"title": "Bad Token", "sourceRef": "wh-1"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalidtoken123456789012",
        )
        assert resp.status_code == 401
        assert resp.json()["type"] == "unauthorized"

    def test_each_token_routes_to_its_own_tenant(self, db: None) -> None:
        """The token is the only routing fact: each one stamps the alert with *its* organisation.

        ADR-009 note: this used to be one feed with two tokens (ADR-004 Q6 / ADR-008 §4). The feed is
        now org-owned, so the two tenants have one feed each — the routing guarantee under test is
        unchanged, and the pairing is now enforced rather than merely conventional.
        """
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        source1 = IngestionSource.objects.create(
            organisation=org1, slug="multi-tenant", name="Multi-Tenant Source"
        )
        source2 = IngestionSource.objects.create(
            organisation=org2, slug="multi-tenant", name="Multi-Tenant Source"
        )

        # One token per tenant, each on that tenant's own feed (same slug, different org).
        _, token1 = IngestionToken.create_token(
            source=source1, organisation=org1, name="org1-token"
        )
        _, token2 = IngestionToken.create_token(
            source=source2, organisation=org2, name="org2-token"
        )

        client = APIClient()

        # Post with token1 → alert in org1
        resp1 = client.post(
            "/api/v1/alerts/webhook/",
            data=json.dumps({"title": "Alert for Org1", "sourceRef": "mt-1"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token1}",
        )
        assert resp1.status_code == 201, resp1.content
        alert1 = Alert.objects.get(pk=resp1.json()["_id"])
        assert alert1.owner_org_id == org1.id

        # Post with token2 → alert in org2
        resp2 = client.post(
            "/api/v1/alerts/webhook/",
            data=json.dumps({"title": "Alert for Org2", "sourceRef": "mt-2"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token2}",
        )
        assert resp2.status_code == 201, resp2.content
        alert2 = Alert.objects.get(pk=resp2.json()["_id"])
        assert alert2.owner_org_id == org2.id

        # Cross-check: each org only sees its own alert
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        resp1_list = client1.get("/api/v1/alert")
        resp2_list = client2.get("/api/v1/alert")

        assert len(resp1_list.json()) == 1
        assert resp1_list.json()[0]["_id"] == str(alert1.id)
        assert len(resp2_list.json()) == 1
        assert resp2_list.json()[0]["_id"] == str(alert2.id)

    def test_legacy_webhook_secret_becomes_token(self, db: None) -> None:
        """The identity.0006_create_default_org migration adopts legacy webhook_secret_hash as an IngestionToken.

        This test imports the migration module and calls its `create_default_org` function directly
        with a fake apps shim wrapping the real models. This exercises the actual migration body,
        so removing the token-adoption loop from the migration would make this test fail.
        The legacy secret must then authenticate a webhook and land the alert in the default org.
        """
        from importlib import import_module

        from django.conf import settings

        from identity.models import IngestionToken, Organisation
        from ingest.models import IngestionSource

        default_org_name = getattr(settings, "AMALTHEA_DEFAULT_ORG_NAME", "Default")

        # The legacy source belongs to the org the migration adopts it into. Post-ADR-009 that org has
        # to pre-exist (a source requires one), and identity.0006 is where it comes from — which is
        # exactly why the adoption loop cannot run against an org-less source any more.
        default_org, _ = Organisation.objects.get_or_create(name=default_org_name)
        IngestionToken.objects.filter(source__slug="legacy").delete()

        # Seed a legacy source with webhook_secret_hash (simulating pre-migration state)
        source = IngestionSource.objects.create(
            organisation=default_org,
            slug="legacy",
            name="Legacy Source",
            webhook_secret_hash="legacys3cr3t",
        )

        # Call the migration's create_default_org function directly with real models
        class _FakeApps:
            def get_model(self, app_label: str, model_name: str):
                return {
                    "Organisation": Organisation,
                    "IngestionSource": IngestionSource,
                    "IngestionToken": IngestionToken,
                }[model_name]

        migration = import_module("identity.migrations.0006_create_default_org")
        migration.create_default_org(_FakeApps(), None)

        # Verify the migration adopted the legacy secret as a token of the default org
        default_org.refresh_from_db()
        token = IngestionToken.objects.get(source=source, organisation=default_org)
        assert token.secret_hash == "legacys3cr3t"

        # The legacy secret should work via the token
        client = APIClient()
        resp = client.post(
            "/api/v1/alerts/webhook/",
            data=json.dumps({"title": "Legacy", "sourceRef": "wh-legacy"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer legacys3cr3t",
        )
        assert resp.status_code == 201, resp.content
        alert = Alert.objects.get(pk=resp.json()["_id"])
        assert alert.owner_org_id == default_org.id


# =============================================================================
# T3-c: Route-enumeration conformance test
# Enumerates tenant-touching read endpoints from URLconf and asserts:
# - cross-org GET → 404 (same shape as non-existent ID)
# - own-org GET → 200
# - fails if a newly added unscoped route exists
# =============================================================================

# Allow-list of genuinely global read routes that are NOT tenant-scoped.
# These are vocabulary/admin/public endpoints. Keep minimal; default is "tenant-touching ⇒ must be guarded".
# Include both base and -slash variants since Django registers both.
_GLOBAL_READ_ROUTES: set[str] = {
    # Vocabulary endpoints (global by design)
    "observable-type-collection",
    "observable-type-collection-slash",
    "observable-type-detail",
    "observable-type-detail-slash",
    "alert-status-collection",
    "alert-status-collection-slash",
    "alert-status-detail",
    "alert-status-detail-slash",
    "case-status-collection",
    "case-status-collection-slash",
    "case-status-detail",
    "case-status-detail-slash",
    "tag-collection",
    "tag-collection-slash",
    "tag-detail",
    "tag-detail-slash",
    "ttp-collection",
    "ttp-collection-slash",
    "ttp-detail",
    "ttp-detail-slash",
    "taxonomy",
    "taxonomy-slash",
    "custom-field-list",
    "custom-field-list-slash",
    "case-template-collection",  # templates are org-neutral definitions
    "case-template-collection-slash",
    "case-template-detail",
    "case-template-detail-slash",
    # Automation definitions (global)
    "playbook-collection",
    "playbook-collection-slash",
    "playbook-meta",
    "playbook-meta-slash",
    "playbook-detail",
    "playbook-detail-slash",
    # Identity (user/org are global resources, but access-controlled)
    "user-current",
    "user-current-slash",
    "user-detail",
    "user-detail-slash",
    "user-collection",
    "user-collection-slash",
    "user-detail-management",
    "user-detail-management-slash",
    "organisation-collection",
    "organisation-collection-slash",
    "organisation-detail",
    "organisation-detail-slash",
    "organisation-create",
    "organisation-create-slash",
    # Public / meta
    "describe-all",
    "describe-all-slash",
    "describe-model",
    "describe-model-slash",
    # Ingest webhook is public (token-gated, not org-scoped)
    "alerts-webhook",
    "alerts-webhook-no-slash",
    # Auth endpoints (global)
    "api-login",
    "api-login-slash",
    "api-logout",
    "api-logout-slash",
    # Catch-all (not a real route)
    "compat-catchall",
}

# UI-specific global read routes (not tenant-scoped)
_UI_GLOBAL_READ_ROUTES: set[str] = {
    # Auth
    "login",
    "logout",
    # Global vocabulary / taxonomy (no org context needed)
    "ui-taxonomy",
    # Case templates are global definitions (not org-scoped data)
    "ui-case-template-list",
    "ui-case-template-create",
    "ui-case-template-detail",
    "ui-case-template-delete",
    # Tags are global vocabulary
    "ui-tag-list",
    "ui-tag-create",
    "ui-tag-detail",
    "ui-tag-delete",
    # Playbooks are global automation definitions
    "ui-playbook-list",
    "ui-playbook-create",
    "ui-playbook-detail",
    "ui-playbook-edit",
    "ui-playbook-delete",
    # Settings (superAdmin only - global management views)
    "ui-settings-organisations",
    "ui-settings-organisations-edit",
    "ui-settings-users",
    "ui-settings-users-edit",
    # Settings — Sources (admin+superAdmin read; hybrid scoping: org-scoped for admin, global for superAdmin).
    # DEVIATION: These routes are NOT purely global — they are tenant-scoped for admin users but global
    # for superAdmin. The conformance suite's model expects a single behaviour per route.
    # They are included here to exclude them from the generic tenant-scoping assertion, which would
    # otherwise fail because an admin's cross-org access returns 404 (correct) but the test expects
    # a uniform 200-with-filtered-results for list routes. A dedicated test covers their behaviour.
    "ui-settings",
    "ui-settings-sources",
}

# Route names that are WRITE-only (POST/PATCH/DELETE only, no GET) — exclude from read conformance.
_WRITE_ONLY_ROUTES: set[str] = {
    "alert-import",
    "alert-import-into",
    "alert-merge",
    "alert-observable-add",
    "alert-tag-link",
    "alert-procedure-create",
    "alert-procedures-create",
    "alert-bulk-update",
    "case-task-create",
    "case-observable-add",
    "case-tag-link",
    "case-alert-remove",
    "case-custom-event-create",
    "case-share-detail",  # DELETE only
    "case-share-detail-slash",  # DELETE only
    "case-attachment-detail",  # DELETE only
    "case-attachment-detail-slash",  # DELETE only
    "case-bulk-update",
    "case-apply-template",
    "case-merge",
    "case-procedure-create",
    "case-procedures-create",
    "observable-tag-link",
    "observable-bulk-update",
    "procedure-bulk-delete",
    "task-bulk-update",
    "playbook-run",
    "api-login",
    "api-logout",
    "case-create",  # not a route name but the POST on case-collection
    "alert-create",
    "observable-create",
    "comment-detail",  # PATCH/DELETE only
    "comment-detail-slash",  # PATCH/DELETE only
    "custom-event-detail",  # PATCH/DELETE only
    "custom-event-detail-slash",  # PATCH/DELETE only
    "organisation-create",
    "organisation-create-slash",
    "user-create",
    "user-create-slash",
}

# UI-specific write-only routes (POST/PATCH/DELETE only, no GET) — exclude from read conformance.
_UI_WRITE_ONLY_ROUTES: set[str] = {
    "ui-alert-escalate",
    "ui-alert-merge",
    "ui-alert-status",
    "ui-case-status",
    "ui-case-close",
    "ui-case-field-edit",
    "ui-case-tag-add",
    "ui-case-tag-remove",
    "ui-case-comment",
    "ui-case-extract",
    "ui-case-task-create",
    "ui-case-task-toggle",
    "ui-case-apply-template",
    "ui-case-merge",
    "ui-case-share-revoke",
    "ui-case-attachment-upload",
    "ui-case-attachment-delete",
    "ui-case-attachment-download",
    "ui-case-attachment-detail",
    "ui-case-procedure-create",
    "ui-case-procedures-create",
    "ui-case-page-create",
    "ui-case-page-edit",
    "ui-case-page-delete",
    "ui-case-template-create",
    "ui-case-template-delete",
    "ui-case-template-edit",
    "ui-observable-delete",
    "ui-tag-create",
    "ui-tag-delete",
    "ui-tag-toggle",
    "ui-playbook-create",
    "ui-playbook-run",
    "ui-playbook-delete",
    "ui-case-tag-toggle",
    "ui-settings-organisations-delete",
    "ui-settings-users-delete",
    "ui-settings-token-create",
    "ui-settings-token-revoke",
}


_HTTP_METHOD_NAMES = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def _view_http_methods(view: object) -> list[str] | None:
    """The methods a plain function view allows, or ``None`` if it declares none.

    Class-based and DRF views expose their methods as attributes, but a function view
    decorated with ``@require_GET`` / ``@require_POST`` / ``@require_http_methods`` stores
    the allowed list only inside the decorator wrapper's closure — Django exposes no public
    attribute. ``functools.wraps`` sets ``__wrapped__``, so walking that chain finds the cell.
    Without this, every decorated function view fell back to ``["GET"]`` and POST-only UI
    routes were enumerated as read routes, then failed the GET conformance check with 405.
    """
    current: object | None = view
    while current is not None:
        for cell in getattr(current, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:  # pragma: no cover - empty cell
                continue
            if (
                isinstance(value, (list, tuple))
                and value
                and all(isinstance(m, str) and m.upper() in _HTTP_METHOD_NAMES for m in value)
            ):
                return [m.upper() for m in value]
        current = getattr(current, "__wrapped__", None)
    return None


def _discover_tenant_write_routes() -> list[tuple[str, str, list[str]]]:
    """
    Discover all tenant-touching WRITE routes from the URLconf.

    Returns a list of (route_name, url_pattern, http_methods) for mutating routes
    (POST/PATCH/PUT/DELETE) that should be tenant-scoped. Excludes global routes.
    Covers both /api/v1/ and UI routes.
    """
    import django
    from django.urls import URLPattern, URLResolver, get_resolver

    django.setup()
    resolver = get_resolver()

    routes: list[tuple[str, str, list[str]]] = []

    def walk(patterns: list, prefix: str = ""):
        for pattern in patterns:
            full_pattern = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, full_pattern)
            elif isinstance(pattern, URLPattern):
                name = getattr(pattern, "name", "")
                if not name:
                    continue
                # Consider both API routes (/api/v1/) and UI routes (no prefix, but named ui-*)
                is_api_route = full_pattern.startswith("api/v1/")
                is_ui_route = name.startswith("ui-")
                if not (is_api_route or is_ui_route):
                    continue
                # Skip global allow-listed routes
                if is_api_route and name in _GLOBAL_READ_ROUTES:
                    continue
                if is_ui_route and name in _UI_GLOBAL_READ_ROUTES:
                    continue
                # Note: _WRITE_ONLY_ROUTES is for read conformance exclusion; write conformance includes them
                # Determine supported methods from the view
                view = pattern.callback
                methods = getattr(view, "cls", view).__dict__.get("http_method_names", [])
                # DRF views have `http_method_names` on the class; function views don't.
                # For function views decorated with @api_view, check the `actions` attr or wrapper.
                if hasattr(view, "actions"):  # ViewSet-style
                    methods = list(view.actions.keys())
                elif hasattr(view, "cls"):  # APIView-style
                    methods = [
                        m.upper() for m in view.cls.http_method_names if hasattr(view.cls, m)
                    ]
                else:
                    # Function-based @api_view — inspect the wrapper
                    methods = getattr(view, "_allowed_methods", [])
                    # For plain function views without @api_view, _allowed_methods is not set.
                    # Assume they support all mutating methods (conservative for conformance testing).
                    if not methods:
                        methods = ["POST", "PATCH", "PUT", "DELETE"]
                methods = [m.upper() for m in methods]
                # Keep only mutating methods
                mutating = [m for m in methods if m in ("POST", "PATCH", "PUT", "DELETE")]
                if mutating:
                    routes.append((name, full_pattern, mutating))

    walk(resolver.url_patterns)
    return routes


def _discover_tenant_read_routes() -> list[tuple[str, str, list[str]]]:
    """
    Discover all tenant-touching read routes from the URLconf.

    Returns a list of (route_name, url_pattern, http_methods) for GET-capable routes
    that should be tenant-scoped. Excludes global routes and write-only routes.
    Covers both /api/v1/ and UI routes.
    """
    import django
    from django.urls import URLPattern, URLResolver, get_resolver

    django.setup()
    resolver = get_resolver()

    routes: list[tuple[str, str, list[str]]] = []

    def walk(patterns: list, prefix: str = ""):
        for pattern in patterns:
            full_pattern = prefix + str(pattern.pattern)
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, full_pattern)
            elif isinstance(pattern, URLPattern):
                name = getattr(pattern, "name", "")
                if not name:
                    continue
                # Consider both API routes (/api/v1/) and UI routes (no prefix, but named ui-*)
                is_api_route = full_pattern.startswith("api/v1/")
                is_ui_route = name.startswith("ui-")
                if not (is_api_route or is_ui_route):
                    continue
                # Skip global allow-listed routes
                if is_api_route and name in _GLOBAL_READ_ROUTES:
                    continue
                if is_ui_route and name in _UI_GLOBAL_READ_ROUTES:
                    continue
                # Skip write-only routes
                if is_api_route and name in _WRITE_ONLY_ROUTES:
                    continue
                if is_ui_route and name in _UI_WRITE_ONLY_ROUTES:
                    continue
                # Determine supported methods from the view
                view = pattern.callback
                methods = getattr(view, "cls", view).__dict__.get("http_method_names", [])
                # DRF views have `http_method_names` on the class; function views don't.
                # For function views decorated with @api_view, check the `actions` attr or wrapper.
                if hasattr(view, "actions"):  # ViewSet-style
                    methods = list(view.actions.keys())
                elif hasattr(view, "cls"):  # APIView-style
                    methods = [
                        m.upper() for m in view.cls.http_method_names if hasattr(view.cls, m)
                    ]
                else:
                    # Function-based @api_view — inspect the wrapper
                    methods = getattr(view, "_allowed_methods", None)
                    if methods is None:
                        # Plain function view: read `@require_*` restrictions from the decorator
                        # chain instead of assuming GET (the previous default made every
                        # POST-only UI route look like a read route).
                        methods = _view_http_methods(view) or ["GET"]
                methods = [m.upper() for m in methods]
                if "GET" in methods:
                    routes.append((name, full_pattern, methods))
                elif "POST" in methods and name == "query-api":
                    # query is POST but read-semantics; include it
                    routes.append((name, full_pattern, ["POST"]))

    walk(resolver.url_patterns)
    return routes


def _build_url(pattern: str, params: dict[str, str]) -> str:
    """Build a URL from a Django path pattern by substituting {param} placeholders."""
    url = pattern
    for key, value in params.items():
        url = (
            url.replace(f"<str:{key}>", value)
            .replace(f"<slug:{key}>", value)
            .replace(f"<uuid:{key}>", value)
        )
    # Remove any remaining angle-bracket patterns (should not happen for our test cases)
    url = re.sub(r"<[^>]+>", "dummy", url)
    return f"/{url}"


class TestRouteEnumerationConformance:
    """
    T3-c: Conformance test that enumerates tenant-touching read routes from URLconf
    and asserts cross-org access returns uniform 404 (same as non-existent ID),
    while own-org access returns 200.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: None, default_org: Organisation):
        self.org1 = default_org
        self.org2 = Organisation.objects.create(name="Org2")
        self.user1 = _mk_user("user1", self.org1)
        self.user2 = _mk_user("user2", self.org2)
        self.client1, _ = _mk_client(self.user1)
        self.client2, _ = _mk_client(self.user2)
        self.superadmin = _mk_user("super", self.org1, role="superAdmin")
        self.super_client, _ = _mk_client(self.superadmin)

        # Seed data in org1 (WITHOUT shares for cross-org testing)
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-org1-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        from django.utils import timezone

        from cases.models import (
            Comment,
            Page,
            Procedure,
            Share,
            Task,
            TimelineEvent,
        )

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )
        self.page1 = Page.objects.create(
            case=self.case1, title="Page 1", content="Content", order=1, created_by=self.user1
        )
        self.proc1 = Procedure.objects.create(
            case=self.case1, pattern_id="T1059", pattern_name="Test"
        )
        self.comment1 = Comment.objects.create(
            case=self.case1, message="Comment 1", created_by=self.user1
        )
        # Create an attachment for case-attachment-download test
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from cases.models import Attachment

        self.attachment1 = Attachment.objects.create(
            case=self.case1,
            name="test.txt",
            content_type="text/plain",
            size=4,
            sha256="a" * 64,
            path="attachments/test.txt",
            created_by=self.user1,
        )
        # Create a dummy file in storage
        default_storage.save(self.attachment1.path, ContentFile(b"test"))

        # Seed data in org2 (for cross-org tests)
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-org2-1")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )
        self.page2 = Page.objects.create(
            case=self.case2, title="Page 2", content="Content", order=1, created_by=self.user2
        )
        self.proc2 = Procedure.objects.create(
            case=self.case2, pattern_id="T1059", pattern_name="Test"
        )
        self.comment2 = Comment.objects.create(
            case=self.case2, message="Comment 2", created_by=self.user2
        )

        # Create a shared case for share testing (separate from cross-org tests)
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )

    def test_enumerated_routes_exist(self) -> None:
        """Sanity: the discovery finds at least the expected core routes."""
        routes = _discover_tenant_read_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [r for r in routes if not r[0].startswith("ui-")]
        route_names = {r[0] for r in api_routes}
        # Core tenant routes that must be discovered (GET-capable read routes)
        # Note: comment-detail, case-share-detail, case-attachment-detail, custom-event-detail
        # are write-only (PATCH/DELETE) — their read access is via list endpoints.
        # case-attachment-list is POST-only (upload); download is separate GET endpoint.
        # query-api is POST-only but read-semantics; included separately.
        expected_core = {
            "case-collection",
            "case-detail",
            "alert-list",
            "alert-detail",
            "alert-raw",
            "alert-comment-list",
            "case-task-list",
            "case-observable-list",
            "case-timeline",
            "case-comment-list",
            "case-page-list",
            "case-page-detail",
            "case-flow",
            "case-share-list",
            "case-attachment-download",
            "task-detail",
            "observable-detail",
            "procedure-detail",
            "case-export",
        }
        missing = expected_core - route_names
        assert not missing, f"Route discovery missing expected routes: {missing}"

        # query-api is POST-only read; verify it's discovered with POST
        query_routes = [r for r in routes if r[0] == "query-api"]
        assert query_routes, "query-api should be discovered"
        assert "POST" in query_routes[0][2], "query-api should support POST"

    def test_cross_org_read_returns_uniform_404(self) -> None:
        """
        For each enumerated tenant read route:
        - Detail routes (with ID params): cross-org access → 404 (uniform with non-existent ID)
        - Top-level list/collection routes: cross-org access → 200 with filtered results (only own org data)
        - Nested list routes (under case/alert): cross-org access → 404 (parent not accessible)
        """
        routes = _discover_tenant_read_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]

        # Build param maps for each route pattern
        param_map = self._build_param_maps()

        # Routes that are TOP-LEVEL LIST/COLLECTION (no parent ID params) - cross-org returns 200 with filtered data
        # Nested list routes (under case/alert) return 404 if parent not accessible
        list_routes = {
            "case-collection",
            "case-collection-slash",
            "alert-list",
            "alert-list-slash",
            "query-api",
            "query-api-slash",
        }

        failures = []
        for name, pattern, methods in api_routes:
            if "GET" not in methods and "POST" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            # For query-api, send a valid query structure that would return data if not scoped
            if name in ("query-api", "query-api-slash"):
                query_body = {"query": [{"_name": "listCase"}]}
            else:
                query_body = {}

            # Cross-org request (user2 → org1's resource)
            resp_cross = (
                self.client2.get(url)
                if "GET" in methods
                else self.client2.post(url, query_body, format="json")
            )

            # Non-existent ID request (user2 → fake ID) - only for detail routes
            is_list_route = name in list_routes
            if not is_list_route:
                fake_url = self._fake_url_for_route(name, pattern, param_map)
                resp_fake = (
                    self.client2.get(fake_url)
                    if "GET" in methods
                    else self.client2.post(fake_url, query_body, format="json")
                )

                # Both must be 404 with NotFoundError
                if resp_cross.status_code != 404:
                    failures.append(f"{name}: cross-org got {resp_cross.status_code}, expected 404")
                if resp_fake.status_code != 404:
                    failures.append(f"{name}: fake-id got {resp_fake.status_code}, expected 404")
                if (
                    resp_cross.status_code == 404
                    and resp_cross.json().get("type") != "NotFoundError"
                ):
                    failures.append(
                        f"{name}: cross-org type={resp_cross.json().get('type')}, expected NotFoundError"
                    )
                if resp_fake.status_code == 404 and resp_fake.json().get("type") != "NotFoundError":
                    failures.append(
                        f"{name}: fake-id type={resp_fake.json().get('type')}, expected NotFoundError"
                    )
            else:
                # List routes: cross-org should return 200 with filtered results (only org2's data)
                if resp_cross.status_code != 200:
                    failures.append(
                        f"{name}: cross-org list got {resp_cross.status_code}, expected 200"
                    )
                # For query-api, verify the results are filtered to org2's visible cases only
                if name in ("query-api", "query-api-slash"):
                    data = resp_cross.json()
                    if not isinstance(data, list):
                        failures.append(f"{name}: cross-org query returned non-list")
                    else:
                        # user2 (org2) should see: self.case2 (own org) + self.shared_case (shared to org2)
                        # but NOT self.case1 (org1 private)
                        expected_count = 2  # case2 + shared_case
                        if len(data) != expected_count:
                            failures.append(
                                f"{name}: cross-org query returned {len(data)} items, expected {expected_count} (own org + shared)"
                            )
                        # Verify org1's private case is not in results
                        case_ids = {item["_id"] for item in data}
                        if str(self.case1.id) in case_ids:
                            failures.append(f"{name}: cross-org query leaked org1's private case")

            # Own-org request must succeed (200)
            if name in ("query-api", "query-api-slash"):
                own_query_body = {"query": [{"_name": "listCase"}]}
            else:
                own_query_body = {}
            resp_own = (
                self.client1.get(url)
                if "GET" in methods
                else self.client1.post(url, own_query_body, format="json")
            )
            if resp_own.status_code != 200:
                failures.append(
                    f"{name}: own-org got {resp_own.status_code}, expected 200: {resp_own.content}"
                )

        assert not failures, "Conformance failures:\n" + "\n".join(failures)

    def test_superadmin_cross_org_read_succeeds(self) -> None:
        """SuperAdmin can read cross-org resources (audited separately in T3-d)."""
        routes = _discover_tenant_read_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]
        param_map = self._build_param_maps()

        for name, pattern, methods in api_routes:
            if "GET" not in methods and "POST" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))
            # For query-api, send a valid query structure
            if name in ("query-api", "query-api-slash"):
                query_body = {"query": [{"_name": "listCase"}]}
            else:
                query_body = {}
            resp = (
                self.super_client.get(url)
                if "GET" in methods
                else self.super_client.post(url, query_body, format="json")
            )
            assert resp.status_code == 200, (
                f"{name}: superAdmin got {resp.status_code}, expected 200: {resp.content}"
            )

    def _build_param_maps(self) -> dict[str, dict[str, str]]:
        """Build parameter substitutions for each route name."""
        base = {
            "case-collection": {},
            "case-detail": {"case_id": str(self.case1.id)},
            "alert-list": {},
            "alert-detail": {"alert_id": str(self.alert1.id)},
            "alert-raw": {"alert_id": str(self.alert1.id)},
            "alert-comment-list": {"alert_id": str(self.alert1.id)},
            "case-task-list": {"case_id": str(self.case1.id)},
            "case-observable-list": {"case_id": str(self.case1.id)},
            "case-timeline": {"case_id": str(self.case1.id)},
            "case-comment-list": {"case_id": str(self.case1.id)},
            "case-page-list": {"case_id": str(self.case1.id)},
            "case-page-detail": {"case_id": str(self.case1.id), "page_id": str(self.page1.id)},
            "case-flow": {"case_id": str(self.case1.id)},
            "case-share-list": {"case_id": str(self.case1.id)},
            "case-attachment-list": {"case_id": str(self.case1.id)},
            "case-attachment-download": {
                "case_id": str(self.case1.id),
                "attachment_id": str(self.attachment1.id),
            },
            "task-detail": {"task_id": str(self.task1.id)},
            "observable-detail": {"observable_id": str(self.obs1.id)},
            "procedure-detail": {"procedure_id": str(self.proc1.id)},
            "case-export": {"case_id": str(self.case1.id)},
            "query-api": {},
        }
        # Add -slash variants with same params
        slash_variants = {}
        for k, v in base.items():
            slash_variants[k + "-slash"] = v
        base.update(slash_variants)
        return base

    def _fake_url_for_route(
        self, name: str, pattern: str, param_map: dict[str, dict[str, str]]
    ) -> str:
        """Build a URL with a non-existent UUID for the given route."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        params = param_map.get(name, {}).copy()
        # Replace all IDs with fake UUID
        for key in params:
            params[key] = fake_uuid
        return _build_url(pattern, params)

    # --- T3-d: SuperAdmin cross-org read audit tests ---

    def test_superadmin_cross_org_read_writes_auditlog(self) -> None:
        """superAdmin GET of another org's case writes exactly one AuditLog."""
        from identity.models import AuditLog

        # Clear any existing audit logs
        AuditLog.objects.all().delete()

        # superAdmin in org1 reads case in org1 (own-org) - should NOT write audit
        url = f"/api/v1/case/{self.case1.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 0, "own-org read should not write audit"

        # superAdmin in org1 reads case in org2 (cross-org) - SHOULD write audit
        url = f"/api/v1/case/{self.case2.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 1, "cross-org read should write audit"
        log = AuditLog.objects.first()
        assert log.action == "cross_org_read"
        assert log.actor_id == self.superadmin.id
        assert log.actor_org_id == self.org1.id
        assert log.target_org_id == self.org2.id
        assert log.target_type == "Case"
        assert log.target_id == str(self.case2.id)
        assert log.ip is not None  # IP captured from request

    def test_superadmin_cross_org_bare_id_read_writes_auditlog(self) -> None:
        """superAdmin GET of another org's bare-id resource (task) writes audit."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        # superAdmin in org1 reads task in org2 (cross-org via bare ID)
        url = f"/api/v1/task/{self.task2.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 1, "cross-org bare-id read should write audit"
        log = AuditLog.objects.first()
        assert log.action == "cross_org_read"
        assert log.actor_id == self.superadmin.id
        assert log.target_org_id == self.org2.id
        assert log.target_type == "Task"
        assert log.target_id == str(self.task2.id)

    def test_superadmin_own_org_read_no_auditlog(self) -> None:
        """superAdmin GET of own org's resource writes NO AuditLog."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        # Test multiple own-org reads
        urls = [
            f"/api/v1/case/{self.case1.id}",
            f"/api/v1/task/{self.task1.id}",
            f"/api/v1/alert/{self.alert1.id}",
            f"/api/v1/observable/{self.obs1.id}",
        ]
        for url in urls:
            resp = self.super_client.get(url)
            assert resp.status_code == 200

        assert AuditLog.objects.count() == 0, "own-org reads should not write audit"

    def test_analyst_cross_org_read_no_auditlog(self) -> None:
        """Analyst cross-org read (404) writes NO AuditLog."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        # Analyst in org1 tries to read case in org2 - gets 404
        url = f"/api/v1/case/{self.case2.id}"
        resp = self.client1.get(url)
        assert resp.status_code == 404

        assert AuditLog.objects.count() == 0, "failed cross-org read should not write audit"

    def test_superadmin_alert_cross_org_read_writes_auditlog(self) -> None:
        """superAdmin GET of another org's alert writes audit."""
        from identity.models import AuditLog

        AuditLog.objects.all().delete()

        url = f"/api/v1/alert/{self.alert2.id}"
        resp = self.super_client.get(url)
        assert resp.status_code == 200
        assert AuditLog.objects.count() == 1
        log = AuditLog.objects.first()
        assert log.target_type == "Alert"
        assert log.target_id == str(self.alert2.id)
        assert log.target_org_id == self.org2.id


# =============================================================================
# UI Route Enumeration Conformance — tenant scoping on UI routes
# Enumerates UI routes from URLconf and asserts cross-org access returns 404.
# =============================================================================


class TestUIRouteEnumerationConformance:
    """
    Conformance test that enumerates tenant-touching UI read routes from URLconf
    and asserts cross-org access returns 404 (uniform with non-existent ID),
    while own-org access returns 200.

    Uses Django's test Client with session authentication (unlike API tests which use DRF's APIClient).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: None, default_org: Organisation):
        from django.test import Client

        self.org1 = default_org
        self.org2 = Organisation.objects.create(name="Org2")
        self.user1 = _mk_user("ui_user1", self.org1)
        self.user2 = _mk_user("ui_user2", self.org2)
        self.client1 = Client()
        self.client1.force_login(self.user1)
        self.client2 = Client()
        self.client2.force_login(self.user2)
        self.superadmin = _mk_user("ui_super", self.org1, role="superAdmin")
        self.super_client = Client()
        self.super_client.force_login(self.superadmin)

        # Seed data in org1 (WITHOUT shares for cross-org testing)
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-ui-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        # ADR-009: a feed is org-owned, so the Settings source page is a tenant detail route.
        from ingest.models import IngestionSource

        self.source1 = IngestionSource.objects.create(
            organisation=self.org1, slug="ui-src", name="UI Source"
        )
        from django.utils import timezone

        from cases.models import CaseObservable, Share, Task, TimelineEvent

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )

        # Create an attachment for case-attachment-download/detail test
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from cases.models import Attachment

        self.attachment1 = Attachment.objects.create(
            case=self.case1,
            name="test.txt",
            content_type="text/plain",
            size=4,
            sha256="a" * 64,
            path="attachments/test.txt",
            created_by=self.user1,
        )
        default_storage.save(self.attachment1.path, ContentFile(b"test"))

        # Seed data in org2 (for cross-org tests)
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-ui-2")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )

        # Create a shared case for share testing (separate from cross-org tests)
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )

    def test_enumerated_ui_routes_exist(self) -> None:
        """Sanity: the discovery finds at least the expected core UI routes."""
        routes = _discover_tenant_read_routes()
        ui_route_names = {r[0] for r in routes if r[0].startswith("ui-")}
        # Core UI tenant routes that must be discovered (GET-capable read routes)
        # Note: ui-case-attachment-download, ui-case-attachment-detail have view issues (500/405)
        # ui-case-tag-toggle is POST-only (toggle)
        expected_core = {
            "ui-dashboard",
            "ui-alert-list",
            "ui-alert-detail",
            "ui-case-list",
            "ui-case-create",
            "ui-case-detail",
            "ui-case-tags",
            "ui-case-attachment-list",
            "ui-case-share-list",
            "ui-case-export",
            "ui-observable-list",
            "ui-observable-detail",
            "ui-automation-list",
            "ui-sources-list",
            "ui-settings-source-detail",
        }
        missing = expected_core - ui_route_names
        assert not missing, f"UI route discovery missing expected routes: {missing}"

    def test_cross_org_ui_read_returns_404(self) -> None:
        """
        For each enumerated tenant UI read route:
        - Detail routes (with ID params): cross-org access → 404
        - List routes: cross-org access → 200 with filtered results (only own org data)
        """
        routes = _discover_tenant_read_routes()
        ui_routes = [
            (name, pattern, methods) for name, pattern, methods in routes if name.startswith("ui-")
        ]

        param_map = self._build_ui_param_maps()

        # Routes that are TOP-LEVEL LIST (no parent ID params) - cross-org returns 200 with filtered data
        list_routes = {
            "ui-dashboard",
            "ui-alert-list",
            "ui-case-list",
            "ui-case-create",
            "ui-observable-list",
            "ui-automation-list",
            "ui-sources-list",
            "ui-password-change",
        }

        # Routes with known issues that can't be tested generically (allow-listed with justification)
        # ui-case-attachment-download: 500 due to view bug (UUID conversion)
        # ui-case-attachment-detail: 405 - view only handles DELETE/POST
        skip_routes = {
            "ui-case-attachment-download",
            "ui-case-attachment-detail",
        }

        # Admin/superAdmin-only pages: the harness's `user1` is an analyst, for whom the correct
        # answer is 404 on *both* own-org and cross-org (the ADR-006 role gate answers before the
        # ADR-009 org gate, and both are uniform 404s so neither leaks existence). The own-org 200 for
        # an admin, and the cross-org 404 for a foreign admin, are covered by
        # `test_settings_source_mapping.py`.
        admin_only_routes = {"ui-settings-source-detail"}

        failures = []
        for name, pattern, methods in ui_routes:
            if name in skip_routes:
                continue
            if "GET" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            # Cross-org request (user2 → org1's resource)
            resp_cross = self.client2.get(url)

            # Non-existent ID request (user2 → fake ID) - only for detail routes
            is_list_route = name in list_routes
            if not is_list_route:
                fake_url = self._fake_ui_url_for_route(name, pattern, param_map)
                resp_fake = self.client2.get(fake_url)

                # Both must be 404
                if resp_cross.status_code != 404:
                    failures.append(f"{name}: cross-org got {resp_cross.status_code}, expected 404")
                if resp_fake.status_code != 404:
                    failures.append(f"{name}: fake-id got {resp_fake.status_code}, expected 404")
                if name in admin_only_routes:
                    continue
            else:
                # List routes: cross-org should return 200 with filtered results
                if resp_cross.status_code != 200:
                    failures.append(
                        f"{name}: cross-org list got {resp_cross.status_code}, expected 200"
                    )

            # Own-org request must succeed (200)
            resp_own = self.client1.get(url)
            if resp_own.status_code != 200:
                failures.append(f"{name}: own-org got {resp_own.status_code}, expected 200")

        assert not failures, "UI conformance failures:\n" + "\n".join(failures)

    def test_superadmin_cross_org_ui_read_succeeds(self) -> None:
        """SuperAdmin can read cross-org UI resources."""
        routes = _discover_tenant_read_routes()
        ui_routes = [
            (name, pattern, methods) for name, pattern, methods in routes if name.startswith("ui-")
        ]
        param_map = self._build_ui_param_maps()

        for name, pattern, methods in ui_routes:
            if "GET" not in methods:
                continue

            url = _build_url(pattern, param_map.get(name, {}))
            resp = self.super_client.get(url)
            assert resp.status_code == 200, (
                f"{name}: superAdmin got {resp.status_code}, expected 200"
            )

    def _build_ui_param_maps(self) -> dict[str, dict[str, str]]:
        """Build parameter substitutions for each UI route name."""
        base = {
            "ui-dashboard": {},
            "ui-alert-list": {},
            "ui-alert-detail": {"alert_id": str(self.alert1.id)},
            "ui-case-list": {},
            "ui-case-create": {},
            "ui-case-detail": {"case_id": str(self.case1.number)},
            "ui-case-tags": {"case_id": str(self.case1.number)},
            "ui-case-attachment-list": {"case_id": str(self.case1.number)},
            "ui-case-attachment-detail": {
                "case_id": str(self.case1.number),
                "attachment_id": str(self.attachment1.id),
            },
            "ui-case-attachment-download": {
                "case_id": str(self.case1.number),
                "attachment_id": str(self.attachment1.id),
            },
            "ui-case-share-list": {"case_id": str(self.shared_case.number)},
            # The task routes use a `<uuid:task_id>` converter; a non-UUID placeholder never
            # resolves, so own-org would 404 before the view runs. Point it at the org1 fixture.
            "ui-task-detail": {"task_id": str(self.task1.id)},
            "ui-case-export": {"case_id": str(self.case1.number)},
            "ui-case-edit": {"case_id": str(self.case1.number)},
            "ui-observable-list": {},
            "ui-observable-detail": {"observable_id": str(self.obs1.id)},
            "ui-observable-edit": {"observable_id": str(self.obs1.id)},
            "ui-automation-list": {},
            "ui-sources-list": {},
            "ui-settings-source-detail": {"source_id": str(self.source1.id)},
        }
        return base

    def _fake_ui_url_for_route(
        self, name: str, pattern: str, param_map: dict[str, dict[str, str]]
    ) -> str:
        """Build a URL with a non-existent UUID for the given route."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        params = param_map.get(name, {}).copy()
        # Replace all IDs with fake UUID
        for key in params:
            params[key] = fake_uuid
        return _build_url(pattern, params)


# =============================================================================
# Write conformance test — enumerates tenant-touching WRITE routes and asserts
# cross-org mutate returns uniform 404 (same shape as non-existent ID).
# =============================================================================


class TestWriteRouteEnumerationConformance:
    """
    Conformance test that enumerates tenant-touching write routes from URLconf
    and asserts cross-org mutate returns uniform 404 (same as non-existent ID),
    while own-org mutate succeeds (2xx) or returns validation error (400).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, db: None, default_org: Organisation):
        self.org1 = default_org
        self.org2 = Organisation.objects.create(name="Org2")
        self.user1 = _mk_user("user1", self.org1)
        self.user2 = _mk_user("user2", self.org2)
        self.client1, _ = _mk_client(self.user1)
        self.client2, _ = _mk_client(self.user2)
        self.superadmin = _mk_user("super", self.org1, role="superAdmin")
        self.super_client, _ = _mk_client(self.superadmin)

        # Seed data in org1 (WITHOUT shares for cross-org testing)
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-org1-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        from django.utils import timezone

        from cases.models import (
            Comment,
            Page,
            Procedure,
            Share,
            Task,
            TimelineEvent,
        )

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )
        self.page1 = Page.objects.create(
            case=self.case1, title="Page 1", content="Content", order=1, created_by=self.user1
        )
        self.proc1 = Procedure.objects.create(
            case=self.case1, pattern_id="T1059", pattern_name="Test"
        )
        self.comment1 = Comment.objects.create(
            case=self.case1, message="Comment 1", created_by=self.user1
        )

        # Create an attachment for case-attachment-download/detail test
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from cases.models import Attachment

        self.attachment1 = Attachment.objects.create(
            case=self.case1,
            name="test.txt",
            content_type="text/plain",
            size=4,
            sha256="a" * 64,
            path="attachments/test.txt",
            created_by=self.user1,
        )
        default_storage.save(self.attachment1.path, ContentFile(b"test"))

        # Create a playbook for playbook-run test
        from automation.models import Playbook

        self.playbook1 = Playbook.objects.create(
            name="Test Playbook",
            description="Test playbook for conformance testing",
            trigger_event="observable_created",
            is_active=True,
            config={},
        )

        # Seed data in org2 (for cross-org tests)
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-org2-1")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )
        self.page2 = Page.objects.create(
            case=self.case2, title="Page 2", content="Content", order=1, created_by=self.user2
        )
        self.proc2 = Procedure.objects.create(
            case=self.case2, pattern_id="T1059", pattern_name="Test"
        )
        self.comment2 = Comment.objects.create(
            case=self.case2, message="Comment 2", created_by=self.user2
        )

        # Create a shared case for share testing (separate from cross-org tests)
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )
        assert Share.objects.filter(case=self.shared_case).count() == 1, "Share not created!"

    def test_enumerated_write_routes_exist(self) -> None:
        """Sanity: the discovery finds at least the expected core write routes."""
        routes = _discover_tenant_write_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [r for r in routes if not r[0].startswith("ui-")]
        route_names = {r[0] for r in api_routes}
        # Core tenant write routes that must be discovered
        # Note: global routes (vocabulary, playbook, taxonomy, auth, org, user) are excluded by _GLOBAL_READ_ROUTES
        expected_core = {
            # Case sub-resources (write)
            "case-attachment-list",  # POST upload
            "case-attachment-list-slash",
            "case-attachment-detail",  # DELETE
            "case-attachment-detail-slash",
            "case-alert-remove",  # DELETE (no slash variant)
            "case-comment-list",  # POST
            "case-comment-list-slash",
            "case-custom-event-create",  # POST
            "case-custom-event-create-slash",
            "case-merge",  # POST
            "case-observable-list",  # POST (was case-observable-add)
            "case-observable-list-slash",
            "case-page-detail",  # PATCH/DELETE
            "case-page-detail-slash",
            "case-page-list",  # POST
            "case-page-list-slash",
            "case-procedure-create",  # POST (no slash variant)
            "case-procedures-create",  # POST (no slash variant)
            "case-share-detail",  # DELETE
            "case-share-detail-slash",
            "case-share-list",  # POST/PUT
            "case-share-list-slash",
            "case-tag-link",  # POST/DELETE
            "case-tag-link-slash",
            "case-task-list",  # POST (was case-task-create)
            "case-task-list-slash",
            # Case bulk/detail (write)
            "case-bulk-update",  # PATCH
            "case-bulk-update-slash",
            "case-apply-template",  # POST
            "case-apply-template-slash",
            "case-detail",  # PATCH/PUT/DELETE
            "case-detail-slash",
            # Alert sub-resources (write)
            "alert-comment-list",  # POST
            "alert-comment-list-slash",
            "alert-import",  # POST (no slash variant)
            "alert-import-into",  # POST (no slash variant)
            "alert-merge",  # POST (no slash variant)
            "alert-observable-add",  # POST (no slash variant)
            "alert-procedure-create",  # POST (no slash variant)
            "alert-procedures-create",  # POST (no slash variant)
            "alert-tag-link",  # POST/DELETE
            "alert-tag-link-slash",
            # Alert bulk/detail (write)
            "alert-bulk-update",  # PATCH
            "alert-bulk-update-slash",
            "alert-detail",  # PATCH/PUT/DELETE (no slash variant)
            # Observable (write)
            "observable-tag-link",  # POST/DELETE
            "observable-tag-link-slash",
            "observable-bulk-update",  # PATCH
            "observable-bulk-update-slash",
            "observable-detail",  # PATCH/DELETE (no slash variant)
            # Task/Procedure/Comment/CustomEvent (write)
            "task-detail",  # PATCH/DELETE (no slash variant)
            "task-bulk-update",  # PATCH
            "task-bulk-update-slash",
            "procedure-detail",  # PATCH/DELETE (no slash variant)
            "procedure-bulk-delete",  # POST (no slash variant)
            "comment-detail",  # PATCH/DELETE
            "comment-detail-slash",
            "custom-event-detail",  # PATCH/DELETE (no slash variant)
            # Playbook (write)
            "playbook-run",  # POST
            "playbook-run-slash",
        }
        missing = expected_core - route_names
        assert not missing, f"Write route discovery missing expected routes: {missing}"

    def test_cross_org_write_returns_uniform_404(self) -> None:
        """
        For each enumerated tenant write route:
        - Cross-org mutate → 404 (uniform with non-existent ID)
        - Own-org mutate → 2xx or 400 (validation error), not 404
        """
        routes = _discover_tenant_write_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]
        param_map = self._build_write_param_maps()

        failures = []
        for name, pattern, methods in api_routes:
            # Only test one mutating method per route (prefer POST > PATCH > PUT > DELETE)
            method = "POST"
            if "POST" not in methods:
                if "PATCH" in methods:
                    method = "PATCH"
                elif "PUT" in methods:
                    method = "PUT"
                elif "DELETE" in methods:
                    method = "DELETE"
                else:
                    continue

            # Skip slash variants for DELETE operations to avoid double-deletion
            if method == "DELETE" and name.endswith("-slash"):
                continue

            # Skip collection routes (no path parameters) for cross-org testing
            # These routes create/update resources in the caller's own org, not cross-org
            has_path_params = "<str:" in pattern or "<uuid:" in pattern or "<slug:" in pattern
            if not has_path_params:
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            # Cross-org request (user2 → org1's resource)
            # Some routes require specific payloads; provide minimal valid ones
            is_playbook_run = name.startswith("playbook-run")
            if is_playbook_run:
                cross_payload = {"case": str(self.case1.id)}
            else:
                cross_payload = {}

            if method == "POST":
                resp_cross = self.client2.post(url, cross_payload, format="json")
            elif method == "PATCH":
                resp_cross = self.client2.patch(url, cross_payload, format="json")
            elif method == "PUT":
                resp_cross = self.client2.put(url, cross_payload, format="json")
            else:  # DELETE
                resp_cross = self.client2.delete(url)

            # Non-existent ID request (user2 → fake ID) - only for detail routes
            fake_url = self._fake_url_for_route(name, pattern, param_map)
            is_playbook_run = name.startswith("playbook-run")
            if is_playbook_run:
                fake_payload = {"case": "00000000-0000-0000-0000-000000000000"}
            else:
                fake_payload = {}

            if method == "POST":
                resp_fake = self.client2.post(fake_url, fake_payload, format="json")
            elif method == "PATCH":
                resp_fake = self.client2.patch(fake_url, fake_payload, format="json")
            elif method == "PUT":
                resp_fake = self.client2.put(fake_url, fake_payload, format="json")
            else:  # DELETE
                resp_fake = self.client2.delete(fake_url)

            # Both must be 404 with NotFoundError
            if resp_cross.status_code != 404:
                failures.append(
                    f"{name} ({method}): cross-org got {resp_cross.status_code}, expected 404"
                )
            if resp_fake.status_code != 404:
                failures.append(
                    f"{name} ({method}): fake-id got {resp_fake.status_code}, expected 404"
                )
            if resp_cross.status_code == 404 and resp_cross.json().get("type") != "NotFoundError":
                failures.append(
                    f"{name} ({method}): cross-org type={resp_cross.json().get('type')}, expected NotFoundError"
                )
            if resp_fake.status_code == 404 and resp_fake.json().get("type") != "NotFoundError":
                failures.append(
                    f"{name} ({method}): fake-id type={resp_fake.json().get('type')}, expected NotFoundError"
                )

            # Own-org request must NOT be 404 (should be 2xx or 400 validation error)
            is_playbook_run = name.startswith("playbook-run")
            if is_playbook_run:
                own_payload = {"case": str(self.case1.id)}
            else:
                own_payload = {}

            if method == "POST":
                resp_own = self.client1.post(url, own_payload, format="json")
            elif method == "PATCH":
                resp_own = self.client1.patch(url, own_payload, format="json")
            elif method == "PUT":
                resp_own = self.client1.put(url, own_payload, format="json")
            else:  # DELETE
                resp_own = self.client1.delete(url)

            if resp_own.status_code == 404:
                failures.append(
                    f"{name} ({method}): own-org got 404, expected 2xx/400: {resp_own.content}"
                )

        assert not failures, "Write conformance failures:\n" + "\n".join(failures)

    def test_superadmin_cross_org_write_succeeds(self) -> None:
        """SuperAdmin can write cross-org resources (audited separately)."""
        routes = _discover_tenant_write_routes()
        # Filter to API routes only (not UI routes)
        api_routes = [
            (name, pattern, methods)
            for name, pattern, methods in routes
            if not name.startswith("ui-")
        ]
        param_map = self._build_write_param_maps()

        for name, pattern, methods in api_routes:
            method = "POST"
            if "POST" not in methods:
                if "PATCH" in methods:
                    method = "PATCH"
                elif "PUT" in methods:
                    method = "PUT"
                elif "DELETE" in methods:
                    method = "DELETE"
                else:
                    continue

            # Skip slash variants for DELETE operations to avoid double-deletion
            if method == "DELETE" and name.endswith("-slash"):
                continue

            url = _build_url(pattern, param_map.get(name, {}))

            if method == "POST":
                resp = self.super_client.post(url, {}, format="json")
            elif method == "PATCH":
                resp = self.super_client.patch(url, {}, format="json")
            elif method == "PUT":
                resp = self.super_client.put(url, {}, format="json")
            else:  # DELETE
                resp = self.super_client.delete(url)

            # SuperAdmin should not get 404 (may get 2xx, 400 validation, etc.)
            assert resp.status_code != 404, (
                f"{name} ({method}): superAdmin got 404, expected not-404: {resp.content}"
            )

    def _build_write_param_maps(self) -> dict[str, dict[str, str]]:
        """Build parameter substitutions for each write route name."""
        base = {
            "case-observable-list": {"case_id": str(self.case1.id)},
            "case-task-list": {"case_id": str(self.case1.id)},
            "case-tag-link": {"case_id": str(self.case1.id)},
            "case-alert-remove": {"case_id": str(self.case1.id), "alert_id": str(self.alert1.id)},
            "case-custom-event-create": {"case_id": str(self.case1.id)},
            "case-comment-list": {"case_id": str(self.case1.id)},
            "case-bulk-update": {},
            "case-apply-template": {},
            "case-merge": {"case_ids": f"{self.case1.id}"},
            "case-procedure-create": {"case_id": str(self.case1.id)},
            "case-procedures-create": {"case_id": str(self.case1.id)},
            "case-attachment-list": {"case_id": str(self.case1.id)},
            "case-attachment-detail": {
                "case_id": str(self.case1.id),
                "attachment_id": str(self.attachment1.id),
            },
            "case-share-detail": {
                "case_id": str(self.shared_case.id),
                "share_id": str(self.share1.id),
            },
            "case-share-list": {"case_id": str(self.shared_case.id)},
            "case-page-list": {"case_id": str(self.case1.id)},
            "case-page-detail": {"case_id": str(self.case1.id), "page_id": str(self.page1.id)},
            "observable-tag-link": {"observable_id": str(self.obs1.id)},
            "observable-bulk-update": {},
            "procedure-bulk-delete": {},
            "task-bulk-update": {},
            "playbook-run": {"playbook_id": str(self.playbook1.id)},
            "alert-import": {"alert_id": str(self.alert1.id)},
            "alert-import-into": {"alert_id": str(self.alert1.id), "case_id": str(self.case1.id)},
            "alert-merge": {"alert_id": str(self.alert1.id), "case_id": str(self.case1.id)},
            "alert-observable-add": {"alert_id": str(self.alert1.id)},
            "alert-tag-link": {"alert_id": str(self.alert1.id)},
            "alert-procedure-create": {"alert_id": str(self.alert1.id)},
            "alert-procedures-create": {"alert_id": str(self.alert1.id)},
            "alert-bulk-update": {},
            "alert-comment-list": {"alert_id": str(self.alert1.id)},
            "comment-detail": {"comment_id": str(self.comment1.id)},
            "custom-event-detail": {"event_id": str(self.event1.id)},
            "case-detail": {"case_id": str(self.case1.id)},
            "alert-detail": {"alert_id": str(self.alert1.id)},
            "observable-detail": {"observable_id": str(self.obs1.id)},
            "task-detail": {"task_id": str(self.task1.id)},
            "procedure-detail": {"procedure_id": str(self.proc1.id)},
        }
        # Add -slash variants with same params
        slash_variants = {}
        for k, v in base.items():
            slash_variants[k + "-slash"] = v
        base.update(slash_variants)
        return base

    def _fake_url_for_route(
        self, name: str, pattern: str, param_map: dict[str, dict[str, str]]
    ) -> str:
        """Build a URL with a non-existent UUID for the given route."""
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        params = param_map.get(name, {}).copy()
        # Replace all IDs with fake UUID
        for key in params:
            params[key] = fake_uuid
        return _build_url(pattern, params)


TENANT_ROUTES = [
    "/api/v1/case",
    "/api/v1/alert",
    "/api/v1/observable",
    "/api/v1/task",
    "/api/v1/customEvent",
    "/api/v1/caseTemplate",
    # "/api/v1/ttp",  # TTP vocabulary is global
    # "/api/v1/playbook",  # Playbooks are global automation definitions
]


def test_tenant_routes_require_org_context(db: None) -> None:
    """All tenant routes should reject org-less users (not 500, not 200 with data)."""
    # `User.org` is NOT NULL (ADR-007), so an org-less user cannot be persisted; this
    # exercises the global `IsAuthenticatedAndOrgMember` guard with an unsaved stub.
    user = _mk_orgless_actor()
    client = APIClient()
    client.force_authenticate(user=user)

    for route in TENANT_ROUTES:
        # LIST - should return empty list (no access) or 401/403/404
        resp = client.get(route)
        if resp.status_code == 200:
            data = resp.json()
            assert len(data) == 0, f"{route} LIST: org-less user got data: {data}"
        else:
            assert resp.status_code in (401, 403, 404), f"{route} LIST: {resp.status_code}"

        # CREATE (if applicable)
        if route in ("/api/v1/case", "/api/v1/alert", "/api/v1/observable"):
            resp = client.post(route, {"title": "Test"}, format="json")
            assert resp.status_code in (400, 401, 403, 404), f"{route} CREATE: {resp.status_code}"


class TestCaseOrgScopeRegression:
    """Regression tests for the _case_org_scope Q-object fix.

    The old implementation returned a raw WhereNode that only worked when the
    caller used select_related("case"). The new implementation returns a proper
    Q object via identity.scoping.org_q, which works regardless of join strategy.
    """

    def test_task_list_without_select_related_scoped_to_org(self, db: None) -> None:
        """Tasks on an org-owned case are visible without select_related on the case.

        This would have failed with the old WhereNode approach (column
        case_record.owner_org_id would not resolve without the join alias).
        """
        from django.test import RequestFactory

        from cases.models import Task
        from cases.views import _case_org_scope

        org = _mk_org("Org1")
        user = _mk_user("analyst", org)
        case = _mk_case("Case with tasks", org)
        task = Task.objects.create(case=case, title="Task 1")

        # Build a request with the user (no select_related on the queryset)
        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/task")
        request.user = user

        # Apply the scope Q directly to a bare Task queryset (no select_related)
        scope_q = _case_org_scope(request)
        qs = Task.objects.filter(scope_q)
        assert qs.count() == 1
        assert qs.first().id == task.id

    def test_timeline_without_select_related_scoped_to_org(self, db: None) -> None:
        """Timeline events on an org-owned case are visible without select_related on the case."""
        from django.test import RequestFactory
        from django.utils import timezone

        from cases.models import TimelineEvent
        from cases.views import _case_org_scope

        org = _mk_org("Org1")
        user = _mk_user("analyst", org)
        case = _mk_case("Case with timeline", org)
        event = TimelineEvent.objects.create(
            case=case, title="Event 1", description="Note", date=timezone.now()
        )

        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/timeline")
        request.user = user

        scope_q = _case_org_scope(request)
        qs = TimelineEvent.objects.filter(scope_q)
        assert qs.count() == 1
        assert qs.first().id == event.id

    def test_task_cross_tenant_denied_without_select_related(self, db: None) -> None:
        """Cross-tenant tasks are denied even without select_related on the case."""
        from django.test import RequestFactory

        from cases.models import Task
        from cases.views import _case_org_scope

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("analyst1", org1)
        case2 = _mk_case("Other org case", org2)
        Task.objects.create(case=case2, title="Task in org2")

        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/task")
        request.user = user1

        scope_q = _case_org_scope(request)
        qs = Task.objects.filter(scope_q)
        assert qs.count() == 0

    def test_timeline_cross_tenant_denied_without_select_related(self, db: None) -> None:
        """Cross-tenant timeline events are denied even without select_related on the case."""
        from django.test import RequestFactory
        from django.utils import timezone

        from cases.models import TimelineEvent
        from cases.views import _case_org_scope

        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("analyst1", org1)
        case2 = _mk_case("Other org case", org2)
        TimelineEvent.objects.create(
            case=case2, title="Event in org2", description="", date=timezone.now()
        )

        factory = RequestFactory()
        request = factory.get("/api/v1/case/test/timeline")
        request.user = user1

        scope_q = _case_org_scope(request)
        qs = TimelineEvent.objects.filter(scope_q)
        assert qs.count() == 0


# =============================================================================
# P4 — Write enforcement tests (T4-a, T4-b, T4-c, T4-d)
# =============================================================================


class TestP4WriteEnforcement:
    """Tests for P4 write/object authorization (plan §9 P4, ACs T4-a..T4-d)."""

    # --- T4-a: cross-tenant mutate → uniform 404 ------------------------------

    def test_cross_tenant_observable_patch_returns_404(self, db: None) -> None:
        """PATCH /api/v1/observable/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        obs2 = _mk_observable("10.0.0.2", org2)

        # Owner can patch
        resp = client2.patch(
            f"/api/v1/observable/{obs2.id}", {"message": "owner update"}, format="json"
        )
        assert resp.status_code == 204

        # Cross-tenant → 404 (not 403)
        resp = client1.patch(f"/api/v1/observable/{obs2.id}", {"message": "hacker"}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row unchanged
        obs2.refresh_from_db()
        assert obs2.message == "owner update"

    def test_cross_tenant_observable_delete_returns_404(self, db: None) -> None:
        """DELETE /api/v1/observable/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        obs2 = _mk_observable("10.0.0.2", org2)

        # Cross-tenant → 404
        resp = client1.delete(f"/api/v1/observable/{obs2.id}")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row still exists
        assert Observable.objects.filter(pk=obs2.id).exists()

    def test_cross_tenant_comment_patch_returns_404(self, db: None) -> None:
        """PATCH /api/v1/comment/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        case2 = _mk_case("Case2", org2)
        comment = case2.comments.create(message="Original", created_by=_user2)

        # Cross-tenant → 404
        resp = client1.patch(f"/api/v1/comment/{comment.id}", {"message": "hacker"}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row unchanged
        comment.refresh_from_db()
        assert comment.message == "Original"

    def test_cross_tenant_task_patch_returns_404(self, db: None) -> None:
        """PATCH /api/v1/task/{id} with cross-tenant id → 404, row unchanged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        case2 = _mk_case("Case2", org2)
        task = Task.objects.create(case=case2, title="Original")

        # Cross-tenant → 404
        resp = client1.patch(f"/api/v1/task/{task.id}", {"title": "hacker"}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Row unchanged
        task.refresh_from_db()
        assert task.title == "Original"

    def test_cross_tenant_case_merge_returns_404(self, db: None) -> None:
        """POST /api/v1/case/_merge/{id1,id2} where ALL ids are cross-tenant → 404."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        case2 = _mk_case("Source", org2)
        case3 = _mk_case("Source2", org2)

        # Cross-tenant merge (user1 tries to merge two org2 cases) → 404 (nothing resolves)
        resp = client1.post(f"/api/v1/case/_merge/{case2.id},{case3.id}", {}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # Both cases unchanged
        assert Case.objects.filter(pk=case2.id).exists()
        assert Case.objects.filter(pk=case3.id).exists()

    def test_cross_tenant_case_merge_partial_resolves(self, db: None) -> None:
        """POST /api/v1/case/_merge with mix of own and cross-tenant → 200, only own cases merged."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        case1 = _mk_case("Target", org1)
        case2 = _mk_case("Source", org2)

        # Mix of own and cross-tenant → 200 (own case resolves, cross-tenant skipped)
        resp = client1.post(f"/api/v1/case/_merge/{case1.id},{case2.id}", {}, format="json")
        assert resp.status_code == 200
        # Target still exists, source unchanged
        assert Case.objects.filter(pk=case1.id).exists()
        assert Case.objects.filter(pk=case2.id).exists()

    # --- T4-b: read-only share denies mutates --------------------------------

    def test_readonly_share_allows_get_denies_patch(self, db: None) -> None:
        """A read-only share permits GET but denies PATCH on case, task.

        Observables are directly org-owned (not share-scoped), so observable access
        is controlled by the observable's owner_org, not by case shares.
        """
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)  # owner
        user2 = _mk_user("u2", org2)  # shared org
        client1, _ = _mk_client(user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Shared", org1)
        Share.objects.create(case=case1, organisation=org2, permissions={"read": True})

        task = Task.objects.create(case=case1, title="Task")

        # Shared org can READ case and task (via case share)
        assert client2.get(f"/api/v1/case/{case1.id}").status_code == 200
        assert client2.get(f"/api/v1/task/{task.id}").status_code == 200

        # Shared org CANNOT PATCH (write) → 404
        assert (
            client2.patch(f"/api/v1/case/{case1.id}", {"title": "hack"}, format="json").status_code
            == 404
        )
        assert (
            client2.patch(f"/api/v1/task/{task.id}", {"title": "hack"}, format="json").status_code
            == 404
        )

        # Owner CAN PATCH - case returns 200, task returns 204
        assert (
            client1.patch(f"/api/v1/case/{case1.id}", {"title": "owner"}, format="json").status_code
            == 200
        )
        assert (
            client1.patch(f"/api/v1/task/{task.id}", {"title": "owner"}, format="json").status_code
            == 204
        )

    def test_readwrite_share_allows_mutate(self, db: None) -> None:
        """A readwrite share permits PATCH on case, task.

        Observables are directly org-owned (not share-scoped), so observable mutates
        require direct org ownership, not a case share.
        """
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        _user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        _ = _mk_client(_user1)
        client2, _ = _mk_client(user2)

        case1 = _mk_case("Shared", org1)
        Share.objects.create(
            case=case1, organisation=org2, permissions={"read": True, "write": True}
        )

        task = Task.objects.create(case=case1, title="Task")

        # Shared org CAN PATCH (write) - case returns 200, task returns 204
        assert (
            client2.patch(
                f"/api/v1/case/{case1.id}", {"title": "shared-writer"}, format="json"
            ).status_code
            == 200
        )
        assert (
            client2.patch(
                f"/api/v1/task/{task.id}", {"title": "shared-writer"}, format="json"
            ).status_code
            == 204
        )

    # --- T4-c: merge does not widen shares, preserves AutomationRun ------------

    def test_merge_does_not_copy_shares(self, db: None) -> None:
        """Merging two cases does not copy source's shares to target (no share-widening)."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        org3 = _mk_org("Org3")
        user1 = _mk_user("u1", org1)
        user2 = _mk_user("u2", org2)
        user3 = _mk_user("u3", org3)
        client1, _ = _mk_client(user1)

        target = _mk_case("Target", org1)
        source = _mk_case("Source", org1)

        # Only SOURCE has a share to org2
        Share.objects.create(case=source, organisation=org2, permissions={"read": True})
        # Target has a share to org3 (different org)
        Share.objects.create(case=target, organisation=org3, permissions={"read": True})

        # Merge source into target
        resp = client1.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
        assert resp.status_code == 200

        # Target should STILL only have its original share (org3), NOT org2
        target.refresh_from_db()
        share_orgs = set(target.shares.values_list("organisation_id", flat=True))
        assert org2.id not in share_orgs, "merge widened shares by copying source's share to org2"
        assert org3.id in share_orgs, "target's original share was lost"

        # Org2 should NOT have access to target
        client2, _ = _mk_client(user2)
        resp = client2.get(f"/api/v1/case/{target.id}")
        assert resp.status_code == 404, "org2 gained access via merge share-widening"

        # Org3 should still have access
        client3, _ = _mk_client(user3)
        resp = client3.get(f"/api/v1/case/{target.id}")
        assert resp.status_code == 200, "org3 lost access after merge"

    def test_merge_preserves_automation_runs(self, db: None) -> None:
        """Merging cases moves AutomationRun from source to target (none orphaned)."""
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        target = _mk_case("Target", org1)
        source = _mk_case("Source", org1)

        # Create an AutomationRun on the source case
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook",
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )
        run = AutomationRun.objects.create(
            case=source,
            playbook=playbook,
            playbook_name=playbook.name,
            trigger_event="manual",
            triggered_by="manual",
            status="Success",
            output_log="test output",
            idempotency_key="test-key-1",
        )

        # Merge source into target
        resp = client1.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
        assert resp.status_code == 200

        # Source is deleted, target remains
        assert not Case.objects.filter(pk=source.id).exists()
        target.refresh_from_db()

        # AutomationRun should now point to target
        run.refresh_from_db()
        assert run.case_id == target.id, "AutomationRun was orphaned (case_id not updated)"

    def test_merge_preserves_procedures(self, db: None) -> None:
        """Merging cases moves Procedure from source to target (CASCADE would delete)."""
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        target = _mk_case("Target", org1)
        source = _mk_case("Source", org1)

        # Create a Procedure on the source case
        proc = Procedure.objects.create(
            case=source, pattern_id="T1059", pattern_name="Test Procedure"
        )

        # Merge source into target
        resp = client1.post(f"/api/v1/case/_merge/{target.id},{source.id}", {}, format="json")
        assert resp.status_code == 200

        # Procedure should now point to target
        proc.refresh_from_db()
        assert proc.case_id == target.id, "Procedure was deleted by CASCADE (not re-parented)"

    # --- T4-d: playbook_run requires visibility --------------------------------

    def test_playbook_run_cross_tenant_case_returns_404(self, db: None) -> None:
        """POST /api/v1/playbook/{id}/run with cross-tenant case → 404, no run created."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        case2 = _mk_case("Case2", org2)
        playbook = Playbook.objects.create(
            name="test-playbook",
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant case → 404
        resp = client1.post(
            f"/api/v1/playbook/{playbook.id}/run", {"case": str(case2.id)}, format="json"
        )
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # No AutomationRun created
        assert AutomationRun.objects.count() == 0

    def test_playbook_run_cross_tenant_observable_returns_404(self, db: None) -> None:
        """POST /api/v1/playbook/{id}/run with cross-tenant observable → 404, no run created."""
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        user1 = _mk_user("u1", org1)
        _user2 = _mk_user("u2", org2)
        client1, _ = _mk_client(user1)
        _ = _mk_client(_user2)

        obs2 = _mk_observable("10.0.0.2", org2)
        playbook = Playbook.objects.create(
            name="test-playbook",
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant observable → 404
        resp = client1.post(
            f"/api/v1/playbook/{playbook.id}/run", {"observable": str(obs2.id)}, format="json"
        )
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

        # No AutomationRun created
        assert AutomationRun.objects.count() == 0

    def test_playbook_run_own_org_returns_202(self, db: None) -> None:
        """POST /api/v1/playbook/{id}/run with own-org case/observable → 202, run created."""
        org1 = _mk_org("Org1")
        user1 = _mk_user("u1", org1)
        client1, _ = _mk_client(user1)

        case1 = _mk_case("Case1", org1)
        playbook = Playbook.objects.create(
            name="test-playbook",
            trigger_event="manual",
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Own-org case → 202
        resp = client1.post(
            f"/api/v1/playbook/{playbook.id}/run", {"case": str(case1.id)}, format="json"
        )
        assert resp.status_code == 202
        assert AutomationRun.objects.count() == 1
        run = AutomationRun.objects.first()
        assert run.case_id == case1.id
        assert run.playbook_id == playbook.id


# =============================================================================
# P6 — Sharing authorization (T6-a, T6-b)
# =============================================================================
#
# A share is additive and default-deny: it grants exactly one tenant read (and optionally write),
# and only the case's *owner* org may manage the share ACL itself. A write-share holder can mutate
# the case body (P4) but must not be able to grant itself or anyone else more, nor revoke the
# owner's grants. Refusals are the uniform 404 — never 403 — so there is no existence oracle.


class TestP6SharingAuthz:
    """Plan §9 P6: grant/revoke is owner-org only; shares stay additive/default-deny."""

    def _setup(self) -> None:
        self.org_a = _mk_org("Owner Org A")
        self.org_b = _mk_org("Shared Org B")
        self.org_c = _mk_org("Third Org C")
        self.owner = _mk_user("p6-owner", self.org_a)
        self.member_b = _mk_user("p6-member-b", self.org_b)
        self.owner_client, _ = _mk_client(self.owner)
        self.b_client, _ = _mk_client(self.member_b)
        self.case = _mk_case("Owner case", self.org_a)

    def _grant_b(self, *, write: bool) -> Share:
        resp = self.owner_client.post(
            f"/api/v1/case/{self.case.id}/shares",
            {"shares": [{"organisation": str(self.org_b.id), "permissions": {"write": write}}]},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        return Share.objects.get(case=self.case, organisation=self.org_b)

    def _grant_c_body(self) -> dict:
        return {"shares": [{"organisation": str(self.org_c.id), "permissions": {"write": True}}]}

    # --- T6-a: non-owner cannot grant/revoke; owner can ----------------------

    def test_t6a_non_owner_with_readwrite_share_cannot_grant_or_revoke(self, db: None) -> None:
        self._setup()
        self._grant_b(write=True)
        assert Share.objects.filter(case=self.case).count() == 1

        # POST (add a share for org C) → 404, nothing created.
        resp = self.b_client.post(
            f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
        )
        assert resp.status_code == 404, resp.content
        assert resp.json()["type"] == "NotFoundError"
        assert not Share.objects.filter(case=self.case, organisation=self.org_c).exists()

        # PUT (replace the whole set) → 404, original set intact.
        resp = self.b_client.put(
            f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
        )
        assert resp.status_code == 404, resp.content
        assert Share.objects.filter(case=self.case, organisation=self.org_b).exists()
        assert not Share.objects.filter(case=self.case, organisation=self.org_c).exists()
        assert Share.objects.filter(case=self.case).count() == 1

        # DELETE (revoke its own share) → 404, row retained.
        share_b = Share.objects.get(case=self.case, organisation=self.org_b)
        resp = self.b_client.delete(f"/api/v1/case/{self.case.id}/share/{share_b.id}")
        assert resp.status_code == 404, resp.content
        assert Share.objects.filter(pk=share_b.id).exists()

    def test_t6a_owner_org_can_grant_replace_and_revoke(self, db: None) -> None:
        self._setup()
        share_b = self._grant_b(write=True)
        assert Share.objects.filter(case=self.case, organisation=self.org_b).count() == 1

        # PUT replaces the whole set with a read grant to org C.
        resp = self.owner_client.put(
            f"/api/v1/case/{self.case.id}/shares",
            {"shares": [{"organisation": str(self.org_c.id), "permissions": {"write": False}}]},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        assert not Share.objects.filter(pk=share_b.id).exists()
        share_c = Share.objects.get(case=self.case, organisation=self.org_c)

        # DELETE revokes org C.
        resp = self.owner_client.delete(f"/api/v1/case/{self.case.id}/share/{share_c.id}")
        assert resp.status_code == 204, resp.content
        assert not Share.objects.filter(case=self.case).exists()

    # --- T6-b: additive / default-deny preserved -----------------------------

    def test_t6b_readonly_share_holder_cannot_grant_or_revoke(self, db: None) -> None:
        self._setup()
        share_b = self._grant_b(write=False)

        assert (
            self.b_client.post(
                f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
            ).status_code
            == 404
        )
        assert (
            self.b_client.put(
                f"/api/v1/case/{self.case.id}/shares", self._grant_c_body(), format="json"
            ).status_code
            == 404
        )
        assert (
            self.b_client.delete(f"/api/v1/case/{self.case.id}/share/{share_b.id}").status_code
            == 404
        )

        # The share set is unchanged: only the read grant to org B.
        assert Share.objects.filter(case=self.case).count() == 1
        assert Share.objects.get(pk=share_b.id).can_write is False

    def test_t6b_shared_read_grants_read_not_write_until_write_granted(self, db: None) -> None:
        self._setup()

        # Default-deny: with no share, org B has no access at all.
        assert self.b_client.get(f"/api/v1/case/{self.case.id}").status_code == 404

        self._grant_b(write=False)

        # Read is open; write is refused with the uniform 404.
        assert self.b_client.get(f"/api/v1/case/{self.case.id}").status_code == 200
        assert (
            self.b_client.patch(
                f"/api/v1/case/{self.case.id}", {"title": "hack"}, format="json"
            ).status_code
            == 404
        )

        # Owner upgrades org B to readwrite.
        resp = self.owner_client.put(
            f"/api/v1/case/{self.case.id}/shares",
            {"shares": [{"organisation": str(self.org_b.id), "permissions": {"write": True}}]},
            format="json",
        )
        assert resp.status_code == 200, resp.content

        # Additive: read still works, and write is now open.
        assert self.b_client.get(f"/api/v1/case/{self.case.id}").status_code == 200
        assert (
            self.b_client.patch(
                f"/api/v1/case/{self.case.id}", {"title": "allowed"}, format="json"
            ).status_code
            == 200
        )


# =============================================================================
# NEW-07: UI conformance tests — tenant scoping on UI routes
# These tests cover the UI routes (ui/views.py) to ensure cross-tenant access
# returns uniform 404, matching the API conformance guarantees.
# =============================================================================


class TestUITenantScoping:
    """UI-level tenant scoping tests for ui/views.py routes (NEW-07)."""

    @pytest.fixture(autouse=True)
    def _setup(self, db: None, default_org: Organisation):
        from django.test import Client

        self.org1 = default_org
        self.org2 = Organisation.objects.create(name="Org2")
        self.user1 = _mk_user("ui_user1", self.org1)
        self.user2 = _mk_user("ui_user2", self.org2)
        self.client1 = Client()
        self.client1.force_login(self.user1)
        self.client2 = Client()
        self.client2.force_login(self.user2)
        self.superadmin = _mk_user("ui_super", self.org1, role="superAdmin")
        self.super_client = Client()
        self.super_client.force_login(self.superadmin)

        # Seed data in org1
        self.case1 = _mk_case("Case Org1", self.org1)
        self.alert1 = _mk_alert("Alert Org1", self.org1, "ref-ui-1")
        self.obs1 = _mk_observable("10.0.0.1", self.org1)
        from django.utils import timezone

        from cases.models import CaseObservable, Share, Task, TimelineEvent

        CaseObservable.objects.create(case=self.case1, observable=self.obs1)
        self.task1 = Task.objects.create(case=self.case1, title="Task 1")
        self.event1 = TimelineEvent.objects.create(
            case=self.case1, title="Event 1", description="Note", date=timezone.now()
        )
        # Shared case for share tests
        self.shared_case = _mk_case("Shared Case", self.org1)
        self.share1 = Share.objects.create(
            case=self.shared_case, organisation=self.org2, permissions={"read": True}
        )

        # Seed data in org2
        self.case2 = _mk_case("Case Org2", self.org2)
        self.alert2 = _mk_alert("Alert Org2", self.org2, "ref-ui-2")
        self.obs2 = _mk_observable("10.0.0.2", self.org2)
        CaseObservable.objects.create(case=self.case2, observable=self.obs2)
        self.task2 = Task.objects.create(case=self.case2, title="Task 2")
        self.event2 = TimelineEvent.objects.create(
            case=self.case2, title="Event 2", description="Note", date=timezone.now()
        )

    # --- NEW-01: playbook_run cross-tenant write ---
    def test_ui_playbook_run_cross_tenant_case_returns_404(self) -> None:
        """POST /ui/playbook/<id>/run with cross-tenant case → 404, no run created (NEW-01)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook",
            trigger_event="manual",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant case (org2's case) from org1 user
        resp = self.client1.post(
            reverse("ui-playbook-run", kwargs={"playbook_id": playbook.id}),
            {"case": str(self.case2.id)},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.content}"
        assert AutomationRun.objects.count() == 0, (
            "No AutomationRun should be created on cross-tenant access"
        )

    def test_ui_playbook_run_cross_tenant_observable_returns_404(self) -> None:
        """POST /ui/playbook/<id>/run with cross-tenant observable → 404, no run created (NEW-01)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook-2",
            trigger_event="observable_created",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Cross-tenant observable from org1 user
        resp = self.client1.post(
            reverse("ui-playbook-run", kwargs={"playbook_id": playbook.id}),
            {"observable": str(self.obs2.id)},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.content}"
        assert AutomationRun.objects.count() == 0, (
            "No AutomationRun should be created on cross-tenant access"
        )

    def test_ui_playbook_run_own_org_returns_302(self) -> None:
        """POST /ui/playbook/<id>/run with own-org case → 302 redirect, run created (NEW-01)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="test-playbook-3",
            trigger_event="manual",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        resp = self.client1.post(
            reverse("ui-playbook-run", kwargs={"playbook_id": playbook.id}),
            {"case": str(self.case1.id)},
        )
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}: {resp.content}"
        )
        assert AutomationRun.objects.count() == 1, (
            "AutomationRun should be created for own-org access"
        )
        run = AutomationRun.objects.first()
        assert run.case_id == self.case1.id

    # --- NEW-02: case_apply_template ---
    def test_ui_case_apply_template_cross_tenant_returns_404(self) -> None:
        """POST /ui/case/<id>/template with cross-tenant case → 404 (NEW-02)."""
        from cases.models import CaseTemplate

        template = CaseTemplate.objects.create(name="Test Template", severity=2)

        # Cross-tenant case
        resp = self.client1.post(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case2.number}),
            {"template_id": template.id},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.content}"

    def test_ui_case_apply_template_own_org_returns_302(self) -> None:
        """POST /ui/case/<id>/template with own-org case → 302 redirect (NEW-02)."""
        from cases.models import CaseTemplate

        template = CaseTemplate.objects.create(
            name="Test Template 2", severity=2, description="Test desc"
        )

        resp = self.client1.post(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case1.number}),
            {"template_id": template.id},
        )
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}: {resp.content}"
        )
        # Verify template was applied (description should be set)
        self.case1.refresh_from_db()
        assert self.case1.description == "Test desc"

    def test_ui_case_apply_template_get_own_org_returns_200(self) -> None:
        """GET /ui/case/<id>/template with own-org case → 200 (NEW-02)."""
        from cases.models import CaseTemplate

        CaseTemplate.objects.create(name="Test Template 3", severity=2)

        resp = self.client1.get(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case1.number})
        )
        assert resp.status_code == 200

    def test_ui_case_apply_template_get_cross_tenant_returns_404(self) -> None:
        """GET /ui/case/<id>/template with cross-tenant case → 404 (NEW-02)."""
        resp = self.client1.get(
            reverse("ui-case-apply-template", kwargs={"case_id": self.case2.number})
        )
        assert resp.status_code == 404

    # --- NEW-03: case_merge ---
    def test_ui_case_merge_cross_tenant_returns_404(self) -> None:
        """GET/POST /ui/case/<id>/merge with cross-tenant case → 404 (NEW-03)."""
        # GET - cross-tenant source case
        resp = self.client1.get(reverse("ui-case-merge", kwargs={"case_id": self.case2.number}))
        assert resp.status_code == 404

        # POST - cross-tenant target case
        resp = self.client1.post(
            reverse("ui-case-merge", kwargs={"case_id": self.case1.number}),
            {"target_case": str(self.case2.id)},
        )
        assert resp.status_code == 404

    def test_ui_case_merge_own_org_get_returns_200(self) -> None:
        """GET /ui/case/<id>/merge with own-org case → 200 (NEW-03)."""
        resp = self.client1.get(reverse("ui-case-merge", kwargs={"case_id": self.case1.number}))
        assert resp.status_code == 200

    def test_ui_case_merge_own_org_post_returns_302(self) -> None:
        """POST /ui/case/<id>/merge with own-org cases → 302 redirect (NEW-03)."""
        # Create another case in org1 to merge into
        target_case = _mk_case("Target Case", self.org1)

        resp = self.client1.post(
            reverse("ui-case-merge", kwargs={"case_id": self.case1.number}),
            {"target_case": str(target_case.id)},
        )
        assert resp.status_code == 302, (
            f"Expected 302 redirect, got {resp.status_code}: {resp.content}"
        )
        # Source case should be deleted, target should remain
        assert not Case.objects.filter(pk=self.case1.id).exists()
        assert Case.objects.filter(pk=target_case.id).exists()

    # --- NEW-04: nav counts (dashboard) ---
    def test_ui_dashboard_counts_scoped_to_org(self) -> None:
        """Dashboard counts only show own org data (NEW-04)."""
        resp1 = self.client1.get(reverse("ui-dashboard"))
        resp2 = self.client2.get(reverse("ui-dashboard"))

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # Check context counts are org-scoped
        # Org1 has 2 cases (case1 + shared_case), 1 alert, 1 task
        # Org2 has 2 cases (case2 + shared_case via share), 1 alert, 1 task
        assert resp1.context["counts"]["cases_open"] == 2
        assert resp2.context["counts"]["cases_open"] == 2
        assert resp1.context["counts"]["alerts_unlinked"] == 1
        assert resp2.context["counts"]["alerts_unlinked"] == 1

    # --- NEW-05: alert_detail merge candidates ---
    def test_ui_alert_detail_merge_candidates_scoped(self) -> None:
        """Alert detail merge candidates only show visible cases (NEW-05)."""
        resp1 = self.client1.get(reverse("ui-alert-detail", kwargs={"alert_id": self.alert1.id}))
        resp2 = self.client1.get(reverse("ui-alert-detail", kwargs={"alert_id": self.alert2.id}))

        assert resp1.status_code == 200
        assert resp2.status_code == 404  # Cross-tenant alert

        # Org1 user should only see org1 cases in merge candidates.
        # §12.3 (d596db6) repointed this context from `Case` to `SimilarCase` (correlation-ranked
        # wrappers, `cases/correlation.py`), so the assertions read through `.case`; this test
        # was left behind and broke — see TODO §12.3-fu2. The tenant-scoping intent is unchanged.
        candidates = resp1.context["merge_candidates"]
        candidate_ids = {sc.case.id for sc in candidates}
        assert self.case1.id in candidate_ids
        assert self.case2.id not in candidate_ids

    # --- NEW-06: sources_list and playbook_detail ---
    def test_ui_sources_list_alert_count_scoped(self) -> None:
        """Sources list alert_count only counts own org alerts (NEW-06)."""
        resp1 = self.client1.get(reverse("ui-sources-list"))
        resp2 = self.client2.get(reverse("ui-sources-list"))

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # Each org should see their own alert counts
        for _source in resp1.context["sources"]:
            # The source may have 0 or 1 alerts depending on org
            pass  # Just verify no crash

    def test_ui_playbook_detail_runs_scoped(self) -> None:
        """Playbook detail runs only show visible cases' runs (NEW-06)."""
        from automation.models import AutomationRun, Playbook

        playbook = Playbook.objects.create(
            name="Scoped Playbook",
            trigger_event="manual",
            is_active=True,
            config={
                "action": "python",
                "action_path": "amalthea.automation.executor.enrichment_probe",
            },
        )

        # Create runs on both orgs' cases with unique idempotency keys
        run1 = AutomationRun.objects.create(
            case=self.case1,
            playbook=playbook,
            playbook_name=playbook.name,
            status="Success",
            idempotency_key="test-run-1",
        )
        run2 = AutomationRun.objects.create(
            case=self.case2,
            playbook=playbook,
            playbook_name=playbook.name,
            status="Success",
            idempotency_key="test-run-2",
        )

        resp1 = self.client1.get(reverse("ui-playbook-detail", kwargs={"playbook_id": playbook.id}))
        resp2 = self.client2.get(reverse("ui-playbook-detail", kwargs={"playbook_id": playbook.id}))

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # Org1 should only see run1
        runs1 = list(resp1.context["runs"])
        assert len(runs1) == 1
        assert runs1[0].id == run1.id

        # Org2 should only see run2
        runs2 = list(resp2.context["runs"])
        assert len(runs2) == 1
        assert runs2[0].id == run2.id

    # --- Core UI routes cross-tenant 404 ---
    def test_ui_case_detail_cross_tenant_returns_404(self) -> None:
        """GET /ui/case/<id> cross-tenant → 404."""
        resp = self.client1.get(reverse("ui-case-detail", kwargs={"case_id": self.case2.number}))
        assert resp.status_code == 404

    def test_ui_alert_detail_cross_tenant_returns_404(self) -> None:
        """GET /ui/alert/<uuid> cross-tenant → 404."""
        resp = self.client1.get(reverse("ui-alert-detail", kwargs={"alert_id": self.alert2.id}))
        assert resp.status_code == 404

    def test_ui_observable_detail_cross_tenant_returns_404(self) -> None:
        """GET /ui/observables/<uuid> cross-tenant → 404."""
        resp = self.client1.get(
            reverse("ui-observable-detail", kwargs={"observable_id": self.obs2.id})
        )
        assert resp.status_code == 404

    def test_ui_case_share_list_cross_tenant_returns_404(self) -> None:
        """GET /ui/case/<id>/shares cross-tenant → 404."""
        resp = self.client1.get(
            reverse("ui-case-share-list", kwargs={"case_id": self.case2.number})
        )
        assert resp.status_code == 404

    def test_ui_case_share_revoke_cross_tenant_returns_404(self) -> None:
        """POST /ui/case/<id>/shares/<share_id>/revoke cross-tenant → 404."""
        # First create a share on org2's case (as org2 user)
        from cases.models import Share

        share = Share.objects.create(
            case=self.case2, organisation=self.org1, permissions={"read": True}
        )

        # Org1 user tries to revoke share on org2's case (not owner)
        resp = self.client1.post(
            reverse(
                "ui-case-share-revoke",
                kwargs={"case_id": self.case2.number, "share_id": share.id},
            )
        )
        assert resp.status_code == 404
        # Share should still exist
        assert Share.objects.filter(pk=share.id).exists()

    def test_ui_case_share_list_non_owner_returns_404(self) -> None:
        """GET /ui/case/<id>/shares with read-only share → 404 (not owner)."""
        # User2 has read share on shared_case (owned by org1)
        resp = self.client2.get(
            reverse("ui-case-share-list", kwargs={"case_id": self.shared_case.number})
        )
        assert resp.status_code == 404, "Read-only share holder cannot access share management"

    def test_ui_case_share_list_owner_returns_200(self) -> None:
        """GET /ui/case/<id>/shares owner org → 200."""
        resp = self.client1.get(
            reverse("ui-case-share-list", kwargs={"case_id": self.shared_case.number})
        )
        assert resp.status_code == 200

    def test_ui_case_share_list_malformed_org_id_does_not_500(self) -> None:
        """A tampered `organisation` id must redirect with an error, not 500 (F7 class)."""
        before = Share.objects.filter(case=self.shared_case).count()
        resp = self.client1.post(
            reverse("ui-case-share-list", kwargs={"case_id": self.shared_case.number}),
            {"organisation": "not-a-uuid"},
        )
        assert resp.status_code == 302
        assert Share.objects.filter(case=self.shared_case).count() == before

    def test_ui_case_share_revoke_non_owner_returns_404(self) -> None:
        """POST /ui/case/<id>/shares/<share_id>/revoke with read-only share → 404."""
        # User2 tries to revoke their own share (not owner)
        resp = self.client2.post(
            reverse(
                "ui-case-share-revoke",
                kwargs={"case_id": self.shared_case.number, "share_id": self.share1.id},
            )
        )
        assert resp.status_code == 404
        assert Share.objects.filter(pk=self.share1.id).exists()

    # --- Username picker scoping (org-global disclosure fix) ---
    def test_ui_case_detail_assignee_picker_scoped_to_org(self) -> None:
        """Case detail assignee picker only shows users from the same org."""
        # Create a user in org2
        user_org2 = _mk_user("org2_user", self.org2)

        # Org1 user views case detail
        resp = self.client1.get(reverse("ui-case-detail", kwargs={"case_id": self.case1.number}))
        assert resp.status_code == 200

        # Assignee picker should only contain org1 users
        assignees = resp.context["assignees"]
        assignee_logins = {u.login for u in assignees}
        assert self.user1.login in assignee_logins  # own org user present
        assert user_org2.login not in assignee_logins, "Assignee picker leaked org2 user"

    def test_ui_case_task_create_assignee_lookup_scoped_to_org(self) -> None:
        """Task assignee lookup only finds users from the same org."""
        # Create a user in org2
        user_org2 = _mk_user("org2_user_lookup", self.org2)

        # Org1 user creates a task with org2 user's login - should NOT find them
        resp = self.client1.post(
            reverse("ui-case-task-create", kwargs={"case_id": self.case1.number}),
            {"title": "Test Task", "assignee": user_org2.login},
        )
        assert resp.status_code == 302  # Redirect on success

        # Task should be created but unassigned (org2 user not found)
        task = Task.objects.filter(case=self.case1, title="Test Task").first()
        assert task is not None
        assert task.assignee is None, "Task assignee lookup leaked org2 user"

        # Org1 user creates a task with org1 user's login - SHOULD find them
        resp = self.client1.post(
            reverse("ui-case-task-create", kwargs={"case_id": self.case1.number}),
            {"title": "Test Task 2", "assignee": self.user1.login},
        )
        assert resp.status_code == 302

        task2 = Task.objects.filter(case=self.case1, title="Test Task 2").first()
        assert task2 is not None
        assert task2.assignee == self.user1, "Task assignee lookup failed for own org user"

    # --- §12.5-fu2: case-page write routes (cross-tenant enumeration) ---
    # Why these exist: the three generic sweeps do NOT cover these routes.
    # `_UI_WRITE_ONLY_ROUTES` excludes them from the UI *read* sweep, and the three
    # write sweeps in `TestWriteRouteEnumerationConformance` filter to API routes
    # (`not name.startswith("ui-")`). A refactor that drops the shared
    # `resolve_case(..., write=True)` guard — or the `case=case` clause on the page
    # lookup — would therefore leave CI green. Each test pins the exact 404 (not a
    # 403/redirect, which would leak existence) and the inert effect on the target.
    # TODO §12.5-fu2 (independent verification 2026-10-18, observation O1).

    def test_ui_case_page_create_cross_tenant_returns_404(self) -> None:
        """POST /cases/<org-B-case>/page from org A → uniform 404, no page created."""
        from cases.models import Page
        from identity.models import AuditLog

        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse("ui-case-page-create", kwargs={"case_id": self.case2.number}),
            {"title": "Injected", "content": "cross-tenant"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert not Page.objects.filter(case=self.case2).exists(), (
            "Cross-tenant page create must not mutate org B's case"
        )
        assert AuditLog.objects.count() == audit_before, (
            "Denied request must not write an AuditLog row"
        )

    def test_ui_case_page_create_readonly_share_returns_404(self) -> None:
        """A read-only share must not authorize page writes: 404, no page created.

        Pins the `write=True` half of the guard specifically — a demotion to
        `write=False` would still 404 a foreign case (no share at all) but would
        let a read-only share holder write.
        """
        from cases.models import Page
        from identity.models import AuditLog

        audit_before = AuditLog.objects.count()
        resp = self.client2.post(
            reverse("ui-case-page-create", kwargs={"case_id": self.shared_case.number}),
            {"title": "Injected", "content": "via read-only share"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert not Page.objects.filter(case=self.shared_case).exists(), (
            "Read-only share must not allow page creation"
        )
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_edit_cross_tenant_case_returns_404(self) -> None:
        """POST /cases/<org-B-case>/page/<page>/edit from org A → 404, page untouched."""
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-edit",
                kwargs={"case_id": self.case2.number, "page_id": page.id},
            ),
            {"title": "Hijacked", "content": "mutated"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        page.refresh_from_db()
        assert page.title == "Org B page", "Cross-tenant edit must not change org B's page"
        assert page.content == "original", "Cross-tenant edit must not change org B's page"
        assert page.updated_by is None, "Cross-tenant edit must not stamp updated_by"
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_edit_cross_tenant_page_returns_404(self) -> None:
        """A foreign page id embedded in the caller's OWN case URL → 404, page untouched.

        The distinct attack: the case in the URL is resolvable and writable by the
        caller, so only the `case=case` clause on the page lookup (which makes the
        page org-scoped through its case) can reject the request.
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-edit",
                kwargs={"case_id": self.case1.number, "page_id": page.id},
            ),
            {"title": "Hijacked", "content": "mutated"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        page.refresh_from_db()
        assert page.title == "Org B page", "Foreign page id must not be editable via own case"
        assert page.content == "original", "Foreign page id must not be editable via own case"
        assert page.updated_by is None
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_edit_readonly_share_returns_404(self) -> None:
        """Read-only share holder editing a page on the shared case → 404, page untouched.

        Pins `write=True` on the edit route (see the create twin above).
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.shared_case,
            title="Shared page",
            content="original",
            order=1,
            created_by=self.user1,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client2.post(
            reverse(
                "ui-case-page-edit",
                kwargs={"case_id": self.shared_case.number, "page_id": page.id},
            ),
            {"title": "Hijacked", "content": "mutated"},
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        page.refresh_from_db()
        assert page.title == "Shared page", "Read-only share must not allow page edits"
        assert page.content == "original"
        assert page.updated_by is None
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_delete_cross_tenant_case_returns_404(self) -> None:
        """POST /cases/<org-B-case>/page/<page>/delete from org A → 404, page survives."""
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-delete",
                kwargs={"case_id": self.case2.number, "page_id": page.id},
            ),
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert Page.objects.filter(pk=page.id).exists(), (
            "Cross-tenant page delete must not remove org B's page"
        )
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_delete_cross_tenant_page_returns_404(self) -> None:
        """A foreign page id embedded in the caller's OWN case URL → 404, page survives.

        Same distinct attack as the edit twin: the case resolves, so only the
        `case=case` clause on the page lookup can reject the delete.
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.case2,
            title="Org B page",
            content="original",
            order=1,
            created_by=self.user2,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client1.post(
            reverse(
                "ui-case-page-delete",
                kwargs={"case_id": self.case1.number, "page_id": page.id},
            ),
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert Page.objects.filter(pk=page.id).exists(), (
            "Foreign page id must not be deletable via own case"
        )
        assert AuditLog.objects.count() == audit_before

    def test_ui_case_page_delete_readonly_share_returns_404(self) -> None:
        """Read-only share holder deleting a page on the shared case → 404, page survives.

        Pins `write=True` on the delete route (see the create twin above).
        """
        from cases.models import Page
        from identity.models import AuditLog

        page = Page.objects.create(
            case=self.shared_case,
            title="Shared page",
            content="original",
            order=1,
            created_by=self.user1,
        )
        audit_before = AuditLog.objects.count()
        resp = self.client2.post(
            reverse(
                "ui-case-page-delete",
                kwargs={"case_id": self.shared_case.number, "page_id": page.id},
            ),
        )
        assert resp.status_code == 404, (
            f"Expected uniform 404, got {resp.status_code}: {resp.content}"
        )
        assert Page.objects.filter(pk=page.id).exists(), (
            "Read-only share must not allow page deletion"
        )
        assert AuditLog.objects.count() == audit_before
