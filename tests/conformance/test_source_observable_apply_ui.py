"""Rendered observable-apply modal: controls, counts and safety copy (PLAN-2026-11-09 §7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User
from ingest.models import IngestionSource
from observables.models import ObservableType

pytestmark = pytest.mark.django_db

MAPPING = {
    "version": 2,
    "fields": {"title": {"paths": ["$.name"]}},
    "observables": [{"type": "mail", "paths": ["$.email"]}],
}


def _status() -> AlertStatus:
    status, _ = AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New"})
    return status


def _alert(source: IngestionSource, ref: str) -> Alert:
    return Alert.objects.create(
        title="Stored title",
        severity=2,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        type="phishing",
        source="wire-source",
        source_ref=ref,
        status=_status(),
        raw_payload={"email": "analyst@example.test"},
        ingestion_source=source,
        owner_org=source.organisation,
    )


@pytest.fixture
def feed():
    org = Organisation.objects.create(name="Observable Modal Org")
    admin = User.objects.create(
        login="observable-modal-admin",
        username="observable-modal-admin",
        email="modal-admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )
    source = IngestionSource.objects.create(
        organisation=org,
        slug="observable-modal-feed",
        name="Observable modal feed",
        mapping_config=dict(MAPPING),
    )
    ObservableType.objects.get_or_create(name="mail", defaults={"name": "mail"})
    return admin, source


def _url(source: IngestionSource) -> str:
    return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})


def _post_preview(user: User, source: IngestionSource):
    client = Client()
    client.force_login(user)
    return client.post(
        _url(source),
        {
            "form": "observables",
            "intent": "apply",
            "observable_paths__mail": "$.email",
        },
    )


class TestControlsAndModal:
    def test_the_card_has_a_second_apply_control_and_no_modal_on_get(self, feed) -> None:
        admin, source = feed
        client = Client()
        client.force_login(admin)

        body = client.get(_url(source)).content.decode()

        assert body.count('name="intent" value="apply"') == 3
        assert "Apply to unlinked alerts…" in body
        assert "Changing rules does not remove links already extracted." in body
        assert 'id="observable-apply-modal"' not in body

    def test_the_modal_states_counts_and_carries_the_revalidated_config(self, feed) -> None:
        admin, source = feed
        _alert(source, "one")
        _alert(source, "two")

        response = _post_preview(admin, source)
        body = response.content.decode()

        assert response.status_code == 200
        assert 'id="observable-apply-modal"' in body
        assert 'data-autoopen="1"' in body
        assert "2 unlinked open alerts" in body
        assert "Values found" in body
        assert "New links" in body
        assert "Already linked" in body
        assert '<td class="mono">mail</td>' in body
        assert '<td class="mono">2</td>' in body
        assert 'name="pending_config"' in body
        assert 'name="intent" value="apply_confirmed"' in body
        assert 'name="form" value="observables"' in body

    def test_the_modal_has_no_inline_script_or_style(self, feed) -> None:
        admin, source = feed
        _alert(source, "one")

        body = _post_preview(admin, source).content.decode()
        modal = body.split('id="observable-apply-modal"', 1)[1].split("</dialog>", 1)[0]

        assert "<script" not in modal
        assert "style=" not in modal


class TestWarningsAndRefusals:
    def test_no_candidates_says_the_rules_still_save(self, feed) -> None:
        admin, source = feed

        body = _post_preview(admin, source).content.decode()

        assert "There are no unlinked open alerts to extract from." in body
        assert "The rules will still be saved." in body

    def test_over_cap_refuses_and_disables_confirm(
        self, feed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ingest import remap

        monkeypatch.setattr(remap, "MAX_REMAP_ALERTS", 1)
        admin, source = feed
        _alert(source, "one")
        _alert(source, "two")

        response = _post_preview(admin, source)
        body = response.content.decode()

        assert "1-per-request limit" in body
        assert "not</strong> saved either" in body
        assert "disabled>Save & extract" in body
