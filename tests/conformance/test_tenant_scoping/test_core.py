"""Core tenant-scoping unit and HTTP conformance tests."""

from __future__ import annotations

import json

from rest_framework.test import APIClient

from alerts.models import Alert
from cases.models import Case, CaseObservable, Share
from identity.models import IngestionToken, Organisation  # noqa: F401
from ingest.models import IngestionSource

from .helpers import (
    _mk_alert,
    _mk_case,
    _mk_client,
    _mk_observable,
    _mk_org,
    _mk_orgless_actor,
    _mk_user,
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

        from identity.models import IngestionToken, Organisation  # noqa: F811
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
