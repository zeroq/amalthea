"""The mapping editor's pure half (`ingest/editor.py`) — plan §8 P3, AC11/AC12.

The editor is where an operator's typed paths become a stored `mapping_config`, so this file pins the
four promises that make that safe (`ingest/editor.py`'s module docstring):

* **rule 1 — the table owns two keys.** `value_maps`, `correlation_key`, and a hand-tuned `bands` list
  survive a table save verbatim. A form that drops what it does not render is a data-loss bug.
* **rule 2 — paths are newline-separated only (**MAP-6**)**, because a JSONPath may legitimately
  contain a comma (`$['a','b']`, `$.x[1,2]`, `?(@.n == 'a,b')`).
* **rule 3 — an empty row means "no rule"**, which restores the literal-key fallback; that is how a
  rule is deleted, and it is why `{}` is a legal, reachable config.
* **AC12 — a save that changes no behaviour changes no bytes.** The table's own rendering of a stored
  config is a fixed point of it (`is_noop_save`), so the view can skip the row write, the `updated_at`
  bump and the audit row.

The module imports nothing from Django, so almost none of this needs a database; the one test that
does is a tripwire in the other direction.
"""

from __future__ import annotations

import pathlib

import pytest

from ingest.editor import (
    LEGACY_FIELD_SPELLINGS,
    TARGET_LABELS,
    TRANSFORM_HELP,
    build_table_config,
    changed_keys,
    editor_rows,
    is_unchanged,
    parse_json_config,
    parse_paths,
    transforms_for_editor,
)
from ingest.mapping import (
    LEGACY_KEYS,
    TARGETS,
    TRANSFORMS,
)

#: The config the source page renders in the P2 tests: two rules, one of them carrying `bands`, which
#: the table does *not* render and therefore must not lose.
STORED = {
    "version": 2,
    "fields": {
        "title": {"paths": ["$.name", "$.display_name"], "transform": "str"},
        "severity": {
            "paths": ["$.severity"],
            "transform": "severity_score",
            "bands": [[80, 4], [0, 2]],
        },
    },
}


def _rows(**overrides: dict[str, str]) -> dict[str, dict[str, str]]:
    """A full table submission: every target present, as a browser posts it."""
    rows = {target: {"paths": "", "transform": "", "default": ""} for target in TARGETS}
    rows.update(overrides)
    return rows


def _row(paths: str = "", transform: str = "", default: str = "") -> dict[str, str]:
    return {"paths": paths, "transform": transform, "default": default}


def _stored_rows(config: dict) -> dict[str, dict[str, str]]:
    """What the table would post if the operator opened the page and pressed Save unchanged."""
    from ingest.editor import editor_rows as rows_for

    return {
        # The textarea carries the paths exactly as the page rendered them.
        row["target"]: {
            "paths": row["paths"],
            "transform": row["transform"],
            "default": row["default"],
        }
        for row in rows_for(config)
    }


# --- Purity -----------------------------------------------------------------


def test_the_editor_module_imports_no_django() -> None:
    """A structural guard, like the engine's: the editor is pure, so it stays testable and reusable.

    Asserted on the source rather than by observation because the failure mode is an *import* that a
    behaviour test would not notice until the module was already coupled.
    """
    import ingest.editor

    source = pathlib.Path(ingest.editor.__file__).read_text(encoding="utf-8")

    assert "django" not in source, "the editor must not import Django (deviation MAP-5)"


def test_build_table_config_needs_no_database(monkeypatch, db: None) -> None:
    """Same tripwire as the engine's AC1: a cursor call from here means the editor grew a lookup."""
    from django.db.backends.utils import CursorWrapper

    calls: list[str] = []

    def record(*args: object, **kwargs: object) -> None:
        calls.append("cursor")

    for method in ("execute", "executemany", "callproc"):
        monkeypatch.setattr(CursorWrapper, method, record)

    result = build_table_config(STORED, _stored_rows(STORED))

    monkeypatch.undo()

    assert calls == [], "build_table_config must not touch the database"
    assert result.ok and result.config is not None


# --- AC12: no-op saves -------------------------------------------------------


class TestSavingWhatIsAlreadyStoredChangesNothing:
    """The table must be a fixed point of a stored config, or "Save" becomes a daily audit event."""

    def test_a_round_trip_is_a_no_op(self) -> None:
        from ingest.editor import is_noop_save

        result = build_table_config(STORED, _stored_rows(STORED))

        assert result.ok, result.errors
        assert is_noop_save(STORED, result.config), (
            "a save that changes no behaviour must not be written, audited or timestamped (AC12)"
        )

    def test_a_canonical_config_round_trips_byte_for_byte(self) -> None:
        """When the stored config is already in the table's own spelling — no `transform: "str"` — the
        save is not merely equivalent, it is identical, so even the JSONB column does not change."""
        stored = {
            "version": 2,
            "fields": {
                "title": {"paths": ["$.name", "$.display_name"]},
                "severity": {"paths": ["$.severity"], "transform": "severity_score"},
            },
        }

        result = build_table_config(stored, _stored_rows(stored))

        assert result.ok, result.errors
        assert result.config == stored

    def test_an_explicit_default_transform_is_normalised_away(self) -> None:
        """`{"paths": […]}` and `{"paths": […], "transform": "str"}` are the same rule to the engine
        (`_normalize` defaults it), so the table stores the shorter one — and `is_noop_save` compares
        against the *table's* view of the stored config, which is why that is still a no-op."""
        from ingest.editor import is_noop_save

        result = build_table_config(STORED, _stored_rows(STORED))

        assert "transform" not in result.config["fields"]["title"]
        assert is_noop_save(STORED, result.config)
        assert not is_noop_save(STORED, {"version": 2, "fields": {"title": {"paths": ["$.other"]}}})

    def test_a_legacy_config_is_never_a_no_op(self) -> None:
        """Converting the shape *is* a change (the literal keys stop being fallbacks), so it must not be
        swallowed by the no-op check — the page warns about it instead."""
        from ingest.editor import is_noop_save

        legacy = {"title": "$.name"}
        result = build_table_config(legacy, _rows(title=_row("$.name")))

        assert result.ok, result.errors
        assert not is_noop_save(legacy, result.config)

    def test_a_config_the_table_cannot_represent_is_never_a_no_op(self) -> None:
        """A v2 config the table's own validation refuses (an unusable path) cannot be rebuilt, so the
        comparison falls back to the raw column: touching nothing is still "no change", but *any* edit
        is then validated and reported rather than silently swallowed as a no-op."""
        from ingest.editor import canonical_form, is_noop_save

        corrupt = {"version": 2, "fields": {"title": {"paths": ["name"]}}}

        assert canonical_form(corrupt) == corrupt, (
            "an unbuildable config must not read as rebuildable"
        )
        assert is_noop_save(corrupt, corrupt)
        assert not is_noop_save(corrupt, {"version": 2, "fields": {"title": {"paths": ["$.name"]}}})

    def test_a_round_trip_of_an_empty_config_stays_empty(self) -> None:
        """`{}` in, `{}` out — not `{"version": 2, "fields": {}}`, which would store "the editor was here"."""
        result = build_table_config({}, _rows())

        assert result.ok, result.errors
        assert result.config == {}

    def test_key_order_is_not_a_change(self) -> None:
        assert is_unchanged(
            {"version": 2, "fields": {"title": {"paths": ["$.a"], "transform": "str"}}},
            {"fields": {"title": {"transform": "str", "paths": ["$.a"]}}, "version": 2},
        )

    def test_a_real_change_is_a_change(self) -> None:
        assert not is_unchanged(
            {"version": 2, "fields": {"title": {"paths": ["$.a"]}}},
            {"version": 2, "fields": {"title": {"paths": ["$.b"]}}},
        )


# --- Rule 1: what the table must not drop -----------------------------------


class TestTheTablePreservesWhatItDoesNotRender:
    """The `value_maps` / `bands` / `correlation_key` guarantees (plan §7.4)."""

    def test_a_hand_tuned_bands_list_survives_a_paths_edit(self) -> None:
        result = build_table_config(
            STORED,
            _rows(
                title=_row("$.name"),
                severity=_row("$.severity", "severity_score", ""),
            ),
        )

        assert result.ok, result.errors
        assert result.config["fields"]["severity"]["bands"] == [[80, 4], [0, 2]]

    def test_value_maps_is_carried_over_untouched(self) -> None:
        stored = dict(STORED, value_maps={"severity": {"Suspicious": 2}})

        result = build_table_config(stored, _stored_rows(stored))

        assert result.ok, result.errors
        assert result.config["value_maps"] == {"severity": {"Suspicious": 2}}

    def test_correlation_key_survives_a_conversion(self) -> None:
        """`correlation_key` is not a target, so the table neither reads nor writes it."""
        stored = {"correlation_key": "$.host_names[0]", "title": "$.name"}

        result = build_table_config(stored, _rows(title=_row("$.name")))

        assert result.ok, result.errors
        assert result.config["correlation_key"] == "$.host_names[0]"

    def test_max_length_and_other_rule_keys_survive(self) -> None:
        stored = {
            "version": 2,
            "fields": {"description": {"paths": ["$.d"], "max_length": 500}},
        }

        result = build_table_config(stored, _stored_rows(stored))

        assert result.ok, result.errors
        assert result.config == stored

    def test_a_top_level_key_of_unknown_meaning_survives(self) -> None:
        """Deliberately permissive: `apply_mapping` cannot tell a future key from a typo (see
        `_normalize`), so the editor's job is to keep it, not to judge it."""
        stored = dict(STORED, future_option={"a": 1})

        result = build_table_config(stored, _stored_rows(stored))

        assert result.ok, result.errors
        assert result.config["future_option"] == {"a": 1}


# --- Rule 2: paths are newline-separated (MAP-6) ----------------------------


class TestPathsAreNewlineSeparatedOnly:
    def test_lines_become_the_candidate_list(self) -> None:
        assert parse_paths("$.a\n$.b\n$.c") == ["$.a", "$.b", "$.c"]

    def test_a_comma_inside_a_path_does_not_split_it(self) -> None:
        """MAP-6's whole reason: `$['a','b']` is one path, and splitting on `,` would break it."""

        assert parse_paths("$['a','b']") == ["$['a','b']"]
        assert parse_paths("$.items[?(@.n == 'a,b')].id") == ["$.items[?(@.n == 'a,b')].id"]

    def test_blank_lines_and_padding_are_dropped(self) -> None:
        assert parse_paths("\n  $.a  \n\n\t\n$.b\n") == ["$.a", "$.b"]

    def test_duplicates_are_collapsed_and_order_is_kept(self) -> None:
        """A repeated candidate can only ever answer exactly as the first copy did."""

        assert parse_paths("$.b\n$.a\n$.b") == ["$.b", "$.a"]

    def test_carriage_returns_are_handled(self) -> None:
        assert parse_paths("$.a\r\n$.b\r\n") == ["$.a", "$.b"]

    def test_an_empty_textarea_is_no_paths(self) -> None:
        assert parse_paths("") == []
        assert parse_paths("   \n  ") == []
        assert parse_paths(None) == []


# --- Rule 3: an empty row is a deletion -------------------------------------


class TestAnEmptyRowRemovesTheRule:
    def test_clearing_a_rows_paths_deletes_the_rule(self) -> None:
        """The row stays on the page (every target is always shown); the stored rule goes."""
        result = build_table_config(
            STORED,
            _rows(
                title=_row("$.name", "str", ""),
                severity=_row("", "", ""),
            ),
        )

        assert result.ok, result.errors
        assert set(result.config["fields"]) == {"title"}, "the cleared severity rule must be gone"

    def test_a_preserved_bands_list_cannot_resurrect_a_cleared_rule(self) -> None:
        """The trap this rules out: a v2 rule with no paths and no default answers *nothing* while still
        shadowing the literal-key fallback — so "clearing" the severity row would have stopped severity
        from being read at all, which is the opposite of what an empty row means."""
        from ingest.mapping import apply_mapping

        result = build_table_config(STORED, _rows(title=_row("$.name"), severity=_row("")))

        assert result.ok, result.errors
        assert "severity" not in result.config["fields"]
        draft = apply_mapping(result.config, {"name": "n", "severity": 2})
        assert draft.severity == 2, "the payload's own severity key must answer again"

    def test_the_last_rule_leaves_an_empty_config(self) -> None:
        result = build_table_config(STORED, _rows())

        assert result.ok, result.errors
        assert result.config == {}, "clearing everything is how an operator resets to literal keys"

    def test_a_reset_restores_the_literal_key_fallback_for_the_reader(self) -> None:
        """The *consequence* of rule 3, asserted against the engine rather than restated: with no rule,
        `apply_mapping` reads the payload's own literal key again."""
        from ingest.mapping import apply_mapping

        result = build_table_config(STORED, _rows())
        draft = apply_mapping(result.config, {"name": "n", "title": "literal title"})

        assert result.config == {}
        assert draft.title == "literal title"

    def test_a_row_left_empty_because_a_legacy_key_answers_it_is_deletion(self) -> None:
        """Converting a legacy flat config without touching a row deletes nothing if the row had no
        path — but the conversion itself *is* the change, which is the warning the page shows."""
        legacy = {"title": "$.name"}

        result = build_table_config(legacy, _rows(title=_row("$.name")))

        assert result.ok, result.errors
        assert result.config == {"version": 2, "fields": {"title": {"paths": ["$.name"]}}}


# --- Rule 4: conversion is announced (and here, just correct) ---------------


class TestConvertingALegacyConfig:
    def test_the_legacy_spellings_are_consumed_not_copied(self) -> None:
        """The literal spellings (`timestamp`, `severityLevel`, …) are not targets: leaving them in the
        stored config would keep a reader that disagrees with the table."""

        legacy = {"title": "$.name", "timestamp": "$.ts", "severityLevel": "$.sev"}
        assert {"timestamp", "severityLevel"} <= LEGACY_FIELD_SPELLINGS

        result = build_table_config(legacy, _rows(title=_row("$.name")))

        assert result.ok, result.errors
        assert set(result.config) == {"version", "fields"}
        assert set(result.config["fields"]) == {"title"}

    def test_the_conversion_keeps_the_payload_fallback_for_untouched_targets(self) -> None:
        """The flip side of the warning: a target the operator left empty has *no* rule, so its literal
        fallback still applies. `read_rules` shows the operator exactly what the table will store."""
        from ingest.editor import read_rules

        rules = read_rules(build_table_config(STORED, _rows(title=_row("$.name"))).config)

        assert set(rules) == {"title"}


# --- Defaults ----------------------------------------------------------------


class TestDefaultsAreValidatedWhereTheSchemaCannot:
    """`validate_config` says nothing about `default` (it is stored verbatim, never transformed), so the
    two cases where "verbatim" cannot work are refused here."""

    def test_a_severity_default_must_be_one_to_four(self) -> None:
        for good in ("1", "4", " 2 "):
            result = build_table_config({}, _rows(severity=_row("$.sev", "severity_int", good)))
            assert result.ok, (good, result.errors)
            assert result.config["fields"]["severity"]["default"] == int(good)

    @pytest.mark.parametrize("bad", ["0", "5", "Critical", "high", "2.5", "-1"])
    def test_a_severity_default_that_cannot_be_honoured_is_refused(self, bad: str) -> None:
        result = build_table_config({}, _rows(severity=_row("$.sev", "severity_int", bad)))

        assert not result.ok
        assert result.config is None
        assert "whole number 1-4" in result.errors["severity"][0]

    def test_a_date_default_is_refused(self) -> None:
        """Only a *path's* value becomes a timestamp, so a date default is an instruction the engine
        cannot follow — better refused than silently ignored."""
        result = build_table_config({}, _rows(date=_row("$.ts", "datetime", "2026-01-01")))

        assert not result.ok
        assert result.config is None
        assert "cannot work" in result.errors["date"][0]

    def test_a_text_default_is_stored_verbatim(self) -> None:
        result = build_table_config({}, _rows(title=_row("$.name", "str", "Unnamed detection")))

        assert result.ok, result.errors
        assert result.config["fields"]["title"]["default"] == "Unnamed detection"

    def test_a_default_on_its_own_is_a_rule(self) -> None:
        """Explicit and therefore allowed: the row's default answers the target even with no paths —
        which does switch the literal-key fallback off for that target, since a v2 rule's paths are the
        whole candidate list."""
        result = build_table_config({}, _rows(type=_row("", "", "unknown")))

        assert result.ok, result.errors
        assert result.config["fields"]["type"] == {"default": "unknown"}

    def test_a_severity_default_is_canonicalised_from_severity_default(self) -> None:
        """`severity_default` is an accepted stored spelling (`_normalize` reads it); the table writes
        the canonical `default`, so the config does not carry both."""
        stored = {"version": 2, "fields": {"severity": {"paths": ["$.sev"], "severity_default": 3}}}

        result = build_table_config(stored, _stored_rows(stored))

        assert result.ok, result.errors
        rule = result.config["fields"]["severity"]
        assert rule.get("default") == 3
        assert "severity_default" not in rule


# --- Rejections keep one schema authority ------------------------------------


class TestTheSchemaStaysTheSingleAuthority:
    """`build_table_config` runs the candidate through `validate_config` and attributes each message to
    its row (`_error_target`), rather than re-implementing the rules."""

    def test_a_path_that_is_not_a_jsonpath_is_refused_and_attributed(self) -> None:
        result = build_table_config({}, _rows(title=_row("name")))

        assert not result.ok
        assert result.config is None
        assert "title" in result.errors
        assert "must start with $" in result.errors["title"][0]

    def test_an_unparseable_jsonpath_is_refused(self) -> None:
        result = build_table_config({}, _rows(title=_row("$.a[unclosed")))

        assert not result.ok
        assert result.config is None
        assert "not a valid JSONPath" in result.errors["title"][0]

    def test_a_transform_with_nothing_to_read_is_refused(self) -> None:
        """An empty row plus a chosen transform is a half-finished row, not a deletion: a deletion is
        the empty transform."""
        result = build_table_config({}, _rows(title=_row("", "severity_score")))

        assert not result.ok
        assert result.config is None
        assert "nothing to read" in result.errors["title"][0]

    def test_an_unknown_transform_is_refused(self) -> None:
        """A crafted POST, since the `<select>` cannot offer it — and it must not reach the database."""
        result = build_table_config({}, _rows(title=_row("$.name", "eval")))

        assert not result.ok
        assert result.config is None
        assert "unknown transform" in result.errors["title"][0]

    def test_a_whole_config_message_is_attributed_to_no_row(self) -> None:
        """`config_errors` renders as a page-level message: `""` is the "not about one row" key."""

        result = parse_json_config(
            '{"version": 2, "fields": {"title": {"paths": ["$.t"]}}, '
            '"value_maps": {"severity": {"Odd": 9}}}'
        )

        assert not result.ok
        assert set(result.errors) == {""}, "a value_maps failure belongs to the config, not a row"
        assert "must map to 1-4" in result.errors[""][0]

    def test_a_legacy_config_is_not_validated_for_value_maps(self) -> None:
        """Documented engine behaviour, pinned here because it surprises: the legacy branch of
        `validate_config` returns before `value_maps` is reached, and a legacy config has no transforms
        for `value_maps` to feed (`_normalize` ignores it). It is inert today and *live* the moment the
        table converts the config — which is why it is preserved rather than dropped."""

        result = parse_json_config('{"title": "$.name", "value_maps": {"severity": {"Odd": 9}}}')

        assert result.ok, result.errors
        assert result.config == {"title": "$.name", "value_maps": {"severity": {"Odd": 9}}}
        # …and it is refused as soon as it is v2, i.e. on the first table save that keeps it.
        assert build_table_config(result.config, _rows(title=_row("$.name"))).errors

    def test_an_empty_table_over_an_empty_config_has_nothing_to_validate(self) -> None:
        result = build_table_config({}, _rows())

        assert result.errors == {}
        assert result.ok


# --- The advanced JSON box ---------------------------------------------------


class TestTheJsonBox:
    """The second view of the same object: it accepts anything `validate_config` accepts, including the
    legacy shape, because it is the escape hatch for keys the table does not own."""

    def test_an_empty_box_is_refused_rather_than_read_as_clear(self) -> None:
        """`{}` is how you clear the mapping; an empty textarea is far more likely a lost paste."""

        result = parse_json_config("   \n ")

        assert not result.ok
        assert result.config is None
        assert "{} to clear the mapping" in result.errors[""][0]

    def test_invalid_json_names_the_position(self) -> None:
        result = parse_json_config('{"version": 2,\n "fields": }')

        assert not result.ok
        assert "Not valid JSON" in result.errors[""][0]
        assert "line 2" in result.errors[""][0]

    def test_a_non_object_is_refused(self) -> None:
        result = parse_json_config("[1, 2]")

        assert not result.ok
        assert "must be a JSON object" in result.errors[""][0]

    def test_an_empty_object_clears_the_mapping(self) -> None:
        result = parse_json_config("{}")

        assert result.ok, result.errors
        assert result.config == {}

    def test_a_v2_config_without_fields_is_valid(self) -> None:
        """Regression for the branch fix: `_normalize` reads `{"version": 2}` as v2-with-no-rules, so
        validation must not read it as legacy and then be surprised by what the engine does."""

        result = parse_json_config('{"version": 2}')

        assert result.ok, result.errors
        assert result.config == {"version": 2}

    def test_a_legacy_config_is_still_acceptable(self) -> None:
        """The box is the one place a legacy config can be *kept* as-is, and its values must still be
        paths the engine can compile. Its errors are attributed to the row that names the key, so the
        operator is pointed at the line, not at the page."""

        assert parse_json_config('{"title": "$.name"}').config == {"title": "$.name"}

        refused = parse_json_config('{"title": "name"}')

        assert not refused.ok
        assert "starts with $" in refused.errors["title"][0]

    def test_a_valid_v2_config_round_trips_unchanged(self) -> None:
        import json

        result = parse_json_config(json.dumps(STORED))

        assert result.ok, result.errors
        assert result.config == STORED


# --- changed_keys (`AuditLog.details`) ---------------------------------------


class TestChangedKeysRecordsWhatChanged:
    """The audit row records *which* keys moved and never their values (ADR-004). The audit row must not
    become a second copy of the mapping."""

    def test_a_changed_path_list_names_the_leaf(self) -> None:
        before = {"version": 2, "fields": {"title": {"paths": ["$.a"], "transform": "str"}}}
        after = {"version": 2, "fields": {"title": {"paths": ["$.b"], "transform": "str"}}}

        assert changed_keys(before, after) == ["fields.title.paths"]

    def test_a_removed_rule_names_the_target(self) -> None:
        assert changed_keys(
            STORED, {"version": 2, "fields": {"title": STORED["fields"]["title"]}}
        ) == ["fields.severity"]

    def test_a_top_level_addition_is_named(self) -> None:
        assert changed_keys({}, {"version": 2, "fields": {}}) == ["fields", "version"]

    def test_no_change_is_an_empty_list(self) -> None:
        assert changed_keys(STORED, STORED) == []

    def test_a_shape_change_reports_the_key_not_its_insides(self) -> None:
        """The audit records *that* a key changed and never the values, so a whole subtree arriving is
        one entry."""

        assert changed_keys({}, {"value_maps": {"severity": {"Odd": 2}}}) == ["value_maps"]


# --- Rendering the table -----------------------------------------------------


class TestThePreviewRows:
    def test_every_target_has_a_row_in_a_fixed_order(self) -> None:
        rows = editor_rows(STORED)

        assert [row["target"] for row in rows] == list(TARGETS)
        assert next(row["label"] for row in rows) == "Title"

    def test_a_stored_rule_pre_fills_its_row(self) -> None:
        rows = {row["target"]: row for row in editor_rows(STORED)}

        assert rows["title"]["paths"] == "$.name\n$.display_name"
        assert rows["title"]["transform"] == "str"
        assert rows["severity"]["transform"] == "severity_score"
        assert rows["date"]["paths"] == ""

    def test_every_row_advertises_its_literal_key_fallback(self) -> None:
        rows = {row["target"]: row for row in editor_rows(STORED)}

        assert rows["title"]["literal_keys"] == list(LEGACY_KEYS["title"])
        assert "message" in rows["title"]["literal_keys"]

    def test_a_legacy_config_pre_fills_as_its_v2_equivalent(self) -> None:
        """The conversion the page warns about, shown before it happens."""

        rows = {row["target"]: row for row in editor_rows({"title": "$.name"})}

        assert rows["title"]["paths"] == "$.name"
        assert rows["title"]["transform"] == "str"

    def test_a_rejected_submission_shows_the_operators_own_input(self) -> None:
        rows = {
            row["target"]: row
            for row in editor_rows(
                STORED,
                submitted={"title": {"paths": "name", "transform": "str", "default": ""}},
                errors={"title": ("fields.title.paths: 'name' must start with $",)},
            )
        }

        assert rows["title"]["paths"] == "name", "the input must survive the 200 re-render"
        assert rows["title"]["errors"] == ["fields.title.paths: 'name' must start with $"]
        assert rows["severity"]["paths"] == "$.severity", (
            "an unsubmitted row keeps the stored value"
        )
        assert rows["severity"]["errors"] == []

    def test_a_corrupt_stored_config_still_renders(self) -> None:
        """`_normalize` never raises, so opening the page must work even when the column is a typo
        someone is trying to fix."""

        for corrupt in ({"fields": "nope"}, "not-a-dict", {"version": 2, "fields": None}, None):
            rows = editor_rows(corrupt)
            assert len(rows) == len(TARGETS)


# --- The page's own invariants -----------------------------------------------


class TestTheEditorCoversTheEngine:
    """The page's own invariants: a transform or a target the seed/engine gains must not be silently
    missing from the table (a `<select>` that cannot express a transform is a trap)."""

    def test_every_transform_is_offered_with_help_text(self) -> None:
        offered = transforms_for_editor()

        assert [name for name, _ in offered] == list(TRANSFORMS)
        assert all(help_text for _, help_text in offered), "a bare transform name is not help"
        assert set(TRANSFORM_HELP) == set(TRANSFORMS)

    def test_every_target_has_a_label(self) -> None:
        assert set(TARGET_LABELS) == set(TARGETS)
