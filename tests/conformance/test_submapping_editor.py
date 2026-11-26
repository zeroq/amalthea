"""Structured editor helpers for source-detail submappings (§25 UI follow-up).

The mapping engine already owns the config language; these tests pin the editor's part of the
contract: add/delete/save operate on one whole config, preserve every sibling byte-for-byte, and
never mutate the caller's dict.
"""

from __future__ import annotations

import copy

import pytest

from ingest.mapping import MAX_SUBMAPPING_KEY_LEN, MAX_SUBMAPPINGS
from ingest.submapping_editor import (
    build_submapping_add,
    build_submapping_config,
    build_submapping_delete,
    submapping_config,
    submapping_keys,
)

TYPES = [type("T", (), {"name": "mail"})()]


def _config(*keys: str) -> dict:
    return {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"]}},
        "observables": [{"type": "mail", "paths": ["$.email"]}],
        "submappings": {key: {"version": 2, "fields": {}} for key in keys},
    }


def test_add_bootstraps_an_empty_config_without_changing_the_original() -> None:
    before: dict = {}
    snapshot = copy.deepcopy(before)

    result = build_submapping_add(before, "  ldt  ")

    assert result.ok, result.errors
    assert result.config == {
        "version": 2,
        "fields": {},
        "submappings": {"ldt": {"version": 2, "fields": {}}},
    }
    assert before == snapshot


def test_add_preserves_master_rules_and_siblings() -> None:
    before = _config("phishing")
    snapshot = copy.deepcopy(before)

    result = build_submapping_add(before, "edr")

    assert result.ok, result.errors
    assert result.config["fields"] == snapshot["fields"]
    assert result.config["observables"] == snapshot["observables"]
    assert result.config["submappings"]["phishing"] == snapshot["submappings"]["phishing"]
    assert submapping_keys(result.config) == ["edr", "phishing"]
    assert before == snapshot


@pytest.mark.parametrize("key_text", ["", "   ", "x" * (MAX_SUBMAPPING_KEY_LEN + 1)])
def test_add_rejects_bad_keys(key_text: str) -> None:
    result = build_submapping_add(_config(), key_text)
    assert not result.ok
    assert "submapping_key" in result.errors
    assert result.config is None


def test_add_rejects_duplicate_key() -> None:
    result = build_submapping_add(_config("ldt"), "ldt")
    assert not result.ok
    assert "already exists" in " ".join(result.errors["submapping_key"])


def test_add_rejects_51st_key() -> None:
    before = {"version": 2, "fields": {}}
    before["submappings"] = {
        f"type-{index:02d}": {"version": 2, "fields": {}} for index in range(MAX_SUBMAPPINGS)
    }
    result = build_submapping_add(before, "one-too-many")
    assert not result.ok
    assert "50" in " ".join(result.errors["submapping_key"])


def test_add_refuses_legacy_master() -> None:
    result = build_submapping_add({"title": {"paths": ["$.name"]}}, "ldt")
    assert not result.ok
    assert "legacy" in " ".join(result.errors["submapping_key"]).lower()


def test_save_updates_only_the_selected_submapping() -> None:
    before = _config("phishing", "edr")
    before["submappings"]["edr"] = {
        "version": 2,
        "fields": {"title": {"paths": ["$.old"]}},
        "observables": [{"type": "mail", "paths": ["$.old_email"]}],
    }
    snapshot = copy.deepcopy(before)

    result = build_submapping_config(
        before,
        "edr",
        {"title": {"paths": "$.new", "transform": "str", "default": ""}},
        {"mail": "$.new_email"},
        TYPES,
    )

    assert result.ok, result.errors
    assert result.config["fields"] == snapshot["fields"]
    assert result.config["observables"] == snapshot["observables"]
    assert result.config["submappings"]["phishing"] == snapshot["submappings"]["phishing"]
    assert submapping_config(result.config, "edr") == {
        "version": 2,
        "fields": {"title": {"paths": ["$.new"]}},
        "observables": [{"type": "mail", "paths": ["$.new_email"]}],
    }
    assert before == snapshot


def test_save_empty_table_still_keeps_a_complete_v2_submapping() -> None:
    before = _config("ldt")
    result = build_submapping_config(
        before, "ldt", {}, {}, TYPES
    )  # every master row defaults to empty
    assert result.ok, result.errors
    assert submapping_config(result.config, "ldt") == {"version": 2, "fields": {}}


def test_save_reports_row_errors_and_writes_nothing() -> None:
    before = _config("ldt")
    result = build_submapping_config(
        before,
        "ldt",
        {"severity": {"paths": "$.severity", "transform": "str", "default": "nope"}},
        {},
        TYPES,
    )
    assert not result.ok
    assert "severity" in result.errors
    assert result.config is None


def test_delete_removes_only_one_key_and_survives_the_last_one() -> None:
    before = _config("phishing", "edr")
    snapshot = copy.deepcopy(before)

    first = build_submapping_delete(before, "edr")
    assert first.ok, first.errors
    assert submapping_keys(first.config) == ["phishing"]
    assert first.config["fields"] == snapshot["fields"]

    second = build_submapping_delete(first.config, "phishing")
    assert second.ok, second.errors
    assert "submappings" not in second.config
    assert second.config["version"] == 2
    assert before == snapshot


def test_nested_submappings_are_rejected_on_save() -> None:
    before = _config("ldt")
    before["submappings"]["ldt"]["submappings"] = {"nested": {"version": 2, "fields": {}}}
    result = build_submapping_config(before, "ldt", {}, {}, TYPES)
    assert not result.ok
    assert any(
        "nested submappings" in message
        for messages in result.errors.values()
        for message in messages
    )
