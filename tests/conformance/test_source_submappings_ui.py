"""Source detail Submappings + Observable Mapping tabs (§25 UI follow-up)."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert, AlertStatus
from identity.models import Organisation, User
from ingest.models import IngestionSource
from observables.models import ObservableType

pytestmark = pytest.mark.django_db


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Submap Org")


@pytest.fixture
def admin(org: Organisation) -> User:
    return User.objects.create(
        login="submap-admin",
        username="submap-admin",
        email="admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )


@pytest.fixture
def client(admin: User) -> Client:
    c = Client()
    c.force_login(admin)
    return c


@pytest.fixture
def source(org: Organisation) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=org,
        slug="submap-feed",
        name="Submap feed",
        mapping_config={
            "version": 2,
            "fields": {"title": {"paths": ["$.name"]}},
            "observables": [{"type": "mail", "paths": ["$.master_email"]}],
        },
    )


@pytest.fixture(autouse=True)
def observable_types() -> None:
    ObservableType.objects.get_or_create(name="mail", defaults={"is_case_sensitive": False})


def _url(source: IngestionSource) -> str:
    return reverse("ui-settings-source-detail", kwargs={"source_id": source.id})


def _status() -> AlertStatus:
    status, _ = AlertStatus.objects.get_or_create(
        value="New", defaults={"stage": "New", "order": 1}
    )
    return status


def _make_alert(source: IngestionSource, org: Organisation, *, ref: str, payload: dict) -> Alert:
    return Alert.objects.create(
        title="Before",
        type="phishing",
        source="webhook",
        source_ref=ref,
        status=_status(),
        raw_payload=payload,
        ingestion_source=source,
        owner_org=org,
        date=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _pending_config(body: str) -> dict:
    match = re.search(r'name="pending_config" value="([^"]+)"', body)
    assert match, "confirmation dialog must carry the validated whole config"
    return json.loads(html.unescape(match.group(1)))


def test_tab_strip_has_all_four_tabs_in_order(client: Client, source: IngestionSource) -> None:
    body = client.get(_url(source)).content.decode()

    tabs = re.findall(r'href="\?tab=([^"]+)"', body)
    assert tabs[:4] == ["mapping", "observable-mapping", "submappings", "raw-data"]
    assert "Mapping rules" in body
    assert "Observable extraction" not in body
    assert "Observable rules now live on the" in body


def test_observable_tab_holds_the_observable_card(client: Client, source: IngestionSource) -> None:
    body = client.get(_url(source) + "?tab=observable-mapping").content.decode()

    assert "Observable extraction" in body
    assert "Mapping rules" not in body
    assert body.count('name="intent" value="apply"') == 1
    assert "observable_paths__mail" in body
    assert "$.master_email" in body


def test_submappings_tab_shows_latest_raw_type(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    _make_alert(source, org, ref="raw-1", payload={"type": "ldt", "name": "sample"})

    body = client.get(_url(source) + "?tab=submappings").content.decode()

    assert "Submappings" in body
    assert "Raw type" in body
    assert "ldt" in body
    assert "Use latest raw type" in body
    assert "submapping_prefill=ldt" in body
    assert "Mapping rules" not in body

    prefilled = client.get(
        _url(source) + "?tab=submappings&submapping_prefill=ldt"
    ).content.decode()
    assert re.search(r'id="submapping_new_key"[^>]*value="ldt"', prefilled)


def test_add_submapping_creates_empty_v2_value_and_redirects(
    client: Client, source: IngestionSource
) -> None:
    response = client.post(
        _url(source), {"form": "submappings", "intent": "add", "submapping_key": "  ldt  "}
    )

    assert response.status_code == 302
    assert response["Location"] == _url(source) + "?tab=submappings&submapping=ldt"
    source.refresh_from_db()
    assert source.mapping_config["submappings"] == {"ldt": {"version": 2, "fields": {}}}
    assert source.mapping_config["fields"] == {"title": {"paths": ["$.name"]}}


def test_save_submapping_updates_only_its_own_fields_and_observables(
    client: Client, source: IngestionSource
) -> None:
    source.mapping_config["submappings"] = {
        "phishing": {"version": 2, "fields": {}},
        "ldt": {"version": 2, "fields": {}},
    }
    source.save(update_fields=["mapping_config"])

    response = client.post(
        _url(source),
        {
            "form": "submappings",
            "submapping_key": "ldt",
            "paths__title": "$.threat.name",
            "transform__title": "str",
            "default__title": "",
            "observable_paths__mail": "$.actor.email",
        },
    )

    assert response.status_code == 302
    assert "submapping=ldt" in response["Location"]
    source.refresh_from_db()
    config = source.mapping_config
    assert config["fields"] == {"title": {"paths": ["$.name"]}}
    assert config["observables"] == [{"type": "mail", "paths": ["$.master_email"]}]
    assert config["submappings"]["phishing"] == {"version": 2, "fields": {}}
    assert config["submappings"]["ldt"] == {
        "version": 2,
        "fields": {"title": {"paths": ["$.threat.name"]}},
        "observables": [{"type": "mail", "paths": ["$.actor.email"]}],
    }


def test_rejected_save_stays_on_submappings_and_echoes_input(
    client: Client, source: IngestionSource
) -> None:
    source.mapping_config["submappings"] = {"ldt": {"version": 2, "fields": {}}}
    source.save(update_fields=["mapping_config"])

    response = client.post(
        _url(source),
        {
            "form": "submappings",
            "submapping_key": "ldt",
            "paths__severity": "$.severity",
            "transform__severity": "str",
            "default__severity": "not-a-number",
        },
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert "Submappings" in body
    assert "$.severity" in body
    assert "Severity default must be a whole number 1-4" in body
    source.refresh_from_db()
    assert source.mapping_config["submappings"]["ldt"] == {"version": 2, "fields": {}}


def test_delete_uses_custom_dialog_then_removes_one_key(
    client: Client, source: IngestionSource
) -> None:
    source.mapping_config["submappings"] = {
        "phishing": {"version": 2, "fields": {}},
        "ldt": {"version": 2, "fields": {}},
    }
    source.save(update_fields=["mapping_config"])

    preview = client.post(
        _url(source), {"form": "submappings", "intent": "delete", "submapping_key": "ldt"}
    )
    body = preview.content.decode()

    assert preview.status_code == 200
    assert 'id="submapping-delete-modal"' in body
    assert "This only removes the override" in body
    assert 'name="intent" value="delete_confirmed"' in body
    source.refresh_from_db()
    assert set(source.mapping_config["submappings"]) == {"phishing", "ldt"}

    confirmed = client.post(
        _url(source),
        {"form": "submappings", "intent": "delete_confirmed", "submapping_key": "ldt"},
    )

    assert confirmed.status_code == 302
    source.refresh_from_db()
    assert list(source.mapping_config["submappings"]) == ["phishing"]
    assert source.mapping_config["fields"] == {"title": {"paths": ["$.name"]}}


def test_apply_preview_writes_nothing_and_explains_whole_config_scope(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    _make_alert(source, org, ref="apply-1", payload={"type": "ldt", "name": "Before"})
    source.mapping_config["submappings"] = {
        "ldt": {"version": 2, "fields": {"title": {"paths": ["$.name"]}}}
    }
    source.save(update_fields=["mapping_config"])
    before = source.mapping_config

    response = client.post(
        _url(source),
        {
            "form": "submappings",
            "intent": "apply",
            "submapping_key": "ldt",
            "paths__title": "$.name",
            "transform__title": "str",
            "default__title": "",
            "observable_paths__mail": "",
        },
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert 'id="mapping-apply-modal"' in body
    assert "This applies the <strong>complete mapping</strong>" in body
    pending = _pending_config(body)
    assert pending["submappings"]["ldt"]["fields"]["title"] == {"paths": ["$.name"]}
    assert 'name="form" value="submappings"' in body
    source.refresh_from_db()
    assert source.mapping_config == before


def test_apply_confirmed_persists_whole_config_and_remaps(
    client: Client, source: IngestionSource, org: Organisation
) -> None:
    alert = _make_alert(source, org, ref="apply-2", payload={"type": "ldt", "name": "After"})
    source.mapping_config["submappings"] = {
        "ldt": {"version": 2, "fields": {"title": {"paths": ["$.name"]}}}
    }
    source.save(update_fields=["mapping_config"])

    pending = {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
        "submappings": {
            "ldt": {"version": 2, "fields": {"title": {"paths": ["$.name"], "transform": "str"}}}
        },
    }
    response = client.post(
        _url(source),
        {
            "form": "submappings",
            "intent": "apply_confirmed",
            "submapping_key": "ldt",
            "pending_config": json.dumps(pending),
        },
    )

    assert response.status_code == 302
    assert "tab=submappings" in response["Location"]
    source.refresh_from_db()
    alert.refresh_from_db()
    assert source.mapping_config == pending
    assert alert.title == "After"


def test_analyst_cannot_post_to_submappings(org: Organisation, source: IngestionSource) -> None:
    analyst = User.objects.create(
        login="submap-analyst",
        username="submap-analyst",
        email="analyst@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )
    c = Client()
    c.force_login(analyst)

    response = c.post(
        _url(source), {"form": "submappings", "intent": "add", "submapping_key": "ldt"}
    )

    assert response.status_code == 404
