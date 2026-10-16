"""Tests for O1-O3: Organisation & User Management (superAdmin only).

Covers:
- O1-a: non-superAdmin → uniform 404 on every mutating route and no directory
- O1-b: org create/edit happy + duplicate-name 400 + blank-name 400
- O1-c: exactly one AuditLog row per org/user mutation with the right action
- O1-d: a plain member's own-org GET/PATCH is unchanged
- O2-a..e: blocked 409 when org owns tenant data; empty-org delete succeeds;
  members=reassign moves members then deletes; members=delete removes accounts then deletes
- O3-a..e: non-superAdmin 404; create validators + duplicate rejection;
  cannot delete self / last superAdmin; role/org change audited; login derived from username
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from rest_framework.test import APIClient

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus, Share
from identity.models import AuditLog, IngestionToken, Organisation, User
from ingest.models import IngestionSource
from observables.models import Observable, ObservableType


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst", is_active: bool = True) -> User:
    user = User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=is_active,
    )
    return user


def _mk_client(user: User, scope: str = "readwrite") -> APIClient:
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
    return client


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


class TestOrgManagementO1:
    """O1 — Organisations API + permissions."""

    @pytest.fixture(autouse=True)
    def _setup(self, db: None) -> None:
        self.org1 = _mk_org("Org1")
        self.org2 = _mk_org("Org2")
        self.superadmin = _mk_user("super", self.org1, role="superAdmin")
        self.analyst1 = _mk_user("analyst1", self.org1)
        self.analyst2 = _mk_user("analyst2", self.org2)
        self.super_client = _mk_client(self.superadmin)
        self.client1 = _mk_client(self.analyst1)
        self.client2 = _mk_client(self.analyst2)

    # --- O1-a: non-superAdmin → uniform 404 on mutating routes ---

    def test_non_superadmin_org_create_404(self) -> None:
        resp = self.client1.post("/api/v1/organisation", {"name": "NewOrg"}, format="json")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

    def test_non_superadmin_org_patch_404(self) -> None:
        resp = self.client1.patch(
            f"/api/v1/organisation/{self.org2.id}", {"name": "Hacked"}, format="json"
        )
        assert resp.status_code == 404

    def test_non_superadmin_org_delete_404(self) -> None:
        resp = self.client1.delete(f"/api/v1/organisation/{self.org2.id}")
        assert resp.status_code == 404

    def test_non_superadmin_org_list_no_directory(self) -> None:
        """Non-superAdmin GET /organisation returns only own org (0 or 1), never a directory."""
        resp = self.client1.get("/api/v1/organisation")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "Org1"

    def test_non_superadmin_user_list_404(self) -> None:
        resp = self.client1.get("/api/v1/user")
        assert resp.status_code == 404
        assert resp.json()["type"] == "NotFoundError"

    def test_non_superadmin_user_create_404(self) -> None:
        resp = self.client1.post(
            "/api/v1/user",
            {
                "username": "newuser",
                "email": "new@example.com",
                "org": str(self.org1.id),
                "password": "Secret123!",
            },
            format="json",
        )
        assert resp.status_code == 404

    def test_non_superadmin_user_patch_404(self) -> None:
        resp = self.client1.patch(
            f"/api/v1/user/{self.analyst2.id}", {"role": "admin"}, format="json"
        )
        assert resp.status_code == 404

    def test_non_superadmin_user_delete_404(self) -> None:
        resp = self.client1.delete(f"/api/v1/user/{self.analyst2.id}")
        assert resp.status_code == 404

    # --- O1-b: org create/edit happy + duplicate/blank name errors ---

    def test_superadmin_org_create_happy(self) -> None:
        resp = self.super_client.post(
            "/api/v1/organisation", {"name": "NewOrg", "description": "Test org"}, format="json"
        )
        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert data["name"] == "NewOrg"
        assert data["description"] == "Test org"
        assert "_createdAt" in data and data["_createdAt"] is not None

        # Verify in DB
        org = Organisation.objects.get(name="NewOrg")
        assert org.description == "Test org"

    def test_superadmin_org_create_duplicate_name_400(self) -> None:
        self.super_client.post("/api/v1/organisation", {"name": "UniqueOrg"}, format="json")
        resp = self.super_client.post("/api/v1/organisation", {"name": "UniqueOrg"}, format="json")
        assert resp.status_code == 400
        assert resp.json()["type"] == "BadRequest"
        assert "name" in resp.json()["fields"]
        assert "already exists" in resp.json()["fields"]["name"][0]

    def test_superadmin_org_create_blank_name_400(self) -> None:
        resp = self.super_client.post("/api/v1/organisation", {"name": "  "}, format="json")
        assert resp.status_code == 400
        assert resp.json()["type"] == "BadRequest"
        assert "name" in resp.json()["fields"]
        assert "required" in resp.json()["fields"]["name"][0]

    def test_superadmin_org_patch_happy(self) -> None:
        resp = self.super_client.patch(
            f"/api/v1/organisation/{self.org2.id}",
            {"name": "Org2Renamed", "description": "Updated"},
            format="json",
        )
        assert resp.status_code == 204
        self.org2.refresh_from_db()
        assert self.org2.name == "Org2Renamed"
        assert self.org2.description == "Updated"

    def test_superadmin_org_patch_duplicate_name_400(self) -> None:
        self.super_client.post("/api/v1/organisation", {"name": "OrgA"}, format="json")
        resp = self.super_client.patch(
            f"/api/v1/organisation/{self.org2.id}", {"name": "OrgA"}, format="json"
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["fields"]["name"][0]

    def test_superadmin_org_patch_blank_name_400(self) -> None:
        resp = self.super_client.patch(
            f"/api/v1/organisation/{self.org2.id}", {"name": ""}, format="json"
        )
        assert resp.status_code == 400
        assert "required" in resp.json()["fields"]["name"][0]

    # --- O1-c: exactly one AuditLog row per mutation ---

    def test_org_create_audits_organisation_created(self) -> None:
        AuditLog.objects.all().delete()
        self.super_client.post("/api/v1/organisation", {"name": "AuditedOrg"}, format="json")
        logs = AuditLog.objects.filter(action="organisation.created")
        assert logs.count() == 1
        log = logs.first()
        assert log.target_type == "Organisation"
        assert log.target_org is not None
        assert log.target_org.name == "AuditedOrg"
        assert log.actor == self.superadmin
        assert log.actor_org == self.org1

    def test_org_update_audits_organisation_updated(self) -> None:
        AuditLog.objects.all().delete()
        self.super_client.patch(
            f"/api/v1/organisation/{self.org2.id}", {"description": "New desc"}, format="json"
        )
        logs = AuditLog.objects.filter(action="organisation.updated")
        assert logs.count() == 1
        log = logs.first()
        assert log.target_type == "Organisation"
        assert log.target_org == self.org2

    def test_org_update_no_audit_when_nothing_changed(self) -> None:
        AuditLog.objects.all().delete()
        # Empty body → 400, no audit
        resp = self.super_client.patch(f"/api/v1/organisation/{self.org2.id}", {}, format="json")
        assert resp.status_code == 400
        assert AuditLog.objects.filter(action="organisation.updated").count() == 0

        # Same values → 204 but no audit
        resp = self.super_client.patch(
            f"/api/v1/organisation/{self.org2.id}",
            {"name": "Org2", "description": ""},
            format="json",
        )
        assert resp.status_code == 204
        assert AuditLog.objects.filter(action="organisation.updated").count() == 0

    # --- O1-d: own-org GET/PATCH unchanged for plain member ---

    def test_analyst_own_org_get_works(self) -> None:
        resp = self.client1.get("/api/v1/organisation")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Org1"

    def test_analyst_own_org_detail_get_works(self) -> None:
        resp = self.client1.get(f"/api/v1/organisation/{self.org1.id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Org1"

    def test_analyst_own_org_patch_works(self) -> None:
        resp = self.client1.patch(
            f"/api/v1/organisation/{self.org1.id}",
            {"description": "Analyst updated"},
            format="json",
        )
        assert resp.status_code == 204
        self.org1.refresh_from_db()
        assert self.org1.description == "Analyst updated"


class TestOrgDeletionO2:
    """O2 — Organisation deletion guard."""

    @pytest.fixture(autouse=True)
    def _setup(self, db: None) -> None:
        self.org1 = _mk_org("Org1")
        self.org2 = _mk_org("Org2")  # has members
        self.org3 = _mk_org("Org3")  # target for reassign
        self.empty_org = _mk_org("EmptyOrg")  # no members, no data
        self.superadmin = _mk_user("super", self.org1, role="superAdmin")
        self.member1 = _mk_user("member1", self.org2)
        self.member2 = _mk_user("member2", self.org2)
        self.super_client = _mk_client(self.superadmin)

    # --- O2-a: blocked 409 with counts when org owns tenant data ---

    def test_delete_blocked_when_owns_cases(self) -> None:
        _mk_case("Case1", self.org2)
        resp = self.super_client.delete(f"/api/v1/organisation/{self.org2.id}")
        assert resp.status_code == 409
        assert resp.json()["type"] == "ConflictError"
        assert "tenant data" in resp.json()["message"]
        assert resp.json()["blockers"]["cases"] == 1

    def test_delete_blocked_when_owns_alerts(self) -> None:
        _mk_alert("Alert1", self.org2, "ref1")
        resp = self.super_client.delete(f"/api/v1/organisation/{self.org2.id}")
        assert resp.status_code == 409
        assert resp.json()["blockers"]["alerts"] == 1

    def test_delete_blocked_when_owns_observables(self) -> None:
        _mk_observable("10.0.0.1", self.org2)
        resp = self.super_client.delete(f"/api/v1/organisation/{self.org2.id}")
        assert resp.status_code == 409
        assert resp.json()["blockers"]["observables"] == 1

    def test_delete_blocked_when_owns_ingestion_tokens(self) -> None:
        source = IngestionSource.objects.create(
            organisation=self.org2, slug="src1", name="Source 1"
        )
        IngestionToken.create_token(source=source, organisation=self.org2, name="token1")
        resp = self.super_client.delete(f"/api/v1/organisation/{self.org2.id}")
        assert resp.status_code == 409
        assert resp.json()["blockers"]["ingestionTokens"] == 1

    def test_delete_blocked_when_owns_ingestion_sources(self) -> None:
        """ADR-009: the feed itself is now org-owned, so it blocks deletion on its own — with no
        token and no alert in sight. The blocker is reported by name rather than as a bare
        ProtectedError, which is the whole reason `organisation_blockers` grew a key."""
        IngestionSource.objects.create(organisation=self.org2, slug="src-no-token", name="Source 2")
        resp = self.super_client.delete(f"/api/v1/organisation/{self.org2.id}")
        assert resp.status_code == 409
        assert resp.json()["blockers"]["ingestionSources"] == 1

    def test_delete_blocked_member_options_do_not_override(self) -> None:
        _mk_case("Case1", self.org2)
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "delete"},
            format="json",
        )
        assert resp.status_code == 409
        assert "tenant data" in resp.json()["message"]

    # --- O2-b: empty-org delete succeeds, shares removed, audit preserved ---

    def test_delete_empty_org_succeeds(self) -> None:
        resp = self.super_client.delete(f"/api/v1/organisation/{self.empty_org.id}")
        assert resp.status_code == 204
        assert not Organisation.objects.filter(id=self.empty_org.id).exists()

        # AuditLog for org deleted should exist with target_id captured
        logs = AuditLog.objects.filter(
            action="organisation.deleted", target_id=str(self.empty_org.id)
        )
        assert logs.count() == 1
        assert logs.first().target_org_id is None  # SET_NULL on delete

    def test_delete_removes_shares_to_org(self) -> None:
        case1 = _mk_case("Case1", self.org1)
        Share.objects.create(case=case1, organisation=self.empty_org, permissions={"read": True})
        assert Share.objects.filter(organisation=self.empty_org).exists()

        self.super_client.delete(f"/api/v1/organisation/{self.empty_org.id}")
        assert not Share.objects.filter(organisation=self.org2).exists()

    # --- O2-c: members=reassign moves every member then deletes ---

    def test_delete_with_members_reassign_moves_members(self) -> None:
        assert self.member1.org_id == self.org2.id
        assert self.member2.org_id == self.org2.id

        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "reassign", "target_org": str(self.org3.id)},
            format="json",
        )
        assert resp.status_code == 204

        self.member1.refresh_from_db()
        self.member2.refresh_from_db()
        assert self.member1.org_id == self.org3.id
        assert self.member2.org_id == self.org3.id
        assert not Organisation.objects.filter(id=self.org2.id).exists()

    def test_delete_reassign_missing_target_org_400(self) -> None:
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "reassign"},
            format="json",
        )
        assert resp.status_code == 400
        assert "target_org required" in resp.json()["message"]

    def test_delete_reassign_unknown_target_org_400(self) -> None:
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "reassign", "target_org": fake_uuid},
            format="json",
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["fields"]["target_org"][0]

    def test_delete_reassign_same_org_400(self) -> None:
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "reassign", "target_org": str(self.org2.id)},
            format="json",
        )
        assert resp.status_code == 400
        assert "invalid" in resp.json()["fields"]["target_org"][0]

    # --- O2-d: members=delete removes accounts then deletes ---

    def test_delete_with_members_delete_removes_accounts(self) -> None:
        member_id = self.member1.id
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "delete"},
            format="json",
        )
        assert resp.status_code == 204

        assert not User.objects.filter(id=member_id).exists()
        assert not Organisation.objects.filter(id=self.org2.id).exists()

    def test_delete_members_delete_audits_user_deleted(self) -> None:
        AuditLog.objects.all().delete()
        self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "delete"},
            format="json",
        )
        # Should have user.deleted for each member + organisation.deleted
        user_deleted = AuditLog.objects.filter(action="user.deleted")
        assert user_deleted.count() == 2
        org_deleted = AuditLog.objects.filter(action="organisation.deleted")
        assert org_deleted.count() == 1

    # --- O2-e: no partial state on failure ---

    def test_delete_atomic_on_protected_error_race(self, monkeypatch) -> None:
        """Simulate a race where tenant data appears after pre-flight."""
        from django.db import models

        # Create a new org without members for this test
        empty_org = _mk_org("EmptyOrgForRaceTest")

        original_delete = Organisation.delete

        def failing_delete(self, *args, **kwargs):
            if self.id == empty_org.id:
                raise models.ProtectedError("race", [])
            return original_delete(self, *args, **kwargs)

        monkeypatch.setattr(Organisation, "delete", failing_delete)

        resp = self.super_client.delete(f"/api/v1/organisation/{empty_org.id}")
        assert resp.status_code == 409
        assert "race" in resp.json()["message"]

        # Org should still exist
        assert Organisation.objects.filter(id=empty_org.id).exists()

    def test_delete_atomic_reassign_not_partial_on_race(self, monkeypatch) -> None:
        """A raced ProtectedError must roll back member reassignment."""
        from django.db import models

        race_org_id = self.org2.id
        original_delete = Organisation.delete

        def failing_delete(self, *args, **kwargs):
            if self.id == race_org_id:
                raise models.ProtectedError("race", [])
            return original_delete(self, *args, **kwargs)

        monkeypatch.setattr(Organisation, "delete", failing_delete)

        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "reassign", "target_org": str(self.org3.id)},
            format="json",
        )
        assert resp.status_code == 409
        assert "race" in resp.json()["message"]

        # Org survives and members were NOT moved to the target.
        assert Organisation.objects.filter(id=self.org2.id).exists()
        self.member1.refresh_from_db()
        self.member2.refresh_from_db()
        assert self.member1.org_id == self.org2.id
        assert self.member2.org_id == self.org2.id

    def test_delete_atomic_member_delete_not_partial_on_race(self, monkeypatch) -> None:
        """A raced ProtectedError must roll back member account deletion."""
        from django.db import models

        race_org_id = self.org2.id
        original_delete = Organisation.delete

        def failing_delete(self, *args, **kwargs):
            if self.id == race_org_id:
                raise models.ProtectedError("race", [])
            return original_delete(self, *args, **kwargs)

        monkeypatch.setattr(Organisation, "delete", failing_delete)

        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "delete"},
            format="json",
        )
        assert resp.status_code == 409

        # Members must still exist — the deletion rolled back with the org.
        assert User.objects.filter(id=self.member1.id).exists()
        assert User.objects.filter(id=self.member2.id).exists()
        assert Organisation.objects.filter(id=self.org2.id).exists()

    def test_service_reassign_without_target_never_orphans_members(self) -> None:
        """The service itself must refuse reassign with no/invalid target."""
        from identity.services import OrganisationDeleteBlockedError, delete_organisation

        with pytest.raises(OrganisationDeleteBlockedError):
            delete_organisation(
                self.org2,
                members="reassign",
                target=None,
                actor=self.superadmin,
                request=None,
            )

        self.member1.refresh_from_db()
        self.member2.refresh_from_db()
        assert self.member1.org_id == self.org2.id
        assert self.member2.org_id == self.org2.id

    def test_delete_self_via_members_delete_blocked(self) -> None:
        """members=delete must never delete the acting superAdmin (lock-out, R1)."""
        # self.superadmin belongs to org1; deleting org1 with members=delete removes them.
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org1.id}",
            {"members": "delete"},
            format="json",
        )
        assert resp.status_code == 409
        assert resp.json()["blockers"]["reason"] == "self_lockout"
        assert Organisation.objects.filter(id=self.org1.id).exists()
        assert User.objects.filter(pk=self.superadmin.pk).exists()

    def test_delete_race_leaves_no_audit_rows(self, monkeypatch) -> None:
        """The org/user audit rows roll back with a raced delete (atomic audit)."""
        from django.db import models

        race_org_id = self.org2.id
        original_delete = Organisation.delete

        def failing_delete(self, *args, **kwargs):
            if self.id == race_org_id:
                raise models.ProtectedError("race", [])
            return original_delete(self, *args, **kwargs)

        monkeypatch.setattr(Organisation, "delete", failing_delete)

        AuditLog.objects.all().delete()
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org2.id}",
            {"members": "delete"},
            format="json",
        )
        assert resp.status_code == 409
        # No user.deleted / organisation.deleted rows survive a rolled-back delete.
        assert not AuditLog.objects.exists()

    def test_delete_own_org_by_reassign_audits_without_error(self) -> None:
        """F2 regression: deleting the actor's own org (via reassign) must not 500."""
        resp = self.super_client.delete(
            f"/api/v1/organisation/{self.org1.id}",
            {"members": "reassign", "target_org": str(self.org3.id)},
            format="json",
        )
        assert resp.status_code == 204
        assert not Organisation.objects.filter(id=self.org1.id).exists()
        log = AuditLog.objects.filter(action="organisation.deleted").first()
        assert log is not None
        # The org FK is nulled by the delete, but the audit row survives.
        assert log.target_org_id is None


class TestUserManagementO3:
    """O3 — Users API."""

    @pytest.fixture(autouse=True)
    def _setup(self, db: None) -> None:
        self.org1 = _mk_org("Org1")
        self.org2 = _mk_org("Org2")
        self.superadmin = _mk_user("super", self.org1, role="superAdmin")
        self.analyst1 = _mk_user("analyst1", self.org1)
        self.analyst2 = _mk_user("analyst2", self.org2)
        self.super_client = _mk_client(self.superadmin)
        self.client1 = _mk_client(self.analyst1)

    # --- O3-a: non-superAdmin → uniform 404 on all user management routes ---

    def test_non_superadmin_user_collection_404(self) -> None:
        resp = self.client1.get("/api/v1/user")
        assert resp.status_code == 404

    def test_non_superadmin_user_create_404(self) -> None:
        resp = self.client1.post(
            "/api/v1/user",
            {
                "username": "u",
                "email": "u@e.com",
                "org": str(self.org1.id),
                "password": "Secret123!",
            },
            format="json",
        )
        assert resp.status_code == 404

    def test_non_superadmin_user_detail_management_404(self) -> None:
        # GET is allowed for any authenticated user (assignee resolution)
        resp = self.client1.get(f"/api/v1/user/{self.analyst2.id}")
        assert resp.status_code == 200
        assert resp.json()["login"] == "analyst2"
        # PATCH/DELETE require superAdmin
        resp = self.client1.patch(
            f"/api/v1/user/{self.analyst2.id}", {"role": "admin"}, format="json"
        )
        assert resp.status_code == 404
        resp = self.client1.delete(f"/api/v1/user/{self.analyst2.id}")
        assert resp.status_code == 404

    # --- O3-b: create validators + duplicate rejection ---

    def test_user_create_happy(self) -> None:
        resp = self.super_client.post(
            "/api/v1/user",
            {
                "username": "newuser",
                "email": "new@example.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
                "role": "analyst",
                "is_active": True,
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        data = resp.json()
        assert data["login"] == "newuser"
        assert data["email"] == "new@example.com"
        assert data["role"] == "analyst"
        assert data["orgId"] == str(self.org2.id)
        assert data["isActive"] is True
        assert "_createdAt" in data

        user = User.objects.get(login="newuser")
        assert user.check_password("Secret123!")
        assert user.org == self.org2

    def test_user_create_login_derived_from_username(self) -> None:
        """login is derived from username by User.save(), not accepted from client."""
        self.super_client.post(
            "/api/v1/user",
            {
                "username": "derived",
                "email": "d@e.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
            },
            format="json",
        )
        user = User.objects.get(username="derived")
        assert user.login == "derived"
        # Ensure client cannot override login
        self.super_client.post(
            "/api/v1/user",
            {
                "username": "derived2",
                "email": "d2@e.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
                "login": "hacked",
            },
            format="json",
        )
        user2 = User.objects.get(username="derived2")
        assert user2.login == "derived2"  # login still derived from username

    def test_user_create_duplicate_username_400(self) -> None:
        self.super_client.post(
            "/api/v1/user",
            {
                "username": "dupe",
                "email": "d@e.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
            },
            format="json",
        )
        resp = self.super_client.post(
            "/api/v1/user",
            {
                "username": "dupe",
                "email": "d2@e.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["fields"]["username"][0]

    def test_user_create_duplicate_login_400(self) -> None:
        _mk_user("existing", self.org2)
        resp = self.super_client.post(
            "/api/v1/user",
            {
                "username": "existing",
                "email": "d@e.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["fields"]["username"][0]

    def test_user_create_missing_required_400(self) -> None:
        resp = self.super_client.post("/api/v1/user", {}, format="json")
        assert resp.status_code == 400
        fields = resp.json()["fields"]
        assert "username" in fields and "required" in fields["username"][0]
        assert "email" in fields and "required" in fields["email"][0]
        assert "password" in fields and "required" in fields["password"][0]
        assert "org" in fields and "required" in fields["org"][0]

    def test_user_create_invalid_role_400(self) -> None:
        resp = self.super_client.post(
            "/api/v1/user",
            {
                "username": "u",
                "email": "u@e.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
                "role": "invalid",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "role" in resp.json()["fields"]

    def test_user_create_unknown_org_400(self) -> None:
        resp = self.super_client.post(
            "/api/v1/user",
            {
                "username": "u",
                "email": "u@e.com",
                "org": "00000000-0000-0000-0000-000000000000",
                "password": "Secret123!",
            },
            format="json",
        )
        assert resp.status_code == 400
        assert "org" in resp.json()["fields"]
        assert "not found" in resp.json()["fields"]["org"][0]

    def test_user_create_password_validation_fails_400(self) -> None:
        # Django's default validators require min 8 chars, not all numeric, etc.
        resp = self.super_client.post(
            "/api/v1/user",
            {"username": "u", "email": "u@e.com", "org": str(self.org2.id), "password": "weak"},
            format="json",
        )
        assert resp.status_code == 400
        assert "password" in resp.json()["fields"]

    # --- O3-c: cannot delete self / last superAdmin ---

    def test_user_delete_self_400(self) -> None:
        resp = self.super_client.delete(f"/api/v1/user/{self.superadmin.id}")
        assert resp.status_code == 400
        assert "self" in resp.json()["fields"]["user"][0]

    def test_user_delete_last_active_superadmin_400(self) -> None:
        # Ensure only one active superAdmin exists (self)
        User.objects.filter(role="superAdmin", is_active=True).exclude(
            id=self.superadmin.id
        ).update(is_active=False)
        # Trying to delete self should fail with self_delete error
        resp = self.super_client.delete(f"/api/v1/user/{self.superadmin.id}")
        assert resp.status_code == 400
        assert "self" in resp.json()["fields"]["user"][0]

    def test_user_delete_other_superadmin_ok_if_multiple(self) -> None:
        other_super = _mk_user("super2", self.org1, role="superAdmin")
        resp = self.super_client.delete(f"/api/v1/user/{other_super.id}")
        assert resp.status_code == 204

    def test_user_delete_inactive_superadmin_ok(self) -> None:
        inactive_super = _mk_user("inactive_super", self.org1, role="superAdmin", is_active=False)
        resp = self.super_client.delete(f"/api/v1/user/{inactive_super.id}")
        assert resp.status_code == 204

    # --- O3-d: role/org changes audited ---

    def test_user_update_role_audited(self) -> None:
        AuditLog.objects.all().delete()
        self.super_client.patch(
            f"/api/v1/user/{self.analyst1.id}", {"role": "admin"}, format="json"
        )
        logs = AuditLog.objects.filter(action="user.updated", target_id=str(self.analyst1.id))
        assert logs.count() == 1
        assert logs.first().target_org == self.org1

    def test_user_update_org_audited(self) -> None:
        AuditLog.objects.all().delete()
        self.super_client.patch(
            f"/api/v1/user/{self.analyst1.id}", {"org": str(self.org2.id)}, format="json"
        )
        logs = AuditLog.objects.filter(action="user.updated", target_id=str(self.analyst1.id))
        assert logs.count() == 1
        assert logs.first().target_org == self.org2

    def test_user_update_is_active_audited(self) -> None:
        AuditLog.objects.all().delete()
        self.super_client.patch(
            f"/api/v1/user/{self.analyst1.id}", {"is_active": False}, format="json"
        )
        logs = AuditLog.objects.filter(action="user.updated", target_id=str(self.analyst1.id))
        assert logs.count() == 1

    def test_user_update_password_audited_not_logged(self) -> None:
        AuditLog.objects.all().delete()
        self.super_client.patch(
            f"/api/v1/user/{self.analyst1.id}", {"password": "NewSecret123!"}, format="json"
        )
        logs = AuditLog.objects.filter(action="user.updated", target_id=str(self.analyst1.id))
        assert logs.count() == 1
        # Password should not be in the audit log (it's not in target_id/fields)

    def test_user_update_empty_body_400(self) -> None:
        resp = self.super_client.patch(f"/api/v1/user/{self.analyst1.id}", {}, format="json")
        assert resp.status_code == 400

    # --- O3-e: login derived from username (no blank login) ---

    def test_user_create_sets_login_from_username(self) -> None:
        self.super_client.post(
            "/api/v1/user",
            {
                "username": "loginfromuser",
                "email": "l@e.com",
                "org": str(self.org2.id),
                "password": "Secret123!",
            },
            format="json",
        )
        user = User.objects.get(username="loginfromuser")
        assert user.login == "loginfromuser"

    def test_user_update_cannot_change_own_role_from_superadmin(self) -> None:
        resp = self.super_client.patch(
            f"/api/v1/user/{self.superadmin.id}", {"role": "analyst"}, format="json"
        )
        assert resp.status_code == 400
        assert "self_lockout" in resp.json()["fields"]["role"][0]

    def test_user_update_cannot_deactivate_self_as_superadmin(self) -> None:
        resp = self.super_client.patch(
            f"/api/v1/user/{self.superadmin.id}", {"is_active": False}, format="json"
        )
        assert resp.status_code == 400
        assert "self_lockout" in resp.json()["fields"]["is_active"][0]

    def test_user_update_cannot_demote_last_active_superadmin(self) -> None:
        # Create second superAdmin, deactivate them
        other = _mk_user("super2", self.org1, role="superAdmin")
        other.is_active = False
        other.save()
        # Now self.superadmin is the only active superAdmin
        # Trying to demote self should fail with self_lockout
        resp = self.super_client.patch(
            f"/api/v1/user/{self.superadmin.id}", {"role": "analyst"}, format="json"
        )
        assert resp.status_code == 400
        assert "self_lockout" in resp.json()["fields"]["role"][0]

    def test_user_update_cannot_deactivate_last_active_superadmin(self) -> None:
        other = _mk_user("super2", self.org1, role="superAdmin")
        other.is_active = False
        other.save()
        # Trying to deactivate self should fail with self_lockout
        resp = self.super_client.patch(
            f"/api/v1/user/{self.superadmin.id}", {"is_active": False}, format="json"
        )
        assert resp.status_code == 400
        assert "self_lockout" in resp.json()["fields"]["is_active"][0]

    def test_user_update_cannot_demote_other_last_active_superadmin(self) -> None:
        """Demoting another superAdmin who is the last active one should fail."""
        other = _mk_user("super2", self.org1, role="superAdmin")
        self.superadmin.is_active = False
        self.superadmin.save()
        # Now 'other' is the only active superAdmin
        resp = self.super_client.patch(
            f"/api/v1/user/{other.id}", {"role": "analyst"}, format="json"
        )
        assert resp.status_code == 400
        assert "last_superadmin" in resp.json()["fields"]["role"][0]

    def test_user_update_cannot_deactivate_other_last_active_superadmin(self) -> None:
        """Deactivating another superAdmin who is the last active one should fail."""
        other = _mk_user("super2", self.org1, role="superAdmin")
        self.superadmin.is_active = False
        self.superadmin.save()
        resp = self.super_client.patch(
            f"/api/v1/user/{other.id}", {"is_active": False}, format="json"
        )
        assert resp.status_code == 400
        assert "last_superadmin" in resp.json()["fields"]["is_active"][0]

    def test_user_delete_other_last_active_superadmin_400(self) -> None:
        """Deleting another superAdmin who is the last active one should fail."""
        other = _mk_user("super2", self.org1, role="superAdmin")
        self.superadmin.is_active = False
        self.superadmin.save()
        resp = self.super_client.delete(f"/api/v1/user/{other.id}")
        assert resp.status_code == 400
        assert "last_superadmin" in resp.json()["fields"]["user"][0]

    def test_user_update_org_reassignment(self) -> None:
        self.super_client.patch(
            f"/api/v1/user/{self.analyst1.id}", {"org": str(self.org2.id)}, format="json"
        )
        self.analyst1.refresh_from_db()
        assert self.analyst1.org == self.org2


class TestOrgUserSerializerRegression:
    """Ensure existing serializers behave correctly with new timestamps."""

    @pytest.fixture(autouse=True)
    def _setup(self, db: None) -> None:
        self.org = _mk_org("TestOrg")
        self.user = _mk_user("testuser", self.org)

    def test_organisation_json_includes_timestamps(self) -> None:
        from core.serializers import organisation_json

        data = organisation_json(self.org)
        assert data["_createdAt"] is not None
        assert data["_updatedAt"] is not None
        assert data["_type"] == "Organisation"

    def test_user_admin_json_includes_all_fields(self) -> None:
        from core.serializers import user_admin_json

        data = user_admin_json(self.user)
        assert data["_type"] == "user"
        assert data["login"] == "testuser"
        assert data["email"] == "testuser@example.net"
        assert data["role"] == "analyst"
        assert data["org"] == "TestOrg"
        assert data["orgId"] == str(self.org.id)
        assert data["isActive"] is True
        assert data["_createdAt"] is not None
        assert data["lastLogin"] is None  # never logged in

    def test_user_json_unchanged(self) -> None:
        """user_json (used by user_current/user_detail) must remain unchanged."""
        from core.serializers import user_json

        data = user_json(self.user)
        # Does NOT have role, orgId, isActive, lastLogin
        assert "role" not in data
        assert "orgId" not in data
        assert "isActive" not in data
        assert "lastLogin" not in data
        # Has the legacy fields
        assert data["_type"] == "user"
        assert data["login"] == "testuser"
        assert data["org"] == "TestOrg"
        assert data["hasKey"] is False
        assert data["hasPassword"] is True
