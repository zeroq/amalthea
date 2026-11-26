"""Settings UI conformance tests for automatic ingest-time bundling (12.3-fu1 / AC-P2.1-P2.3)."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from cases.models import CorrelationRule
from identity.models import AuditLog, Organisation, User
from ingest.models import IngestionSource


def _mk_org(name: str) -> Organisation:
    return Organisation.objects.create(name=name)


def _mk_user(login: str, org: Organisation, role: str = "admin") -> User:
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


def _url(org_id) -> str:
    return reverse("ui-settings-correlation-org", kwargs={"organisation_id": org_id})


def _update_url(org_id) -> str:
    return reverse("ui-settings-correlation-update", kwargs={"organisation_id": org_id})


@pytest.mark.django_db
class TestCorrelationSettingsGating:
    def test_admin_own_org_can_view_and_lazy_creates_disabled_rule(self) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))

        response = client.get(_url(org.id))

        assert response.status_code == 200
        rule = CorrelationRule.objects.get(organisation=org)
        assert rule.enabled is False
        assert rule.match_mode == "correlation_key"
        assert rule.window_minutes == 10
        assert "automatic alert bundling" in response.content.decode().lower()

    def test_short_redirect_uses_own_org(self) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))

        response = client.get(reverse("ui-settings-correlation"))

        assert response.status_code == 302
        assert response.url == _url(org.id)

    @pytest.mark.parametrize("role", ["analyst", "viewer"])
    def test_non_admin_gets_404(self, role: str) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user(role, org, role=role))

        assert client.get(_url(org.id)).status_code == 404
        assert client.post(_update_url(org.id), {}).status_code == 404

    def test_cross_org_admin_gets_404(self) -> None:
        org_a = _mk_org("Org A")
        org_b = _mk_org("Org B")
        client = _ui_client(_mk_user("admin_a", org_a))

        assert client.get(_url(org_b.id)).status_code == 404
        assert client.post(_update_url(org_b.id), {}).status_code == 404

    def test_superadmin_can_edit_explicit_org(self) -> None:
        admin_org = _mk_org("Admin org")
        target_org = _mk_org("Target org")
        client = _ui_client(_mk_user("super", admin_org, role="superAdmin"))

        assert client.get(_url(target_org.id)).status_code == 200


@pytest.mark.django_db
class TestCorrelationSettingsUpdate:
    def test_valid_update_persists_and_audits(self) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))

        response = client.post(
            _update_url(org.id),
            {"enabled": "on", "match_mode": "shared_observable", "window_minutes": "15"},
        )

        assert response.status_code == 302
        rule = CorrelationRule.objects.get(organisation=org)
        assert rule.enabled is True
        assert rule.match_mode == "shared_observable"
        assert rule.window_minutes == 15
        audit = AuditLog.objects.get(action="case.correlation_rule_changed")
        assert audit.target_org_id == org.id
        assert audit.details["old"] == {
            "enabled": False,
            "match_mode": "correlation_key",
            "window_minutes": 10,
        }
        assert audit.details["new"] == {
            "enabled": True,
            "match_mode": "shared_observable",
            "window_minutes": 15,
        }
        assert audit.details["break_glass"] is False

    def test_no_op_does_not_audit(self) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))

        response = client.post(
            _update_url(org.id),
            {"match_mode": "correlation_key", "window_minutes": "10"},
        )

        assert response.status_code == 302
        assert not AuditLog.objects.filter(action="case.correlation_rule_changed").exists()

    @pytest.mark.parametrize("window", ["0", "61", "abc", ""])
    def test_invalid_window_writes_nothing(self, window: str) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))
        client.get(_url(org.id))

        response = client.post(
            _update_url(org.id),
            {"enabled": "on", "match_mode": "correlation_key", "window_minutes": window},
        )

        assert response.status_code == 200
        assert "between 1 and 60" in response.content.decode()
        rule = CorrelationRule.objects.get(organisation=org)
        assert rule.enabled is False
        assert rule.window_minutes == 10
        assert not AuditLog.objects.filter(action="case.correlation_rule_changed").exists()

    def test_invalid_match_mode_writes_nothing(self) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))
        client.get(_url(org.id))

        response = client.post(
            _update_url(org.id),
            {"match_mode": "not-a-mode", "window_minutes": "10"},
        )

        assert response.status_code == 200
        assert "valid correlation match mode" in response.content.decode()
        rule = CorrelationRule.objects.get(organisation=org)
        assert rule.match_mode == "correlation_key"
        assert not AuditLog.objects.filter(action="case.correlation_rule_changed").exists()

    def test_superadmin_cross_org_change_is_break_glass(self) -> None:
        admin_org = _mk_org("Admin org")
        target_org = _mk_org("Target org")
        client = _ui_client(_mk_user("super", admin_org, role="superAdmin"))

        response = client.post(
            _update_url(target_org.id),
            {"enabled": "on", "match_mode": "correlation_key", "window_minutes": "5"},
        )

        assert response.status_code == 302
        audit = AuditLog.objects.get(action="case.correlation_rule_changed")
        assert audit.actor_org_id == admin_org.id
        assert audit.target_org_id == target_org.id
        assert audit.details["break_glass"] is True
        assert "secret" not in str(audit.details).lower()

    def test_enabled_rule_shows_warning_banner(self) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))
        CorrelationRule.objects.create(organisation=org, enabled=True)

        response = client.get(_url(org.id))

        assert response.status_code == 200
        assert "automatic case mutation is enabled" in response.content.decode().lower()


@pytest.mark.django_db
class TestSourceFlagHelpText:
    def test_source_pages_explain_correlation_kill_switch(self) -> None:
        org = _mk_org("Own org")
        client = _ui_client(_mk_user("admin", org))
        source = IngestionSource.objects.create(
            organisation=org, slug="feed", name="Feed", correlation_enabled=False
        )

        list_html = client.get(reverse("ui-settings-sources")).content.decode().lower()
        detail_html = (
            client.get(reverse("ui-settings-source-detail", kwargs={"source_id": source.id}))
            .content.decode()
            .lower()
        )

        assert "per-feed kill switch for automatic alert bundling" in list_html
        assert "per-feed kill switch for automatic alert bundling" in detail_html
