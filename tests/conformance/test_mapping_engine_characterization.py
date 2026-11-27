"""Characterization tests pinned before the §13.5 mapping package move.

These tests deliberately exercise the public surface of ``ingest.mapping`` through imports only.
They are behavior snapshots: no test reaches into a module-private helper or a future submodule path.
"""

from __future__ import annotations

import pytest

from ingest.mapping import (
    TARGETS,
    TRANSFORMS,
    AlertDraft,
    FieldWarning,
    MappingResolution,
    apply_mapping,
    config_shape,
    extract_all_by_jsonpath,
    extract_by_jsonpath,
    extract_correlation_key,
    read_rules,
    resolve_mapping_config,
    submapping_count,
    validate_config,
)


def test_extract_by_jsonpath_returns_first_match_and_tolerates_bad_path() -> None:
    payload = {"name": "first", "nested": {"name": "second"}}
    assert extract_by_jsonpath("$.name", payload) == "first"
    assert extract_by_jsonpath("$.missing", payload) is None
    assert extract_by_jsonpath("$[", payload) is None


def test_extract_all_by_jsonpath_flattens_scalars_and_skips_objects() -> None:
    payload = {"items": [1, [2, {"x": 3}], None, True]}
    assert extract_all_by_jsonpath("$.items[*]", payload) == [1, 2]
    assert extract_all_by_jsonpath("$[", payload) == []


def test_field_warning_renders_structured_and_text_forms() -> None:
    warning = FieldWarning("severity", "not_found", ("$.severity",))
    assert warning.as_dict() == {
        "field": "severity",
        "reason": "not_found",
        "paths": ["$.severity"],
    }
    assert warning.as_text() == "severity_unmapped: nothing at $.severity"

    preview = FieldWarning("severity", "unknown_value", ("$.severity",), "urgent")
    assert preview.as_dict()["value_preview"] == "urgent"
    assert preview.as_text() == "severity_unknown_value: $.severity held 'urgent'"


def test_alert_draft_defaults_are_pure_data() -> None:
    draft = AlertDraft()
    assert draft.title is None
    assert draft.severity is None
    assert draft.warnings == ()
    assert draft.field_warnings == ()


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, "empty"),
        ({}, "empty"),
        ({"title": "$.name"}, "legacy"),
        ({"version": 2, "fields": {}}, "v2"),
        ({"fields": {}}, "v2"),
    ],
)
def test_config_shape_matches_the_reader(config: object, expected: str) -> None:
    assert config_shape(config) == expected


def test_read_rules_preserves_legacy_paths_and_v2_transform() -> None:
    assert read_rules({"title": "$.name"}) == {"title": {"paths": ["$.name"], "transform": "str"}}
    assert read_rules(
        {
            "version": 2,
            "fields": {
                "title": {"paths": ["$.display_name"], "transform": "str_lower", "default": "x"}
            },
        }
    ) == {
        "title": {
            "paths": ["$.display_name"],
            "transform": "str_lower",
            "default": "x",
        }
    }


def test_validate_config_accepts_valid_v2_and_rejects_unknown_target() -> None:
    valid = {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"], "transform": "str"}},
    }
    assert validate_config(valid) == []
    errors = validate_config({"version": 2, "fields": {"bogus": {}}})
    assert any("unknown target" in error for error in errors)


def test_extract_correlation_key_uses_newest_winning_spelling() -> None:
    config = {"correlation_key": "$.a", "correlationKey": "$.b"}
    assert extract_correlation_key(config, {"a": "A", "b": "B"}) == "A"
    assert extract_correlation_key(None, {"a": "A"}) == ""


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, 0),
        ({"title": "$.name"}, 0),
        ({"submappings": {}}, 0),
        ({"submappings": {"phish": {"version": 2}}}, 1),
    ],
)
def test_submapping_count(config: dict[str, object] | None, expected: int) -> None:
    assert submapping_count(config) == expected


def test_resolve_mapping_config_exact_match_is_a_shallow_copy_without_nesting() -> None:
    master = {
        "version": 2,
        "fields": {},
        "submappings": {
            "phish": {
                "version": 2,
                "fields": {},
                "submappings": {"nested": {"version": 2, "fields": {}}},
            }
        },
    }
    payload = {"type": "phish"}
    result = resolve_mapping_config(master, payload)

    assert result.matched_type == "phish"
    assert result.used_submapping is True
    assert "submappings" not in result.config
    assert "submappings" in master["submappings"]["phish"]
    assert payload == {"type": "phish"}


def test_resolve_mapping_config_falls_back_without_mutation() -> None:
    master = {"version": 2, "fields": {}, "submappings": {"phish": {"version": 2}}}
    assert resolve_mapping_config(master, "not-a-dict") == MappingResolution(config=master)
    assert resolve_mapping_config(master, {"type": "other"}) == MappingResolution(config=master)
    assert master["submappings"] == {"phish": {"version": 2}}


def test_apply_mapping_uses_rule_paths_and_records_defaulted_severity() -> None:
    config = {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"], "transform": "str", "max_length": 100}},
    }
    draft = apply_mapping(config, {"name": "  Credential theft  "})
    assert draft.title == "Credential theft"
    assert draft.severity == 2
    assert any("severity_defaulted" in warning for warning in draft.warnings)


def test_transform_and_target_tables_are_stable() -> None:
    assert TARGETS == (
        "title",
        "description",
        "summary",
        "severity",
        "date",
        "type",
        "source_ref",
    )
    assert set(TRANSFORMS) == {
        "str",
        "str_lower",
        "int",
        "severity_name",
        "severity_score",
        "severity_int",
        "datetime",
        "list",
        "json",
        "tlp",
        "pap",
    }
