"""The golden corpus for the T1 conformance suite (AC-B1).

Each fixture under `tests/fixtures/thehive/` is a structurally-derived copy of a recorded
TheHive example (OpenAPI line refs are in the plan): the keys are exactly the keys the spec
marks `required` plus the optional keys the T1 contract emits, and the values are the recorded
example values where T1 emits such a field at all.

Two deliberate divergences from the recorded examples, both approved deviations, live here too:

* `_id` is a plain UUID on our wire, never a `~123` TheHive row id — pinned elsewhere, by
  `test_t1_surface.py`.
* timestamps are ISO-8601 strings (P10-y), not the recorded millisecond integers.

Because of those, the fixtures are the **key-shape** corpus: what they pin is *which* keys
exist and what they mean, so a refactor that drops or renames a wire key fails here before it
reaches a live-response assertion.

`user_example.json` additionally records the `org` spelling (our name for TheHive's
`organisation`) and omits `profile`/`permissions`/`extraData`, which are out of scope for the
session endpoints — see the plan's A6 key list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures" / "thehive"

#: filename -> the exact key set the fixture file carries. Editing a fixture is therefore a
#: deliberate act: the tripwire forces the pin to be re-read next to the change. The *live*
#: key sets the T1 contract must emit are asserted by `test_t1_surface.py` against real responses.
#:
#: `case_example.json` is the older, wider recorded output (it carries `access`, `papLabel`,
#: `timeToDetect`, … and no `description`); it predates the narrowed `case_json` and is kept
#: as-is because it is the record, not the contract.
PINNED_KEYS: dict[str, set[str]] = {
    "case_example.json": {
        "_id",
        "_type",
        "_createdBy",
        "_createdAt",
        "_updatedBy",
        "_updatedAt",
        "title",
        "severity",
        "severityLabel",
        "status",
        "stage",
        "number",
        "tags",
        "assignee",
        "customFields",
        "access",
        "flag",
        "pap",
        "papLabel",
        "tlp",
        "tlpLabel",
        "startDate",
        "newDate",
        "inProgressDate",
        "timeToDetect",
        "extraData",
    },
    "task_example.json": {
        "_id",
        "id",
        "_type",
        "_createdBy",
        "_updatedBy",
        "_createdAt",
        "_updatedAt",
        "title",
        "group",
        "description",
        "status",
        "flag",
        "startDate",
        "endDate",
        "order",
        "dueDate",
        "assignee",
        "mandatory",
        "extraData",
    },
    "custom_event_example.json": {
        "_id",
        "id",
        "_type",
        "_createdBy",
        "_updatedBy",
        "_createdAt",
        "_updatedAt",
        "date",
        "endDate",
        "title",
        "description",
        "caseId",
    },
    "custom_field_example.json": {
        "_id",
        "_type",
        "_createdBy",
        "_createdAt",
        "name",
        "displayName",
        "group",
        "description",
        "type",
        "options",
        "order",
        "mandatory",
        "extraData",
    },
    "user_example.json": {
        "_id",
        "_type",
        "_createdBy",
        "_createdAt",
        "login",
        "name",
        "org",
        "hasKey",
        "hasPassword",
        "hasMFA",
        "locked",
    },
    "alert_observable_example.json": {"dataType", "data", "tlp"},
}


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_every_fixture_has_a_pinned_key_set() -> None:
    """A new fixture is only useful once its key set is written down."""
    on_disk = {p.name for p in FIXTURES.glob("*.json")}
    assert on_disk == set(PINNED_KEYS), (
        f"fixtures without a PINNED_KEYS entry: {on_disk - set(PINNED_KEYS)}; "
        f"PINNED_KEYS without a file: {set(PINNED_KEYS) - on_disk}"
    )


@pytest.mark.parametrize("name", sorted(PINNED_KEYS))
def test_fixture_keys_are_exactly_the_contract(name: str) -> None:
    assert set(_load(name)) == PINNED_KEYS[name]


def test_load_case_fixture() -> None:
    data = _load("case_example.json")
    assert data["_id"] == "~98112"
    assert data["title"] == "Suspicious Ransomware Activity"
    assert data["severity"] == 3
    assert data["status"] == "InProgress"


def test_load_task_fixture() -> None:
    data = _load("task_example.json")
    assert data["_id"] == "~84123"
    assert data["_type"] == "Task"
    assert data["title"] == "Isolate affected workstation from the network"
    assert data["status"] == "InProgress"
    assert data["group"] == "Containment"
    assert data["mandatory"] is False
    assert data["extraData"] == {}


def test_load_custom_event_fixture() -> None:
    data = _load("custom_event_example.json")
    assert data["_id"] == "~24568324"
    assert data["_type"] == "CustomEvent"
    assert data["title"] == "Ransom demand received via email"
    assert data["caseId"] == "~98112"
    # Both dates must be present: an event with an endDate that silently dropped out of the
    # payload would shrink a timeline window to a point.
    assert data["date"] < data["endDate"]


def test_load_custom_field_fixture() -> None:
    data = _load("custom_field_example.json")
    assert data["_id"] == "~123456789"
    assert data["_type"] == "customField"
    assert data["name"] == "threat-type"
    assert data["displayName"] == "Threat Type"
    assert data["type"] == "string"
    assert data["options"] == ["Malware", "Intrusion", "Data Leak"]
    assert data["mandatory"] is True


def test_load_user_fixture() -> None:
    data = _load("user_example.json")
    assert data["_id"] == "~192024"
    assert data["_type"] == "user"
    assert data["login"] == "lucas@example.com"
    assert data["org"] == "TheOrganization"
    assert data["hasKey"] is True
    assert data["hasPassword"] is True
    assert data["hasMFA"] is False
    assert data["locked"] is False


def test_load_alert_observable_fixture() -> None:
    """AC4.3's request body, verbatim from the brief."""
    data = _load("alert_observable_example.json")
    assert data == {"dataType": "domain", "data": "evil.example.com", "tlp": 2}
