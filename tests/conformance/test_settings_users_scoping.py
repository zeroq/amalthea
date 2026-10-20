"""Conformance tests for org-scoped Settings → Users (PLAN-2026-10-20-org-scoped-user-management).

Mirrors the structure of `test_settings_ingestion_tokens.py`:
- TestRoleGates: analyst/viewer → 404 on all three routes (AC-14)
- TestSuperadminOrgHandling: superAdmin sees all orgs, per-org layout (AC-14, AC-17)
- TestAdminOrgBinding: admin sees only own org, create/edit/delete pinned to own org (AC-15)
- TestEscalationGuards: admin cannot create/promote admin|superAdmin; cannot edit/delete such targets (AC-16)
- TestAuditTargetOrg: audit rows carry target user's org (AC-20)
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from identity.models import AuditLog, Organisation, User


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


def _ui_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


class TestRoleGates:
    """AC-14 — analyst and viewer get uniform 404 on all three routes."""

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_roles_locked_out_of_list(self, db: None, role: str) -> None:
        org = _mk_org("Org1")
        user = _mk_user(f"user-{role}", org, role=role)
        client = _ui_client(user)

        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 404

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_roles_locked_out_of_edit(self, db: None, role: str) -> None:
        org = _mk_org("Org1")
        user = _mk_user(f"user-{role}", org, role=role)
        target = _mk_user("target", org)
        client = _ui_client(user)

        resp = client.get(reverse("ui-settings-users-edit", kwargs={"user_id": target.id}))
        assert resp.status_code == 404

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_roles_locked_out_of_delete(self, db: None, role: str) -> None:
        org = _mk_org("Org1")
        user = _mk_user(f"user-{role}", org, role=role)
        target = _mk_user("target", org)
        client = _ui_client(user)

        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": target.id}))
        assert resp.status_code == 404


class TestSuperadminOrgHandling:
    """AC-14, AC-17 — superAdmin sees all orgs with per-org layout."""

    def test_superadmin_list_shows_multiple_orgs(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        _mk_user("user-a", org_a)
        _mk_user("user-b", org_b)

        client = _ui_client(superadmin)
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200

        # SuperAdmin uses per-org context, not pagination
        assert resp.context["page_obj"] is None
        org_contexts = resp.context["org_contexts"]
        assert len(org_contexts) >= 2

        # Each org context has its users and member count
        org_names = {ctx["org"].name for ctx in org_contexts}
        assert "Org A" in org_names
        assert "Org B" in org_names

        # Verify member counts
        for ctx in org_contexts:
            if ctx["org"].name == "Org A":
                assert ctx["member_count"] >= 2  # superadmin + user-a
            elif ctx["org"].name == "Org B":
                assert ctx["member_count"] >= 1  # user-b

    def test_superadmin_per_org_cap_disclosure(self, db: None) -> None:
        """When an org exceeds USERS_PER_ORG_CAP, the card shows the disclosure."""
        from ui.views import USERS_PER_ORG_CAP

        org = _mk_org("BigOrg")
        superadmin = _mk_user("super", org, role="superAdmin")
        # Create more users than the cap
        for i in range(USERS_PER_ORG_CAP + 5):
            _mk_user(f"user{i}", org)

        client = _ui_client(superadmin)
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200

        org_contexts = resp.context["org_contexts"]
        big_org_ctx = next(ctx for ctx in org_contexts if ctx["org"].name == "BigOrg")
        assert big_org_ctx["capped"] is True
        assert big_org_ctx["cap_limit"] == USERS_PER_ORG_CAP
        assert big_org_ctx["member_count"] == USERS_PER_ORG_CAP + 5 + 1  # +1 for superadmin
        assert len(big_org_ctx["users"]) == USERS_PER_ORG_CAP

    def test_superadmin_empty_org_still_renders_card(self, db: None) -> None:
        """An org with 0 users still renders its card with 'No users' line."""
        org_a = _mk_org("Org A")
        _mk_org("EmptyOrg")
        superadmin = _mk_user("super", org_a, role="superAdmin")

        client = _ui_client(superadmin)
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200

        org_contexts = resp.context["org_contexts"]
        empty_org_ctx = next(ctx for ctx in org_contexts if ctx["org"].name == "EmptyOrg")
        assert empty_org_ctx["member_count"] == 0
        assert empty_org_ctx["capped"] is False
        assert len(empty_org_ctx["users"]) == 0

    def test_superadmin_create_in_chosen_org(self, db: None) -> None:
        """SuperAdmin can create a user in any org via the per-org modal."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        client = _ui_client(superadmin)

        # POST to create user in org_b
        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "newuser",
                "email": "new@orgb.net",
                "password": "ComplexPass123!",
                "role": "analyst",
                "org": str(org_b.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        user = User.objects.get(username="newuser")
        assert user.org_id == org_b.id


class TestAdminOrgBinding:
    """AC-15 — admin sees only own org; create/edit/delete pinned to own org."""

    def test_admin_list_only_own_org(self, db: None) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        _mk_user("user-a", org_a)
        other = _mk_user("user-b", org_b)

        client = _ui_client(admin_a)
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200

        # Admin uses flat paginated table
        assert resp.context["page_obj"] is not None
        users = list(resp.context["page_obj"].object_list)
        assert len(users) == 2  # admin-a + user-a
        assert all(u.org_id == org_a.id for u in users)

        # Verify org_b user's row is absent — match on its exact id, not a substring of the body
        # (a bare "user-b" can appear inside a random UUID such as "...delete-user-bbc7...").
        body = resp.content.decode()
        assert f'data-user-id="{other.id}"' not in body

    def test_admin_does_not_see_superadmin_in_own_org(self, db: None) -> None:
        """A lower role must never see a higher-role user: superAdmins are hidden from an admin."""
        org = _mk_org("Org A")
        admin = _mk_user("admin-a", org, role="admin")
        _mk_user("analyst-a", org, role="analyst")
        super_user = _mk_user("super-a", org, role="superAdmin")

        client = _ui_client(admin)
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200

        users = list(resp.context["page_obj"].object_list)
        assert all(u.role != "superAdmin" for u in users), "admin must not see superAdmins"
        assert {u.login for u in users} == {"admin-a", "analyst-a"}

        # Match the exact id, not a substring that could collide with a UUID fragment.
        assert f'data-user-id="{super_user.id}"' not in resp.content.decode()

    def test_admin_does_not_see_superadmin_in_any_org(self, db: None) -> None:
        """superAdmins are hidden regardless of which org they belong to."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin = _mk_user("admin-a", org_a, role="admin")
        _mk_user("super-a", org_a, role="superAdmin")
        _mk_user("super-b", org_b, role="superAdmin")

        client = _ui_client(admin)
        resp = client.get(reverse("ui-settings-users"))
        users = list(resp.context["page_obj"].object_list)
        assert all(u.role != "superAdmin" for u in users)

    def test_superadmin_still_sees_superadmins(self, db: None) -> None:
        """The hide rule must NOT apply to a superAdmin's own view."""
        org = _mk_org("Org A")
        superadmin = _mk_user("super-a", org, role="superAdmin")
        _mk_user("super-b", org, role="superAdmin")

        client = _ui_client(superadmin)
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200
        # SuperAdmin uses the per-org layout; collect all users across org cards.
        seen = {u.login for ctx in resp.context["org_contexts"] for u in ctx["users"]}
        assert "super-b" in seen, "superAdmin must still see other superAdmins"

    def test_admin_create_pinned_to_own_org(self, db: None) -> None:
        """Admin posting a foreign org_id creates user in admin's own org."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        client = _ui_client(admin_a)

        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "newuser",
                "email": "new@example.net",
                "password": "ComplexPass123!",
                "role": "analyst",
                "org": str(org_b.id),  # Attempt to create in org_b
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        user = User.objects.get(username="newuser")
        assert user.org_id == org_a.id, "admin's posted org was ignored, user created in own org"

    def test_admin_edit_out_of_org_returns_404(self, db: None) -> None:
        """Admin editing a user in another org gets 404."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        target_b = _mk_user("target-b", org_b)
        client = _ui_client(admin_a)

        resp = client.get(reverse("ui-settings-users-edit", kwargs={"user_id": target_b.id}))
        assert resp.status_code == 404

        # POST also 404
        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target_b.id}),
            {
                "email": "changed@example.net",
                "role": "analyst",
                "org": str(org_b.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 404

    def test_admin_delete_out_of_org_returns_404(self, db: None) -> None:
        """Admin deleting a user in another org gets 404."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        admin_a = _mk_user("admin-a", org_a, role="admin")
        target_b = _mk_user("target-b", org_b)
        client = _ui_client(admin_a)

        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": target_b.id}))
        assert resp.status_code == 404
        assert User.objects.filter(pk=target_b.id).exists()


class TestEscalationGuards:
    """AC-16 — admin cannot create/promote admin|superAdmin; cannot edit/delete such targets."""

    def test_admin_cannot_create_admin_role(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "newadmin",
                "email": "newadmin@example.net",
                "password": "ComplexPass123!",
                "role": "admin",  # Not allowed for admin
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 200  # Re-renders with error
        content = resp.content.decode()
        assert "admin may only assign roles" in content
        assert not User.objects.filter(username="newadmin").exists()

    def test_admin_cannot_create_superadmin_role(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "newsuper",
                "email": "newsuper@example.net",
                "password": "ComplexPass123!",
                "role": "superAdmin",  # Not allowed for admin
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "admin may only assign roles" in content
        assert not User.objects.filter(username="newsuper").exists()

    def test_admin_cannot_promote_to_admin(self, db: None) -> None:
        """Admin cannot promote existing user to admin role."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target = _mk_user("target", org, role="analyst")
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {"email": "target@example.net", "role": "admin", "org": str(org.id), "is_active": "on"},
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "admin may only assign roles" in content
        target.refresh_from_db()
        assert target.role == "analyst"

    def test_admin_cannot_promote_to_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target = _mk_user("target", org, role="analyst")
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {
                "email": "target@example.net",
                "role": "superAdmin",
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "admin may only assign roles" in content
        target.refresh_from_db()
        assert target.role == "analyst"

    def test_admin_empty_role_post_is_rejected(self, db: None) -> None:
        """SA-01 regression: a present-but-empty role must be rejected, not silently ignored."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target = _mk_user("target", org, role="analyst")
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {"email": "target@example.net", "role": "", "org": str(org.id), "is_active": "on"},
        )
        assert resp.status_code == 200
        assert "admin may only assign roles" in resp.content.decode()
        target.refresh_from_db()
        assert target.role == "analyst"

    def test_admin_absent_role_post_leaves_role_unchanged(self, db: None) -> None:
        """A partial post (no role key, e.g. password reset) must not error and must keep the role."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target = _mk_user("target", org, role="analyst")
        client = _ui_client(admin)

        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {"email": "target@example.net", "is_active": "on"},
        )
        assert resp.status_code == 302  # success redirect
        target.refresh_from_db()
        assert target.role == "analyst"
        assert target.email == "target@example.net"

    def test_admin_cannot_edit_admin_target(self, db: None) -> None:
        """Admin editing a target whose role is admin → 404."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target_admin = _mk_user("target-admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.get(reverse("ui-settings-users-edit", kwargs={"user_id": target_admin.id}))
        assert resp.status_code == 404

    def test_admin_cannot_edit_superadmin_target(self, db: None) -> None:
        """Admin editing a target whose role is superAdmin → 404."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target_super = _mk_user("target-super", org, role="superAdmin")
        client = _ui_client(admin)

        resp = client.get(reverse("ui-settings-users-edit", kwargs={"user_id": target_super.id}))
        assert resp.status_code == 404

    def test_admin_cannot_delete_admin_target(self, db: None) -> None:
        """Admin deleting a target whose role is admin → 404."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target_admin = _mk_user("target-admin", org, role="admin")
        client = _ui_client(admin)

        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": target_admin.id}))
        assert resp.status_code == 404
        assert User.objects.filter(pk=target_admin.id).exists()

    def test_admin_cannot_delete_superadmin_target(self, db: None) -> None:
        """Admin deleting a target whose role is superAdmin → 404."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target_super = _mk_user("target-super", org, role="superAdmin")
        client = _ui_client(admin)

        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": target_super.id}))
        assert resp.status_code == 404
        assert User.objects.filter(pk=target_super.id).exists()

    def test_superadmin_unaffected_by_escalation_guards(self, db: None) -> None:
        """SuperAdmin can still create/promote admin and superAdmin."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        target = _mk_user("target", org_b, role="analyst")
        client = _ui_client(superadmin)

        # Create admin in org_b
        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "newadmin",
                "email": "newadmin@orgb.net",
                "password": "ComplexPass123!",
                "role": "admin",
                "org": str(org_b.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        user = User.objects.get(username="newadmin")
        assert user.role == "admin"
        assert user.org_id == org_b.id

        # Promote target to superAdmin
        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {
                "email": "target@example.net",
                "role": "superAdmin",
                "org": str(org_b.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        target.refresh_from_db()
        assert target.role == "superAdmin"


class TestAuditTargetOrg:
    """AC-20 — create/update/delete audit rows carry target user's org."""

    def test_create_audit_carries_target_org(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_user("super", org, role="superAdmin")
        client = _ui_client(superadmin)

        AuditLog.objects.all().delete()
        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "newuser",
                "email": "new@example.net",
                "password": "ComplexPass123!",
                "role": "analyst",
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302

        log = AuditLog.objects.get(action="user.created")
        assert log.target_org_id == org.id

    def test_update_audit_carries_target_org(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_user("super", org, role="superAdmin")
        target = _mk_user("target", org)
        client = _ui_client(superadmin)

        AuditLog.objects.all().delete()
        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {
                "email": "changed@example.net",
                "role": "analyst",
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302

        log = AuditLog.objects.get(action="user.updated")
        assert log.target_org_id == org.id

    def test_delete_audit_carries_target_org(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_user("super", org, role="superAdmin")
        target = _mk_user("target", org)
        client = _ui_client(superadmin)

        AuditLog.objects.all().delete()
        target_id = str(target.id)
        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": target_id}))
        assert resp.status_code == 302

        log = AuditLog.objects.get(action="user.deleted")
        assert log.target_org_id == org.id
        assert log.target_id == target_id

    def test_admin_audit_carries_own_org_as_target_org(self, db: None) -> None:
        """Admin mutations audit with their own org as target_org."""
        org = _mk_org("Org1")
        admin = _mk_user("admin", org, role="admin")
        target = _mk_user("target", org)
        client = _ui_client(admin)

        AuditLog.objects.all().delete()
        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {
                "email": "changed@example.net",
                "role": "analyst",
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302

        log = AuditLog.objects.get(action="user.updated")
        assert log.target_org_id == org.id
        assert log.actor_org_id == org.id  # Same for admin

    def test_superadmin_cross_org_audit_shows_actor_vs_target_org(self, db: None) -> None:
        """SuperAdmin cross-org mutation: actor_org != target_org in audit row."""
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        superadmin = _mk_user("super", org_a, role="superAdmin")
        client = _ui_client(superadmin)

        AuditLog.objects.all().delete()
        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "crossuser",
                "email": "cross@orgb.net",
                "password": "ComplexPass123!",
                "role": "analyst",
                "org": str(org_b.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302

        log = AuditLog.objects.get(action="user.created")
        assert log.actor_org_id == org_a.id
        assert log.target_org_id == org_b.id
        assert log.actor_org_id != log.target_org_id


class TestNavUnorphan:
    """AC-18 — Users tab/rail present for admin+superAdmin, absent for analyst/viewer."""

    def _make_client_for_role(self, role: str) -> tuple[Client, Organisation, User]:
        org = _mk_org(f"Org-{role}")
        user = _mk_user(f"user-{role}", org, role=role)
        client = Client(SERVER_NAME="localhost", HTTP_HOST="localhost")
        client.force_login(user)
        return client, org, user

    def test_tab_present_for_admin(self, db: None) -> None:
        client, _, _ = self._make_client_for_role("admin")
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'href="/settings/users"' in body
        assert "fa-users" in body
        assert "Users" in body

    def test_tab_present_for_superadmin(self, db: None) -> None:
        client, _, _ = self._make_client_for_role("superAdmin")
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'href="/settings/users"' in body
        assert "fa-users" in body
        assert "Users" in body

    def test_tab_absent_for_analyst(self, db: None) -> None:
        client, _, _ = self._make_client_for_role("analyst")
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 404  # Page itself is 404 for analyst
        # Can't check tab because page is 404, but the template would not render it
        # The role gate is tested in TestRoleGates

    def test_tab_absent_for_viewer(self, db: None) -> None:
        client, _, _ = self._make_client_for_role("viewer")
        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 404

    def test_rail_entry_present_for_admin_on_settings_page(self, db: None) -> None:
        """Rail submenu shows Users for admin on any settings sub-page."""
        client, _, _ = self._make_client_for_role("admin")
        # Visit settings root (which should redirect or show something)
        resp = client.get(reverse("ui-settings"))
        # The rail is in base.html; check if Users link appears in the rail submenu
        # Since admin can't see Organisations, the rail should show Users and Sources
        body = resp.content.decode()
        # The rail entry is only shown when the Settings parent is expanded (on settings pages)
        # Let's check on the sources page which admin can access
        resp = client.get(reverse("ui-settings-sources"))
        assert resp.status_code == 200
        body = resp.content.decode()
        # Rail submenu should contain Users link
        assert 'href="/settings/users"' in body
        assert "fa-users" in body
        assert "Users" in body

    def test_rail_entry_present_for_superadmin_on_settings_page(self, db: None) -> None:
        client, _, _ = self._make_client_for_role("superAdmin")
        resp = client.get(reverse("ui-settings-sources"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'href="/settings/users"' in body
        assert "fa-users" in body
        assert "Users" in body

    def test_rail_entry_absent_for_analyst_on_settings_page(self, db: None) -> None:
        # Non-vacuous negative: render a page an analyst CAN see (the rail lives in base.html),
        # and assert the Users entry is genuinely absent there — not merely that settings 404s.
        client, _, _ = self._make_client_for_role("analyst")
        resp = client.get(reverse("ui-case-list"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'href="/settings/users"' not in body
        assert "fa-users" not in body

    def test_rail_entry_absent_for_viewer_on_settings_page(self, db: None) -> None:
        client, _, _ = self._make_client_for_role("viewer")
        resp = client.get(reverse("ui-case-list"))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert 'href="/settings/users"' not in body
        assert "fa-users" not in body
