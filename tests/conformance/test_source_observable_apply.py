"""Source page: preview + confirm applying observable rules to existing alerts (PLAN-2026-11-09)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertObservable, AlertStatus
from identity.models import AuditLog, Organisation, User
from ingest.models import IngestionSource
from observables.models import Observable, ObservableType

pytestmark = pytest.mark.django_db

MAPPING = {
    "version": 2,
    "fields": {"title": {"paths": ["$.name"]}},
    "observables": [{"type": "mail", "paths": ["$.email"]}],
}


def _status(value: str = "New", stage: str = "New") -> AlertStatus:
    status, _ = AlertStatus.objects.get_or_create(value=value, defaults={"stage": stage})
    return status


def _alert(source: IngestionSource, ref: str = "ref-1") -> Alert:
    return Alert.objects.create(
        title="Stored title",
        description="Stored description",
        summary="Stored summary",
        severity=2,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        type="phishing",
        source="wire-source",
        source_ref=ref,
        status=_status(),
        raw_payload={"name": "New title", "email": "analyst@example.test"},
        ingestion_source=source,
        correlation_key=f"kept-{ref}",
        owner_org=source.organisation,
    )


@pytest.fixture
def feed():
    org = Organisation.objects.create(name="Observable Apply UI Org")
    admin = User.objects.create(
        login="observable-apply-admin",
        username="observable-apply-admin",
        email="apply-admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )
    source = IngestionSource.objects.create(
        organisation=org,
        slug="observable-apply-feed",
        name="Observable apply feed",
        mapping_config=dict(MAPPING),
    )
    ObservableType.objects.get_or_create(name="mail", defaults={"name": "mail"})
    return org, admin, source


def _url(source: IngestionSource) -> str:
    return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _observable_post(**overrides: str) -> dict[str, str]:
    data = {"form": "observables"}
    for name in ObservableType.objects.order_by("name").values_list("name", flat=True):
        data[f"observable_paths__{name}"] = overrides.get(name, "")
    return data


def _preview(client: Client, source: IngestionSource, **overrides: str):
    data = _observable_post(**overrides)
    if not overrides:
        for rule in source.mapping_config.get("observables") or []:
            name = str(rule.get("type") or "")
            paths = rule.get("paths") or []
            if name and paths:
                data[f"observable_paths__{name}"] = "\n".join(paths)
    return client.post(_url(source), {**data, "intent": "apply"})


def _confirm(client: Client, source: IngestionSource, fields: dict):
    return client.post(
        _url(source),
        {"form": "observables", "intent": "apply_confirmed", **fields},
    )


class TestPreview:
    def test_it_writes_nothing_and_renders_the_modal(self, feed) -> None:
        _org, admin, source = feed
        _alert(source)
        updated_before = IngestionSource.objects.get(pk=source.pk).updated_at

        response = _preview(_client(admin), source)
        body = response.content.decode()

        assert response.status_code == 200
        assert response.context["observable_apply_pending"].candidates == 1
        assert response.context["apply_pending"] is None
        assert response.context["observable_apply_rows"] == [
            {"type": "mail", "found": 1, "new_links": 1, "existing_links": 0}
        ], response.context["observable_apply_pending"]
        assert 'id="observable-apply-modal"' in body
        assert 'data-autoopen="1"' in body
        assert 'name="intent" value="apply_confirmed"' in body
        assert 'name="form" value="observables"' in body
        assert "Save & extract" in body
        assert 'id="mapping-apply-modal"' not in body
        assert not AlertObservable.objects.exists()
        assert not Observable.objects.exists()
        assert not AuditLog.objects.exists()
        assert IngestionSource.objects.get(pk=source.pk).updated_at == updated_before
        assert IngestionSource.objects.get(pk=source.pk).mapping_config == MAPPING

    def test_an_invalid_rule_re_renders_and_writes_nothing(self, feed) -> None:
        _org, admin, source = feed

        response = _preview(_client(admin), source, mail="not-a-path")

        assert response.status_code == 200
        assert "JSONPath starts with $." in response.content.decode()
        assert not AuditLog.objects.exists()
        assert IngestionSource.objects.get(pk=source.pk).mapping_config == MAPPING


class TestConfirm:
    def test_it_saves_extracts_and_audits_counts(self, feed) -> None:
        org, admin, source = feed
        alert = _alert(source)
        client = _client(admin)
        preview = _preview(client, source, mail="$.email\n$.recipient")
        fields = preview.context["observable_apply_confirm_fields"]
        pending = json.loads(fields["pending_config"])

        response = _confirm(client, source, fields)

        assert response.status_code == 302
        source.refresh_from_db()
        assert source.mapping_config == pending
        link = AlertObservable.objects.get(alert=alert)
        assert link.added_by == admin
        log = AuditLog.objects.get(action="ingestion_source.observables_applied")
        assert log.actor == admin
        assert log.target_org == org
        assert log.details == {
            "changed": ["observables"],
            "alerts": {
                "candidates": 1,
                "with_values": 1,
                "new_links": 1,
                "existing_links": 0,
            },
            "types": {"mail": {"found": 1, "new": 1, "existing": 0}},
        }
        assert Alert.objects.get(pk=alert.pk).title == "Stored title"

    def test_a_second_confirm_adds_nothing(self, feed) -> None:
        _org, admin, source = feed
        _alert(source)
        client = _client(admin)
        fields = _preview(client, source).context["observable_apply_confirm_fields"]

        _confirm(client, source, fields)
        response = _confirm(client, source, fields)

        assert response.status_code == 302
        assert AlertObservable.objects.count() == 1
        logs = AuditLog.objects.filter(action="ingestion_source.observables_applied")
        assert logs.count() == 2
        assert logs.order_by("-created_at").first().details["alerts"]["new_links"] == 0
        assert logs.order_by("-created_at").first().details["alerts"]["existing_links"] == 1
        assert logs.order_by("-created_at").first().details["changed"] == []

    def test_a_tampered_config_that_changes_fields_is_refused(self, feed) -> None:
        _org, admin, source = feed
        _alert(source)
        client = _client(admin)
        fields = dict(_preview(client, source).context["observable_apply_confirm_fields"])
        config = json.loads(fields["pending_config"])
        config["fields"]["title"]["paths"] = ["$.email"]
        fields["pending_config"] = json.dumps(config)

        response = _confirm(client, source, fields)

        assert response.status_code == 200
        assert "Only observable rules may change in this form." in response.content.decode()
        assert IngestionSource.objects.get(pk=source.pk).mapping_config == MAPPING
        assert not AlertObservable.objects.exists()
        assert not AuditLog.objects.exists()

    def test_invalid_json_is_refused(self, feed) -> None:
        _org, admin, source = feed

        response = _confirm(
            _client(admin),
            source,
            {"pending_config": "{not-json"},
        )

        assert response.status_code == 200
        assert "Not valid JSON" in response.content.decode()
        assert not AuditLog.objects.exists()
        assert IngestionSource.objects.get(pk=source.pk).mapping_config == MAPPING

    def test_over_cap_writes_nothing(self, feed, monkeypatch: pytest.MonkeyPatch) -> None:
        from ingest import remap

        monkeypatch.setattr(remap, "MAX_REMAP_ALERTS", 1)
        _org, admin, source = feed
        _alert(source, "one")
        _alert(source, "two")
        client = _client(admin)
        fields = _preview(client, source).context["observable_apply_confirm_fields"]
        updated_before = IngestionSource.objects.get(pk=source.pk).updated_at

        response = _confirm(client, source, fields)

        assert response.status_code == 302
        assert IngestionSource.objects.get(pk=source.pk).updated_at == updated_before
        assert not AlertObservable.objects.exists()
        assert not AuditLog.objects.exists()

    def test_an_analyst_cannot_confirm(self, feed) -> None:
        org, _admin, source = feed
        analyst = User.objects.create(
            login="observable-apply-analyst",
            username="observable-apply-analyst",
            email="apply-analyst@example.net",
            org=org,
            role="analyst",
            is_active=True,
        )

        response = _confirm(
            _client(analyst),
            source,
            {"pending_config": json.dumps(MAPPING)},
        )

        assert response.status_code == 404
        assert not AuditLog.objects.exists()

    def test_a_missing_csrf_token_is_refused(self, feed) -> None:
        _org, admin, source = feed
        client = Client(enforce_csrf_checks=True)
        client.force_login(admin)

        response = _confirm(
            client,
            source,
            {"pending_config": json.dumps(MAPPING)},
        )

        assert response.status_code == 403
        assert not AuditLog.objects.exists()

    def test_immediate_save_still_does_not_extract(self, feed) -> None:
        _org, admin, source = feed
        _alert(source)

        response = _client(admin).post(
            _url(source),
            _observable_post(mail="$.recipient"),
        )

        assert response.status_code == 302
        assert AlertObservable.objects.count() == 0
        assert not AuditLog.objects.filter(action="ingestion_source.observables_applied").exists()
        assert AuditLog.objects.filter(action="ingestion_source.updated").exists()

    def test_no_candidates_still_saves_the_rules(self, feed) -> None:
        _org, admin, source = feed
        client = _client(admin)
        response = _preview(client, source)
        body = response.content.decode()
        assert "There are no unlinked open alerts to extract from." in body

        fields = response.context["observable_apply_confirm_fields"]
        _confirm(client, source, fields)

        source.refresh_from_db()
        assert source.mapping_config["observables"] == [{"type": "mail", "paths": ["$.email"]}]
        assert not AlertObservable.objects.exists()
        log = AuditLog.objects.get(action="ingestion_source.observables_applied")
        assert log.details["alerts"]["candidates"] == 0
