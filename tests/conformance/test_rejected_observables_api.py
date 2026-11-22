"""Conformance tests for the Rejected Observables API (Module C, AGENTS.md).

Covers tenant scoping, status/reason filtering, and promotion semantics.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User
from observables.models import RejectedObservable


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


def _mk_client(user: User, scope: str = "readwrite") -> tuple:
    token = "k" + uuid4().hex
    from argon2 import PasswordHasher

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


def _mk_alert(title: str, org: Organisation, source_ref: str) -> Alert:
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return Alert.objects.create(
        title=title,
        source="test-source",
        source_ref=source_ref,
        severity=2,
        status=status,
        owner_org=org,
    )


def _mk_rejected_observable(
    org: Organisation,
    data_type_name: str = "ip",
    value: str = "10.0.0.1",
    reason: str = "other",
    alert: Alert | None = None,
) -> RejectedObservable:
    from observables.models import ObservableType

    otype, _ = ObservableType.objects.get_or_create(
        name=data_type_name, defaults={"is_case_sensitive": False}
    )
    return RejectedObservable.objects.create(
        owner_org=org,
        data_type=otype,
        normalized_data=value,
        alert=alert,
        reason=reason,
    )


@pytest.fixture
def org(db):
    return Organisation.objects.create(name="Test Org")


@pytest.fixture
def user(db, org):
    return User.objects.create(
        login="analyst",
        username="analyst",
        email="a@b.com",
        org=org,
        is_active=True,
        role="analyst",
    )


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def super_admin_user(db, org):
    return User.objects.create(
        login="super",
        username="super",
        email="super@example.net",
        org=org,
        is_active=True,
        role="superAdmin",
    )


@pytest.fixture
def super_client(super_admin_user):
    client = APIClient()
    client.force_authenticate(user=super_admin_user)
    return client


@pytest.mark.django_db
class TestRejectedObservablesTenantScoping:
    """Test that rejected observables are properly tenant-scoped."""

    def test_own_org_user_see_own_rejected_items(self, db, org, user, api_client):
        """An own-org user sees only their own org's rejected observables."""
        org2 = Organisation.objects.create(name="Org2")
        user2 = _mk_user("u2", org2, role="analyst")

        # Create rejected observables in org's org
        _mk_rejected_observable(org, value="10.0.0.1", reason="other")
        # Create rejected observable in other org (should not be visible)
        _mk_rejected_observable(org2, value="10.0.0.2", reason="other")

        client2, _ = _mk_client(user2)

        resp = api_client.get("/api/v1/rejected-observables/")
        assert resp.status_code == 200
        data = resp.json()
        # Should only see org's rejected items
        assert len(data) == 1
        assert data[0]["value"] == "10.0.0.1"

        # Cross-org user sees only their own org's item, never org1's item.
        resp2 = client2.get("/api/v1/rejected-observables/")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2) == 1
        assert data2[0]["value"] == "10.0.0.2"

    def test_super_admin_see_cross_org_rejected_items(
        self, db, org, super_admin_user, super_client
    ):
        """superAdmin sees cross-org rejected items."""
        org2 = Organisation.objects.create(name="Org2")

        # Create rejected observables in both orgs
        _mk_rejected_observable(org, value="10.0.0.1", reason="other")
        _mk_rejected_observable(org2, value="10.0.0.2", reason="false_positive")

        resp = super_client.get("/api/v1/rejected-observables/")
        assert resp.status_code == 200
        data = resp.json()
        # superAdmin should see all rejected observables
        assert len(data) == 2
        values = {item["value"] for item in data}
        assert "10.0.0.1" in values
        assert "10.0.0.2" in values

    def test_status_filter_own_org(self, db, org, user, api_client):
        """Status filter returns correct tokens for own org."""
        # Create rejected observables with different promotion states
        rej_promoted = _mk_rejected_observable(org, value="10.0.0.1", reason="other", alert=None)
        rej_promoted.is_promoted = True
        rej_promoted.save(update_fields=["is_promoted"])

        _mk_rejected_observable(org, value="10.0.0.2", reason="other", alert=None)

        # Filter by promoted=True
        resp = api_client.get("/api/v1/rejected-observables/", {"status": "promoted"})
        assert resp.status_code == 200
        data = resp.json()
        # Only the promoted one should appear
        assert len(data) == 1
        assert data[0]["isPromoted"] is True

        # Filter by promoted=False
        resp = api_client.get("/api/v1/rejected-observables/", {"status": "pending"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["isPromoted"] is False

    def test_empty_filter_params_are_ignored(self, db, org, user, api_client):
        """Empty query params mean no filter, not an impossible exact match."""
        _mk_rejected_observable(org, value="10.0.0.1", reason="duplicate")

        resp = api_client.get("/api/v1/rejected-observables/", {"status": "", "reason": ""})
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_reason_filter(self, db, org, user, api_client):
        """Reason filter returns correct tokens."""
        _mk_rejected_observable(org, value="10.0.0.1", reason="duplicate")
        _mk_rejected_observable(org, value="10.0.0.2", reason="irrelevant")

        # Filter by reason=duplicate
        resp = api_client.get("/api/v1/rejected-observables/", {"reason": "duplicate"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["reason"] == "duplicate"

        # Filter by reason=irrelevant
        resp = api_client.get("/api/v1/rejected-observables/", {"reason": "irrelevant"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["reason"] == "irrelevant"

    def test_viewer_denied_list(self, db, org, user, api_client):
        """Viewer role is denied the rejected-observables list with 403."""
        viewer_org = Organisation.objects.create(name="Viewer Org")
        viewer_user = _mk_user("viewer", viewer_org, role="viewer")
        viewer_client = APIClient()
        viewer_client.force_authenticate(user=viewer_user)

        _mk_rejected_observable(org, value="10.0.0.1", reason="other")

        resp = viewer_client.get("/api/v1/rejected-observables/")
        assert resp.status_code == 403, (
            f"Viewer should be denied list access, got {resp.status_code}: {resp.content}"
        )
        assert resp.json()["type"] == "AuthorizationError"

    def test_viewer_denied_promotion(self, db, org, user, api_client):
        """Viewer role is denied promotion with 403."""

        # Create a viewer user
        viewer_org = Organisation.objects.create(name="Viewer Org")
        viewer_user = User.objects.create(
            login="viewer",
            username="viewer",
            email="viewer@example.net",
            org=viewer_org,
            is_active=True,
            role="viewer",
        )
        viewer_client = APIClient()
        viewer_client.force_authenticate(user=viewer_user)

        rej = _mk_rejected_observable(org, value="10.0.0.1", reason="other")

        resp = viewer_client.post(f"/api/v1/rejected-observables/{rej.id}/promote/")
        assert resp.status_code == 403, (
            f"Viewer should be denied promotion, got {resp.status_code}: {resp.content}"
        )
        assert resp.json()["type"] == "AuthorizationError"

    def test_promotion_idempotent(self, db, org, user, api_client):
        """Promotion is idempotent: already promoted returns (obj, False)."""
        rej = _mk_rejected_observable(org, value="10.0.0.1", reason="other")
        rej.is_promoted = True
        rej.save(update_fields=["is_promoted"])

        url = f"/api/v1/rejected-observables/{rej.id}/promote/"
        resp = api_client.post(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["promoted"] is False  # idempotent, no new save
        # Verify the object still has is_promoted=True
        rej.refresh_from_db()
        assert rej.is_promoted is True

    def test_promotion_updates_is_promoted(self, db, org, user, api_client):
        """Promotion sets is_promoted=True and returns (obj, True)."""
        rej = _mk_rejected_observable(org, value="10.0.0.1", reason="other")
        # Ensure is_promoted starts as False
        rej.is_promoted = False
        rej.save(update_fields=["is_promoted"])

        url = f"/api/v1/rejected-observables/{rej.id}/promote/"
        resp = api_client.post(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["promoted"] is True  # was promoted
        # Verify the object was updated
        rej.refresh_from_db()
        assert rej.is_promoted is True

    def test_promoted_rejected_observable_is_not_suppressed_on_ingest(
        self, db, org, user, api_client
    ):
        """Promoted rows are re-enabled for review and must not suppress ingest extraction."""
        from alerts.models import Alert, AlertStatus
        from ingest.observable_rules import extract_observable_candidates

        _mk_rejected_observable(org, data_type_name="ip", value="1.2.3.4", reason="other")
        promoted = RejectedObservable.objects.get(owner_org=org, normalized_data="1.2.3.4")
        promoted.is_promoted = True
        promoted.save(update_fields=["is_promoted"])

        status, _ = AlertStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )
        alert = Alert.objects.create(
            title="Promoted suppression test",
            source="test-source",
            source_ref="promoted-suppression",
            severity=2,
            status=status,
            owner_org=org,
        )
        config = {"observables": [{"type": "ip", "paths": ["$.ip"]}]}
        extraction = extract_observable_candidates(config, {"ip": "1.2.3.4"}, alert=alert)
        assert [c.raw_value for c in extraction.candidates] == ["1.2.3.4"]
        assert extraction.skipped_values == 0

    def test_promotion_404_if_not_scoped(self, db, org, user, api_client):
        """Promotion returns 404 if the rejected observable is not visible to the user."""
        org2 = Organisation.objects.create(name="Org2")
        rej = _mk_rejected_observable(org2, value="10.0.0.2", reason="other")

        # org user cannot access org2's rejected observable
        url = f"/api/v1/rejected-observables/{rej.id}/promote/"
        resp = api_client.post(url)
        assert resp.status_code == 404, (
            f"Should be 404 for cross-org access, got {resp.status_code}: {resp.content}"
        )

    def test_response_is_json_serializable(self, db, org, user, api_client):
        """Response from rejected observables list is JSON-serializable."""
        _mk_rejected_observable(org, value="10.0.0.1", reason="other")
        resp = api_client.get("/api/v1/rejected-observables/")
        assert resp.status_code == 200
        # DRF Response is always JSON-serializable when content type is application/json
        content = resp.content
        assert b'"value"' in content or b'"isPromoted"' in content
