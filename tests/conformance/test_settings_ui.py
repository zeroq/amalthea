"""Conformance tests for the Settings UI (O4).

Tests:
- Nav hidden for non-superAdmin; routes 404 for non-superAdmin
- Full CRUD on both pages via UI (HTMX and no-HTMX)
- Destructive actions use <dialog> (Esc/Tab-trap), never native confirm()
- Org-delete modal shows counts, disables confirm when blocked, offers reassign-or-delete for members
- Keyboard shortcut + cheat-sheet updated
"""

from __future__ import annotations

import re
from uuid import uuid4

from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import AuditLog, Organisation, User
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


def _mk_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _mk_superadmin(org: Organisation) -> User:
    return _mk_user("super", org, role="superAdmin")


def _mk_analyst(org: Organisation) -> User:
    return _mk_user("analyst", org, role="analyst")


def _mk_case(title: str, org: Organisation) -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title=title, status=status, owner_org=org)


def _mk_alert(title: str, org: Organisation) -> Alert:
    status = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[
        0
    ]
    return Alert.objects.create(
        type="test",
        source="test",
        source_ref=str(uuid4()),
        title=title,
        status=status,
        owner_org=org,
    )


def _mk_observable(data: str, org: Organisation) -> Observable:
    otype = ObservableType.objects.get_or_create(name="ip", defaults={"is_case_sensitive": False})[
        0
    ]
    return Observable.objects.create(
        data_type=otype, data=data, normalized_data=data, owner_org=org
    )


class TestSettingsNav:
    """Settings nav visibility and route access."""

    def test_nav_hidden_for_non_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        client = _mk_client(analyst)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        content = resp.content.decode()
        # Settings link should not be in the rail for non-superAdmin
        assert "/settings/organisations" not in content
        assert "Settings" not in content

    def test_nav_visible_for_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-dashboard"))
        assert resp.status_code == 200
        content = resp.content.decode()
        # Settings link should be in the rail for superAdmin (now points to /settings)
        assert "/settings" in content
        assert "Settings" in content

    def test_settings_home_redirects_superadmin_to_organisations(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _mk_client(_mk_superadmin(org))

        resp = client.get(reverse("ui-settings"))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("ui-settings-organisations")

    def test_settings_home_redirects_admin_to_sources(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _mk_client(_mk_user("admin1", org, role="admin"))

        resp = client.get(reverse("ui-settings"))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("ui-settings-sources")

    def test_rail_submenu_expands_on_settings_routes(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _mk_client(_mk_superadmin(org))

        content = client.get(reverse("ui-settings-organisations")).content.decode()
        # Non-vacuous: both sub-items are present inside the nested rail list.
        assert 'class="rail-sub"' in content
        assert reverse("ui-settings-organisations") in content
        assert reverse("ui-settings-sources") in content

    def test_rail_submenu_is_collapsed_outside_settings(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _mk_client(_mk_superadmin(org))

        content = client.get(reverse("ui-dashboard")).content.decode()
        assert 'class="rail-sub"' not in content

    def test_rail_submenu_hides_organisations_from_admin(self, db: None) -> None:
        org = _mk_org("Org1")
        client = _mk_client(_mk_user("admin1", org, role="admin"))

        content = client.get(reverse("ui-settings-sources")).content.decode()
        assert 'class="rail-sub"' in content
        assert reverse("ui-settings-sources") in content
        assert reverse("ui-settings-organisations") not in content

    def test_organisations_route_404_for_non_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        client = _mk_client(analyst)

        resp = client.get(reverse("ui-settings-organisations"))
        assert resp.status_code == 404

    def test_users_route_404_for_non_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        client = _mk_client(analyst)

        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 404

    def test_organisations_edit_route_404_for_non_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        client = _mk_client(analyst)

        resp = client.get(reverse("ui-settings-organisations-edit", kwargs={"org_id": org.id}))
        assert resp.status_code == 404

    def test_users_edit_route_404_for_non_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        analyst = _mk_analyst(org)
        user = _mk_user("target", org)
        client = _mk_client(analyst)

        resp = client.get(reverse("ui-settings-users-edit", kwargs={"user_id": user.id}))
        assert resp.status_code == 404


class TestSettingsTabStrip:
    """The settings tab strip mirrors the case-detail strip.

    `case_detail.html` is the layout role model: the strip is the first thing in
    ``{% block content %}`` (immediately under the page heading) and ``.tabs`` holds the tab links as
    its *direct* children. A ``<ul>``/``<li>`` wrapper puts the ``<a>`` inside a block box, which
    breaks the active tab's overlap and pushes the strip below the page's content.
    """

    def _content(self, client: Client, url_name: str) -> str:
        resp = client.get(reverse(url_name))
        assert resp.status_code == 200
        return resp.content.decode()

    def test_strip_sits_under_the_heading_and_above_the_content_card(self, db: None) -> None:
        client = _mk_client(_mk_superadmin(_mk_org("Org1")))

        for url_name in (
            "ui-settings-organisations",
            "ui-settings-sources",
            "ui-settings-users",
        ):
            content = client.get(reverse(url_name)).content.decode()
            heading = content.index("<h1")
            strip = content.index('aria-label="Settings sections"')
            card = content.index('<div class="card"')
            assert heading < strip < card, (
                f"{url_name}: the settings strip must render under the page heading and above the "
                "content card (case-detail position)"
            )

    def test_strip_uses_the_case_detail_markup(self, db: None) -> None:
        client = _mk_client(_mk_superadmin(_mk_org("Org1")))
        content = client.get(reverse("ui-settings-sources")).content.decode()

        assert '<nav class="tabs" aria-label="Settings sections">' in content
        # No `<ul>`/`<li>` wrapper and none of the old unstyled wrapper class.
        assert '<ul class="tabs"' not in content
        assert 'role="presentation"' not in content
        assert "settings-nav" not in content

    def test_active_tab_carries_tab_on_and_aria_current(self, db: None) -> None:
        client = _mk_client(_mk_superadmin(_mk_org("Org1")))
        content = client.get(reverse("ui-settings-sources")).content.decode()

        match = re.search(r'<a class="tab tab-on"[^>]*aria-current="page"', content)
        assert match is not None, (
            'the active settings tab must be marked with both `tab-on` and `aria-current="page"`'
        )
        assert reverse("ui-settings-sources") in match.group(0)

    def test_strip_tabs_are_filtered_by_role(self, db: None) -> None:
        admin = _mk_client(_mk_user("admin1", _mk_org("Org1"), role="admin"))
        content = admin.get(reverse("ui-settings-sources")).content.decode()

        assert reverse("ui-settings-sources") in content
        assert reverse("ui-settings-organisations") not in content


class TestOrganisationsCRUD:
    """Organisations CRUD via UI."""

    def test_list_organisations(self, db: None) -> None:
        org1 = _mk_org("Org Alpha")
        _mk_org("Org Beta")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-organisations"))
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "Org Alpha" in content
        assert "Org Beta" in content

    def test_create_organisation(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.post(
            reverse("ui-settings-organisations"),
            {"name": "New Org", "description": "Description here"},
        )
        assert resp.status_code == 302
        assert Organisation.objects.filter(name="New Org").exists()

    def test_create_organisation_duplicate_name(self, db: None) -> None:
        org1 = _mk_org("Org1")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        resp = client.post(
            reverse("ui-settings-organisations"),
            {"name": "Org1", "description": "Duplicate"},
        )
        assert resp.status_code == 200  # Re-renders with error
        content = resp.content.decode()
        assert "already exists" in content

    def test_edit_organisation(self, db: None) -> None:
        org = _mk_org("Old Name")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        # GET form
        resp = client.get(reverse("ui-settings-organisations-edit", kwargs={"org_id": org.id}))
        assert resp.status_code == 200

        # POST update
        resp = client.post(
            reverse("ui-settings-organisations-edit", kwargs={"org_id": org.id}),
            {"name": "New Name", "description": "Updated"},
        )
        assert resp.status_code == 302
        org.refresh_from_db()
        assert org.name == "New Name"
        assert org.description == "Updated"

    def test_delete_organisation_empty(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("ToDelete")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        resp = client.post(reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id}))
        assert resp.status_code == 302
        assert not Organisation.objects.filter(pk=org2.id).exists()

    def test_delete_organisation_blocked_by_cases(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasCase")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        _mk_case("Test Case", org2)

        resp = client.post(reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id}))
        assert resp.status_code == 302  # Redirects back with error message
        assert Organisation.objects.filter(pk=org2.id).exists()

        # Check error message in redirected page
        resp2 = client.get(reverse("ui-settings-organisations"))
        content = resp2.content.decode()
        assert "owns tenant data" in content
        assert "case" in content.lower()

    def test_delete_organisation_blocked_by_alerts(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasAlert")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        _mk_alert("Test Alert", org2)

        resp = client.post(reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id}))
        assert resp.status_code == 302
        assert Organisation.objects.filter(pk=org2.id).exists()

    def test_delete_organisation_blocked_by_observables(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasObservable")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        _mk_observable("10.0.0.1", org2)

        resp = client.post(reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id}))
        assert resp.status_code == 302
        assert Organisation.objects.filter(pk=org2.id).exists()

    def test_delete_organisation_with_members_reassign(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasMembers")
        org3 = _mk_org("TargetOrg")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        member = _mk_user("member", org2)

        resp = client.post(
            reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id}),
            {"members": "reassign", "target_org": str(org3.id)},
        )
        assert resp.status_code == 302
        assert not Organisation.objects.filter(pk=org2.id).exists()
        member.refresh_from_db()
        assert member.org_id == org3.id

    def test_delete_organisation_reassign_invalid_target_blocked(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasMembers")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        member = _mk_user("member", org2)
        url = reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id})

        # No target supplied → must not orphan the members.
        resp = client.post(url, {"members": "reassign"})
        assert resp.status_code == 302
        assert Organisation.objects.filter(pk=org2.id).exists()
        member.refresh_from_db()
        assert member.org_id == org2.id

        # Target that does not exist → same guard.
        resp = client.post(
            url,
            {"members": "reassign", "target_org": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 302
        assert Organisation.objects.filter(pk=org2.id).exists()
        member.refresh_from_db()
        assert member.org_id == org2.id

        # Target == the org being deleted → same guard.
        resp = client.post(url, {"members": "reassign", "target_org": str(org2.id)})
        assert resp.status_code == 302
        assert Organisation.objects.filter(pk=org2.id).exists()
        member.refresh_from_db()
        assert member.org_id == org2.id

    def test_delete_organisation_with_members_delete(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasMembers")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        member = _mk_user("member", org2)

        resp = client.post(
            reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id}),
            {"members": "delete"},
        )
        assert resp.status_code == 302
        assert not Organisation.objects.filter(pk=org2.id).exists()
        assert not User.objects.filter(pk=member.id).exists()

    def test_delete_organisation_with_members_no_action_blocked(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasMembers")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        _mk_user("member", org2)

        resp = client.post(reverse("ui-settings-organisations-delete", kwargs={"org_id": org2.id}))
        assert resp.status_code == 302
        assert Organisation.objects.filter(pk=org2.id).exists()


class TestUsersCRUD:
    """Users CRUD via UI."""

    def test_list_users(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        _mk_user("user1", org)
        _mk_user("user2", org)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-users"))
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "user1" in content
        assert "user2" in content
        assert "super" in content

    def test_create_user(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

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
        user = User.objects.get(username="newuser")
        assert user.email == "new@example.net"
        assert user.role == "analyst"
        assert user.org_id == org.id
        assert user.is_active is True
        assert user.has_usable_password()

    def test_create_user_weak_password_rejected(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "weakpass",
                "email": "weak@example.net",
                "password": "123",  # Too weak
                "role": "analyst",
                "org": str(org.id),
            },
        )
        assert resp.status_code == 200  # Re-renders with error
        content = resp.content.decode()
        assert "password" in content.lower()

    def test_create_user_duplicate_username(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        _mk_user("existing", org)
        client = _mk_client(superadmin)

        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "existing",
                "email": "new@example.net",
                "password": "ComplexPass123!",
                "role": "analyst",
                "org": str(org.id),
            },
        )
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "already exists" in content

    def test_edit_user(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("Org2")
        superadmin = _mk_superadmin(org1)
        user = _mk_user("target", org1, role="analyst")
        client = _mk_client(superadmin)

        # GET form
        resp = client.get(reverse("ui-settings-users-edit", kwargs={"user_id": user.id}))
        assert resp.status_code == 200

        # POST update
        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": user.id}),
            {
                "email": "updated@example.net",
                "role": "admin",
                "org": str(org2.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.email == "updated@example.net"
        assert user.role == "admin"
        assert user.org_id == org2.id
        assert user.is_active is True

    def test_edit_user_password_reset(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        user = _mk_user("target", org)
        client = _mk_client(superadmin)

        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": user.id}),
            {"password": "NewComplexPass456!"},
        )
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.check_password("NewComplexPass456!")

    def test_edit_user_cannot_demote_self(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": superadmin.id}),
            {"role": "analyst"},
        )
        assert resp.status_code == 302
        superadmin.refresh_from_db()
        assert superadmin.role == "superAdmin"

    def test_edit_user_cannot_deactivate_self(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": superadmin.id}),
            {},  # is_active not checked = False
        )
        assert resp.status_code == 302
        superadmin.refresh_from_db()
        assert superadmin.is_active is True

    def test_delete_user(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        target = _mk_user("todelete", org)
        client = _mk_client(superadmin)

        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": target.id}))
        assert resp.status_code == 302
        assert not User.objects.filter(pk=target.id).exists()

    def test_delete_user_cannot_delete_self(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": superadmin.id}))
        assert resp.status_code == 302
        assert User.objects.filter(pk=superadmin.id).exists()

    def test_delete_user_cannot_delete_last_superadmin(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        other_super = _mk_user("othersuper", org, role="superAdmin")
        client = _mk_client(superadmin)

        # Delete the other superAdmin (leaving one)
        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": other_super.id}))
        assert resp.status_code == 302
        assert not User.objects.filter(pk=other_super.id).exists()

        # Now try to delete the last superAdmin (self)
        resp = client.post(reverse("ui-settings-users-delete", kwargs={"user_id": superadmin.id}))
        assert resp.status_code == 302
        assert User.objects.filter(pk=superadmin.id).exists()


class TestSettingsUIDialogs:
    """Verify modals use <dialog> not native confirm()."""

    def test_org_delete_uses_dialog(self, db: None) -> None:
        org1 = _mk_org("Org1")
        _mk_org("Org2")
        superadmin = _mk_superadmin(org1)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-organisations"))
        content = resp.content.decode()
        # Check for <dialog> modals
        assert "<dialog" in content
        assert 'data-modal-open="delete-org-' in content
        # No native confirm()
        assert "confirm(" not in content

    def test_user_delete_uses_dialog(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        _mk_user("target", org)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-users"))
        content = resp.content.decode()
        assert "<dialog" in content
        assert 'data-modal-open="delete-user-' in content
        assert "confirm(" not in content

    def test_org_create_uses_dialog(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-organisations"))
        content = resp.content.decode()
        assert 'data-modal-open="org-create-modal"' in content

    def test_user_create_uses_dialog(self, db: None) -> None:
        org = _mk_org("Org1")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-users"))
        content = resp.content.decode()
        assert 'data-modal-open="user-create-modal"' in content


class TestOrgDeleteModalBlockers:
    """Org delete modal shows blockers and disables confirm."""

    def test_modal_shows_case_blocker(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasCase")
        superadmin = _mk_superadmin(org1)
        _mk_case("Test", org2)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-organisations"))
        content = resp.content.decode()
        # Blockers now ship as row data attributes *and* as server-rendered modal text
        # (see test_delete_modal_blocker_is_rendered_server_side); no JS builds the body.
        assert 'data-cases="1"' in content
        assert 'data-alerts="0"' in content

    def test_modal_shows_members_reassign_option(self, db: None) -> None:
        org1 = _mk_org("Org1")
        org2 = _mk_org("HasMembers")
        _mk_org("Target")
        superadmin = _mk_superadmin(org1)
        _mk_user("member", org2)
        client = _mk_client(superadmin)

        resp = client.get(reverse("ui-settings-organisations"))
        content = resp.content.decode()
        assert 'data-members="1"' in content


class TestSettingsUIAudit:
    """F4: UI create/update must write an AuditLog row (parity with the API)."""

    def test_ui_create_organisation_audited(self, db: None) -> None:
        org = _mk_org("Base")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        AuditLog.objects.all().delete()
        resp = client.post(reverse("ui-settings-organisations"), {"name": "NewOrg"})
        assert resp.status_code == 302
        assert AuditLog.objects.filter(action="organisation.created").count() == 1

    def test_ui_edit_organisation_audited(self, db: None) -> None:
        org = _mk_org("Base")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        AuditLog.objects.all().delete()
        resp = client.post(
            reverse("ui-settings-organisations-edit", kwargs={"org_id": org.id}),
            {"name": "Renamed", "description": "x"},
        )
        assert resp.status_code == 302
        assert AuditLog.objects.filter(action="organisation.updated").count() == 1

    def test_ui_create_user_audited(self, db: None) -> None:
        org = _mk_org("Base")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        AuditLog.objects.all().delete()
        resp = client.post(
            reverse("ui-settings-users"),
            {
                "username": "newuser",
                "email": "new@example.com",
                "password": "Str0ng-Pass-2026!",
                "role": "analyst",
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        assert AuditLog.objects.filter(action="user.created").count() == 1

    def test_ui_edit_user_audited(self, db: None) -> None:
        org = _mk_org("Base")
        superadmin = _mk_superadmin(org)
        target = _mk_user("editme", org)
        client = _mk_client(superadmin)

        AuditLog.objects.all().delete()
        resp = client.post(
            reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
            {
                "email": "changed@example.com",
                "role": "analyst",
                "org": str(org.id),
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        assert AuditLog.objects.filter(action="user.updated").count() == 1


class TestKeyboardShortcuts:
    """Keyboard shortcuts and cheat-sheet."""

    def test_comma_shortcut_in_keys_js(self, db: None) -> None:
        from pathlib import Path

        keys_js = Path(__file__).resolve().parents[2] / "ui" / "static" / "ui" / "keys.js"
        content = keys_js.read_text()
        assert '",": "/settings"' in content

    def test_cheatsheet_has_settings_entry(self, db: None) -> None:
        from pathlib import Path

        keys_js = Path(__file__).resolve().parents[2] / "ui" / "static" / "ui" / "keys.js"
        content = keys_js.read_text()
        assert 'label: "Settings"' in content
        assert 'key: ","' in content


class TestSettingsUICspAndEscaping:
    """F3/F10 — CSP-safe, server-rendered Settings UI (no inline JS, no innerHTML)."""

    def _settings_pages(self, superadmin: User) -> list[str]:
        client = _mk_client(superadmin)
        return [
            client.get(reverse("ui-settings-organisations")).content.decode(),
            client.get(reverse("ui-settings-users")).content.decode(),
        ]

    def test_settings_pages_ship_no_inline_script_or_style(self, db: None) -> None:
        org = _mk_org("Org")
        superadmin = _mk_superadmin(org)

        for content in (
            _mk_client(superadmin).get(reverse("ui-settings-organisations")).content.decode(),
            _mk_client(superadmin).get(reverse("ui-settings-users")).content.decode(),
        ):
            # No inline script/style — the CSP would block them anyway (ADR-003).
            assert "<script>" not in content
            assert 'style="' not in content
        # The modal behaviour ships as external, CSP-allowed assets.
        assert "ui/modal.js" in content
        assert "ui/modal.css" in content

    def test_modals_do_not_build_html_from_server_data(self, db: None) -> None:
        org = _mk_org("Org")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        for url in (reverse("ui-settings-organisations"), reverse("ui-settings-users")):
            content = client.get(url).content.decode()
            assert "innerHTML" not in content
            assert "escapejs" not in content

    def test_org_name_with_markup_is_escaped(self, db: None) -> None:
        org = _mk_org("Org")
        Organisation.objects.create(name="<img src=x onerror=alert(1)>")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        content = client.get(reverse("ui-settings-organisations")).content.decode()

        assert "<img src=x" not in content
        assert "&lt;img src=x" in content

    def test_org_name_with_markup_is_escaped_on_users_page(self, db: None) -> None:
        """F3 — the users page renders org names into the table and the create-user
        <select>; both must auto-escape (the organisations page is covered above)."""
        org = _mk_org("Org")
        Organisation.objects.create(name="<script>alert(1)</script>")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        content = client.get(reverse("ui-settings-users")).content.decode()

        assert "<script>alert(1)</script>" not in content
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content

    def test_settings_pages_ship_no_inline_markup_or_handlers(self, db: None) -> None:
        """F10 — catch inline script/style/handlers, not just the literal `<script>`.

        The existing no-`<script>`-substring check would miss `<script type=...>` and
        inline event handlers. This version rejects any inline script tag, `<style>`
        blocks, `style=""` attributes and `on*=` handlers on the Settings pages.
        """
        import re

        org = _mk_org("Org")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        inline_script = re.compile(r"<script\b(?![^>]*\bsrc=)")
        inline_handler = re.compile(r"\son[a-z]+\s*=")

        for url in (reverse("ui-settings-organisations"), reverse("ui-settings-users")):
            content = client.get(url).content.decode()
            # External `<script src=...>` is CSP-allowed; an inline tag is not.
            assert inline_script.search(content) is None
            assert "<style" not in content
            assert " style=" not in content
            assert " style='" not in content
            assert inline_handler.search(content) is None

    def test_delete_modal_blocker_is_rendered_server_side(self, db: None) -> None:
        org = _mk_org("Org")
        has_case = _mk_org("HasCase")
        _mk_case("Test", has_case)
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        content = client.get(reverse("ui-settings-organisations")).content.decode()

        # The warning text (not just a data attribute) is present in the HTML.
        assert "Cannot delete:" in content
        assert "owns tenant data" in content
        assert "1 case" in content
        assert "data-modal-blocked" in content

    def test_create_modal_fields_are_rendered_server_side(self, db: None) -> None:
        org = _mk_org("Org")
        superadmin = _mk_superadmin(org)
        client = _mk_client(superadmin)

        orgs_html = client.get(reverse("ui-settings-organisations")).content.decode()
        assert 'name="name"' in orgs_html
        assert 'form="org-create-form"' in orgs_html

        users_html = client.get(reverse("ui-settings-users")).content.decode()
        assert 'name="username"' in users_html
        assert 'form="user-create-form"' in users_html
