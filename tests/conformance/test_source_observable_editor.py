"""P6 — the source page's observable-rules card owns only the `observables` key."""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from identity.models import AuditLog, Organisation, User
from ingest.models import IngestionSource
from observables.models import ObservableType

pytestmark = pytest.mark.django_db

MAPPING = {
    "version": 2,
    "fields": {"title": {"paths": ["$.name"], "transform": "str"}},
    "value_maps": {"severity": {"Suspicious": 2}},
    "observables": [
        {"type": "future-type", "paths": ["$.future"]},
        {"type": "mail", "paths": ["$.actor.email"]},
    ],
}


@pytest.fixture
def feed():
    org = Organisation.objects.create(name="Observable Editor Org")
    admin = User.objects.create(
        login="observable-admin",
        username="observable-admin",
        email="admin@example.net",
        org=org,
        role="admin",
        is_active=True,
    )
    source = IngestionSource.objects.create(
        organisation=org,
        slug="observable-feed",
        name="Observable feed",
        mapping_config=MAPPING,
    )
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


def _table_post(**overrides: tuple[str, str, str]) -> dict[str, str]:
    from ingest.mapping import TARGETS

    data = {"form": "table"}
    for target in TARGETS:
        paths, transform, default = overrides.get(target, ("", "", ""))
        data[f"paths__{target}"] = paths
        data[f"transform__{target}"] = transform
        data[f"default__{target}"] = default
    return data


def test_the_card_renders_one_row_per_vocabulary_type(feed) -> None:
    _org, admin, source = feed

    response = _client(admin).get(_url(source))
    body = response.content.decode()

    assert response.status_code == 200
    assert "Observable extraction" in body
    assert body.count('name="observable_paths__') == ObservableType.objects.count()
    assert 'name="observable_paths__mail"' in body
    assert "$.actor.email" in body
    assert 'name="observable_paths__future-type"' not in body


def test_saving_preserves_fields_value_maps_and_unknown_type_rules(feed) -> None:
    org, admin, source = feed

    response = _client(admin).post(
        _url(source),
        _observable_post(mail="$.actor.email\n$.recipients[*].address", ip="$.source.ip"),
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.mapping_config["fields"] == MAPPING["fields"]
    assert source.mapping_config["value_maps"] == MAPPING["value_maps"]
    assert source.mapping_config["observables"] == [
        {"type": "future-type", "paths": ["$.future"]},
        {"type": "mail", "paths": ["$.actor.email", "$.recipients[*].address"]},
        {"type": "ip", "paths": ["$.source.ip"]},
    ]

    log = AuditLog.objects.get(action="ingestion_source.updated")
    assert log.actor == admin
    assert log.target_org == org
    assert log.details["changed"] == ["observables"]


def test_saving_the_rendered_card_unchanged_is_a_no_op(feed) -> None:
    _org, admin, source = feed
    source.mapping_config = {
        "version": 2,
        "fields": {},
        "observables": [{"type": "mail", "paths": ["$.actor.email"]}],
    }
    source.save(update_fields=["mapping_config", "updated_at"])
    updated_before = source.updated_at

    response = _client(admin).post(
        _url(source),
        _observable_post(mail="$.actor.email"),
        follow=True,
    )

    assert "No changes to save." in response.content.decode()
    source.refresh_from_db()
    assert source.updated_at == updated_before
    assert not AuditLog.objects.exists()


def test_an_invalid_path_re_renders_the_input_and_writes_nothing(feed) -> None:
    _org, admin, source = feed
    updated_before = source.updated_at

    response = _client(admin).post(
        _url(source),
        _observable_post(mail="not-a-path"),
    )
    body = response.content.decode()

    assert response.status_code == 200
    assert "JSONPath starts with $." in body
    assert "not-a-path" in body
    source.refresh_from_db()
    assert source.mapping_config == MAPPING
    assert source.updated_at == updated_before
    assert not AuditLog.objects.exists()


def test_clearing_every_row_removes_the_section_but_keeps_the_fields(feed) -> None:
    _org, admin, source = feed
    source.mapping_config = {
        "version": 2,
        "fields": MAPPING["fields"],
        "value_maps": MAPPING["value_maps"],
        "observables": [{"type": "mail", "paths": ["$.actor.email"]}],
    }
    source.save(update_fields=["mapping_config", "updated_at"])

    response = _client(admin).post(_url(source), _observable_post())

    assert response.status_code == 302
    source.refresh_from_db()
    assert "observables" not in source.mapping_config
    assert source.mapping_config["fields"] == MAPPING["fields"]
    assert source.mapping_config["value_maps"] == MAPPING["value_maps"]


def test_an_empty_config_gets_the_current_shape_on_first_save(feed) -> None:
    _org, admin, source = feed
    source.mapping_config = {}
    source.save(update_fields=["mapping_config", "updated_at"])

    response = _client(admin).post(
        _url(source),
        _observable_post(mail="$.actor.email"),
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.mapping_config == {
        "version": 2,
        "fields": {},
        "observables": [{"type": "mail", "paths": ["$.actor.email"]}],
    }


def test_a_table_save_preserves_observable_rules(feed) -> None:
    _org, admin, source = feed
    source.mapping_config = {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
        "observables": [{"type": "mail", "paths": ["$.actor.email"]}],
    }
    source.save(update_fields=["mapping_config", "updated_at"])

    response = _client(admin).post(
        _url(source),
        _table_post(title=("$.name", "", "")),
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.mapping_config["observables"] == [{"type": "mail", "paths": ["$.actor.email"]}]


def test_the_json_box_validates_observable_rules(feed) -> None:
    _org, admin, source = feed
    config = {
        "version": 2,
        "fields": {},
        "observables": [{"type": "mail", "paths": ["bad"]}],
    }

    response = _client(admin).post(
        _url(source),
        {"form": "json", "mapping_json": json.dumps(config)},
    )

    assert response.status_code == 200
    assert "JSONPath starts with $." in response.content.decode()
    source.refresh_from_db()
    assert source.mapping_config == MAPPING


def test_an_analyst_cannot_write_observable_rules(feed) -> None:
    org, _admin, source = feed
    analyst = User.objects.create(
        login="observable-analyst",
        username="observable-analyst",
        email="analyst@example.net",
        org=org,
        role="analyst",
        is_active=True,
    )

    response = _client(analyst).post(
        _url(source),
        _observable_post(mail="$.actor.email"),
    )

    assert response.status_code == 404
    source.refresh_from_db()
    assert source.mapping_config == MAPPING
