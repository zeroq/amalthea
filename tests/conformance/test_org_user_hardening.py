"""Hardening regression tests for SECURITY-2026-10-11 (findings F5-F9).

- F5: last-active-superAdmin mutations are serialised and the guards still fire
- F6: PATCH/DELETE /api/v1/user/{idOrLogin} is a uniform 404 (no existence oracle)
- F7: Settings UI POSTs with malformed ids / oversize names do not 500
- F8: API is_active rejects non-boolean strings instead of truthy-coercing
- F9: IngestionToken.secret_hash is not displayed in the Django admin
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import Client, RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from identity.admin import IngestionTokenAdmin
from identity.models import AuditLog, IngestionToken, Organisation, User
from identity.services import (
    delete_organisation,
    delete_user,
    lock_active_superadmins,
    serializes_superadmin_changes,
    update_user,
)


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "analyst", is_active: bool = True) -> User:
    return User.objects.create(
        login=login,
        username=login,
        email=f"{login}@example.net",
        org=org,
        role=role,
        is_active=is_active,
    )


def _api(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _ui(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


# --------------------------------------------------------------------------
# F5 — the last-active-superAdmin invariant survives the lock wrapper
# --------------------------------------------------------------------------


def test_lock_helper_counts_active_superadmins(db: None) -> None:
    org = _mk_org("Org")
    _mk_user("super", org, role="superAdmin")
    _mk_user("inactive", org, role="superAdmin", is_active=False)
    _mk_user("analyst", org)
    with transaction.atomic():
        assert lock_active_superadmins() == 1


def test_delete_user_service_still_guards_last_superadmin(db: None) -> None:
    """The @locks_active_superadmins wrapper must not defuse the guard it wraps."""
    org = _mk_org("Org")
    actor = _mk_user("actor", org, role="superAdmin", is_active=False)
    target = _mk_user("target", org, role="superAdmin")  # the only *active* superAdmin

    with pytest.raises(ValidationError) as exc:
        delete_user(target, actor=actor, request=None)

    assert exc.value.message_dict.get("user") == ["last_superadmin"]
    assert User.objects.filter(pk=target.pk).exists()


def test_delete_user_service_still_works_and_audits(db: None) -> None:
    """The wrapper (atomic + lock) must not break a permitted deletion."""
    org = _mk_org("Org")
    actor = _mk_user("actor", org, role="superAdmin")
    target = _mk_user("target", org)

    AuditLog.objects.all().delete()
    request = RequestFactory().post("/settings/users")
    delete_user(target, actor=actor, request=request)

    assert not User.objects.filter(pk=target.pk).exists()
    assert AuditLog.objects.filter(action="user.deleted").count() == 1


def test_delete_user_lock_wrapper_acquires_superadmin_lock(db: None, monkeypatch) -> None:
    """F5 — the wrapper must actually *take* the lock, not merely run the guard.

    SQLite treats ``SELECT ... FOR UPDATE`` as a no-op, so the existing guard tests
    cannot prove the serialisation is wired in. Asserting the lock helper is invoked
    keeps that wiring honest on the SQLite inner loop; the lock itself is exercised on
    the PostgreSQL deployment target.
    """
    calls: list[int] = []
    monkeypatch.setattr("identity.services.lock_active_superadmins", lambda: calls.append(1) or 1)
    org = _mk_org("Org")
    actor = _mk_user("actor", org, role="superAdmin")
    target = _mk_user("target", org)
    request = RequestFactory().post("/settings/users")

    delete_user(target, actor=actor, request=request)

    assert calls == [1]


def test_update_user_lock_wrapper_acquires_superadmin_lock(db: None, monkeypatch) -> None:
    """F5 — ``update_user`` (demote/deactivate path) is serialised the same way."""
    calls: list[int] = []
    monkeypatch.setattr("identity.services.lock_active_superadmins", lambda: calls.append(1) or 1)
    org = _mk_org("Org")
    actor = _mk_user("actor", org, role="superAdmin")
    target = _mk_user("target", org)
    request = RequestFactory().post("/settings/users")

    update_user(target, email="changed@example.net", actor=actor, request=request)

    assert calls == [1]


def test_delete_organisation_lock_wrapper_acquires_superadmin_lock(db: None, monkeypatch) -> None:
    """F5 — org delete serialises too: `members="delete"` can drop superAdmin rows.

    The F1 last-superAdmin guard in `delete_organisation` is the same check-then-act
    shape as the user paths, so it must take the same lock or two concurrent deletes
    could each remove the other's admin.
    """
    calls: list[int] = []
    monkeypatch.setattr("identity.services.lock_active_superadmins", lambda: calls.append(1) or 1)
    home = _mk_org("Home")
    actor = _mk_user("actor", home, role="superAdmin")
    doomed = _mk_org("Doomed")

    delete_organisation(
        doomed, members=None, target=None, actor=actor, request=RequestFactory().post("/settings")
    )

    assert calls == [1]


def test_http_lock_wrapper_locks_mutations_only(db: None, monkeypatch) -> None:
    """F5 — the view decorator locks POST/PATCH/PUT/DELETE but not read-only GETs."""
    from django.http import HttpResponse

    calls: list[int] = []
    monkeypatch.setattr("identity.services.lock_active_superadmins", lambda: calls.append(1) or 1)

    @serializes_superadmin_changes
    def _view(request):
        return HttpResponse("ok")

    factory = RequestFactory()
    assert _view(factory.get("/")).status_code == 200
    assert calls == []  # a settings page load (GET) must not take row locks
    assert _view(factory.post("/")).status_code == 200
    assert calls == [1]


# --------------------------------------------------------------------------
# F6 — uniform 404 on mutating user-detail
# --------------------------------------------------------------------------


def test_mutating_user_detail_404_is_uniform(db: None) -> None:
    org = _mk_org("Org")
    outsider = _mk_user("outsider", org)
    target = _mk_user("target", org)
    client = _api(outsider)

    existing = client.patch(f"/api/v1/user/{target.id}", {"role": "admin"}, format="json")
    missing = client.patch(f"/api/v1/user/{uuid4()}", {"role": "admin"}, format="json")

    assert existing.status_code == missing.status_code == 404
    # Identical body: a differing message would be an existence oracle.
    assert existing.json() == missing.json()


def test_mutating_user_detail_delete_404_is_uniform(db: None) -> None:
    """F6 — DELETE (not just PATCH) must be a uniform 404 body."""
    org = _mk_org("Org")
    outsider = _mk_user("outsider", org)
    target = _mk_user("target", org)
    client = _api(outsider)

    existing = client.delete(f"/api/v1/user/{target.id}")
    missing = client.delete(f"/api/v1/user/{uuid4()}")

    assert existing.status_code == missing.status_code == 404
    assert existing.json() == missing.json()


# --------------------------------------------------------------------------
# F8 — is_active must be a real boolean
# --------------------------------------------------------------------------


def test_api_patch_is_active_string_false_is_accepted_as_false(db: None) -> None:
    """An explicit "false" must mean False — not the truthy bool("false") bug (F8)."""
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    target = _mk_user("target", org)
    client = _api(superadmin)

    resp = client.patch(f"/api/v1/user/{target.id}", {"is_active": "false"}, format="json")

    assert resp.status_code == 204
    target.refresh_from_db()
    assert target.is_active is False


def test_api_patch_is_active_true_string_is_accepted(db: None) -> None:
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    target = _mk_user("target", org, is_active=False)
    client = _api(superadmin)

    resp = client.patch(f"/api/v1/user/{target.id}", {"is_active": "true"}, format="json")

    assert resp.status_code == 204
    target.refresh_from_db()
    assert target.is_active is True


def test_api_patch_is_active_invalid_value_is_rejected(db: None) -> None:
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    target = _mk_user("target", org)
    client = _api(superadmin)

    resp = client.patch(f"/api/v1/user/{target.id}", {"is_active": "yes"}, format="json")

    assert resp.status_code == 400
    assert "is_active" in resp.json()["fields"]
    target.refresh_from_db()
    assert target.is_active is True


def test_api_create_is_active_invalid_value_is_rejected(db: None) -> None:
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    client = _api(superadmin)

    resp = client.post(
        "/api/v1/user",
        {
            "username": "newuser",
            "email": "new@example.com",
            "org": str(org.id),
            "password": "Str0ng-Pass-2026!",
            "is_active": "yes",
        },
        format="json",
    )

    assert resp.status_code == 400
    assert "is_active" in resp.json()["fields"]
    assert not User.objects.filter(login="newuser").exists()


# --------------------------------------------------------------------------
# F7 — Settings UI validates ids / names instead of raising
# --------------------------------------------------------------------------


def test_ui_user_create_malformed_org_does_not_500(db: None) -> None:
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    client = _ui(superadmin)

    resp = client.post(
        reverse("ui-settings-users"),
        {
            "username": "newuser",
            "email": "new@example.com",
            "password": "Str0ng-Pass-2026!",
            "role": "analyst",
            "org": "not-a-uuid",
        },
    )

    assert resp.status_code == 200  # re-renders the list with an error, not a 500
    assert not User.objects.filter(login="newuser").exists()


def test_ui_org_delete_malformed_target_does_not_500(db: None) -> None:
    org = _mk_org("Org")
    target = _mk_org("Target")
    superadmin = _mk_user("super", org, role="superAdmin")
    _mk_user("member", target)
    client = _ui(superadmin)

    resp = client.post(
        reverse("ui-settings-organisations-delete", kwargs={"org_id": target.id}),
        {"members": "reassign", "target_org": "not-a-uuid"},
    )

    assert resp.status_code == 302
    assert Organisation.objects.filter(pk=target.pk).exists()


def test_ui_org_create_oversize_name_collision_does_not_500(db: None) -> None:
    Organisation.objects.create(name="x" * 200)
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    client = _ui(superadmin)

    resp = client.post(reverse("ui-settings-organisations"), {"name": "x" * 201})

    assert resp.status_code == 200  # error message, not IntegrityError/500
    assert Organisation.objects.filter(name="x" * 200).count() == 1


def test_ui_org_edit_oversize_name_collision_does_not_500(db: None) -> None:
    """F7 — the *edit* path truncates before the uniqueness pre-check too."""
    Organisation.objects.create(name="x" * 200)
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    client = _ui(superadmin)

    resp = client.post(
        reverse("ui-settings-organisations-edit", kwargs={"org_id": org.id}),
        {"name": "x" * 201},  # truncates to collide with the existing 200-char name
    )

    assert resp.status_code == 200  # error message, not IntegrityError/500
    org.refresh_from_db()
    assert org.name == "Org"
    assert Organisation.objects.filter(name="x" * 200).count() == 1


def test_ui_user_edit_malformed_org_does_not_500(db: None) -> None:
    """F7 — the edit-user path resolves the org field without raising on garbage."""
    org = _mk_org("Org")
    superadmin = _mk_user("super", org, role="superAdmin")
    target = _mk_user("target", org)
    client = _ui(superadmin)

    resp = client.post(
        reverse("ui-settings-users-edit", kwargs={"user_id": target.id}),
        {"email": target.email, "role": "analyst", "org": "not-a-uuid", "is_active": "on"},
    )

    assert resp.status_code == 200  # re-renders with an error, not a 500
    target.refresh_from_db()
    assert target.org_id == org.id


# --------------------------------------------------------------------------
# F9 — the token hash stays out of the admin
# --------------------------------------------------------------------------


def test_admin_does_not_display_secret_hash(db: None) -> None:
    assert "secret_hash" not in IngestionTokenAdmin.readonly_fields

    # Removing it from `readonly_fields` alone is NOT "hidden": the model field is
    # editable (`editable=True`) and the admin sets no `fields`/`exclude`, so Django
    # renders it as a writable input. Plan §7 requires the hash excluded everywhere.
    from django.contrib import admin as django_admin

    model_admin = IngestionTokenAdmin(IngestionToken, django_admin.site)
    request = RequestFactory().get("/admin/identity/ingestiontoken/1/change/")
    request.user = User(is_superuser=True, is_staff=True)
    assert "secret_hash" not in model_admin.get_fields(request)
    assert "secret_hash" not in model_admin.get_form(request).base_fields
