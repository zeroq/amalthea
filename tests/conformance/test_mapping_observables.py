"""P1 — observable rules are parsed strictly at save time and leniently at ingest time."""

from __future__ import annotations

import pytest

from ingest.mapping import extract_all_by_jsonpath, validate_config
from ingest.observable_rules import (
    MAX_OBSERVABLE_CANDIDATES,
    MAX_OBSERVABLE_PATHS,
    MAX_OBSERVABLE_RULES,
    MAX_OBSERVABLE_VALUE_CHARS,
    extract_observable_candidates,
    observable_rules,
    validate_observables,
)

CONFIG = {
    "version": 2,
    "fields": {},
    "observables": [
        {"type": "mail", "paths": ["$.actor.email", "$.recipients[*].address"]},
        {"type": "ip", "paths": ["$.source.ip"]},
    ],
}


def test_extract_all_by_jsonpath_returns_every_match_and_flattens_arrays() -> None:
    payload = {"recipients": [{"address": "a@example.com"}, {"address": "b@example.com"}]}
    assert extract_all_by_jsonpath("$.recipients[*].address", payload) == [
        "a@example.com",
        "b@example.com",
    ]


def test_extract_all_by_jsonpath_skips_objects_nulls_and_booleans_but_stringifies_numbers() -> None:
    payload = {"mixed": ["a", 3, None, True, {"nested": "x"}, ["b"]]}
    assert extract_all_by_jsonpath("$.mixed", payload) == ["a", 3, "b"]


def test_candidates_are_deterministic_and_union_paths_and_rules() -> None:
    payload = {
        "actor": {"email": "a@example.com"},
        "recipients": [{"address": "b@example.com"}, {"address": "a@example.com"}],
        "source": {"ip": "203.0.113.9"},
    }

    extraction = extract_observable_candidates(CONFIG, payload)

    assert [(c.type_name, c.raw_value) for c in extraction.candidates] == [
        ("mail", "a@example.com"),
        ("mail", "b@example.com"),
        ("ip", "203.0.113.9"),
    ]
    assert not extraction.truncated


def test_missing_paths_and_malformed_rules_do_not_raise() -> None:
    config = {
        "observables": [
            None,
            {"type": "", "paths": ["$.x"]},
            {"type": "mail", "paths": "not-a-list"},
            {"type": "ip", "paths": ["$..["]},
            {"type": "mail", "paths": ["$.real"]},
        ]
    }
    extraction = extract_observable_candidates(config, {"real": "ok@example.com"})

    assert [c.raw_value for c in extraction.candidates] == ["ok@example.com"]
    assert extraction.truncated


def test_an_oversize_value_is_skipped_and_reported() -> None:
    extraction = extract_observable_candidates(
        {"observables": [{"type": "mail", "paths": ["$.value"]}]},
        {"value": "x" * (MAX_OBSERVABLE_VALUE_CHARS + 1)},
    )

    assert extraction.candidates == ()
    assert extraction.truncated
    assert extraction.skipped_values == 1
    assert any("observables_truncated" in note for note in extraction.notes)


def test_candidate_cap_preserves_the_first_n_unique_values() -> None:
    payload = {"items": [f"user{i}@example.com" for i in range(MAX_OBSERVABLE_CANDIDATES + 3)]}
    extraction = extract_observable_candidates(
        {"observables": [{"type": "mail", "paths": ["$.items[*]"]}]},
        payload,
    )

    assert len(extraction.candidates) == MAX_OBSERVABLE_CANDIDATES
    assert extraction.candidates[0].raw_value == "user0@example.com"
    assert extraction.truncated


def test_validation_accepts_a_missing_or_empty_section_and_unknown_types() -> None:
    assert validate_observables({"version": 2, "fields": {}}) == []
    assert validate_observables({"version": 2, "fields": {}, "observables": []}) == []
    assert (
        validate_observables(
            {"version": 2, "fields": {}, "observables": [{"type": "future", "paths": ["$.x"]}]}
        )
        == []
    )


@pytest.mark.parametrize(
    "observables, fragment",
    [
        ("not-a-list", "JSON array"),
        ([{"paths": ["$.x"]}], "type"),
        ([{"type": "mail"}], "paths"),
        ([{"type": "mail", "paths": []}], "paths"),
        ([{"type": "mail", "paths": ["x"]}], "starts with $"),
        ([{"type": "mail", "paths": ["$.["]}], "not a valid JSONPath"),
    ],
)
def test_validation_rejects_malformed_rules_with_readable_paths(observables, fragment) -> None:
    errors = validate_observables({"version": 2, "fields": {}, "observables": observables})

    assert errors
    assert fragment in errors[0]


def test_validation_enforces_rule_path_and_value_limits() -> None:
    too_many_rules = {
        "observables": [
            {"type": f"type{i}", "paths": ["$.x"]} for i in range(MAX_OBSERVABLE_RULES + 1)
        ]
    }
    too_many_paths = {
        "observables": [
            {"type": "mail", "paths": [f"$.p{i}" for i in range(MAX_OBSERVABLE_PATHS + 1)]}
        ]
    }

    assert any("at most" in error for error in validate_observables(too_many_rules))
    assert any("at most" in error for error in validate_observables(too_many_paths))


def test_validate_config_includes_observable_errors() -> None:
    errors = validate_config(
        {"version": 2, "fields": {}, "observables": [{"type": "mail", "paths": ["bad"]}]}
    )

    assert any(error.startswith("observables[0].paths[0]") for error in errors)


def test_observable_rules_is_a_lenient_reader_that_deduplicates_paths() -> None:
    rules = observable_rules({"observables": [{"type": " mail ", "paths": ["$.a", "$.a"]}]})

    assert len(rules) == 1
    assert rules[0].type_name == "mail"
    assert rules[0].paths == ("$.a",)
