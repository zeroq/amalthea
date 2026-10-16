"""The JSON-path mapping engine (plan §8 P1, AC1—AC7).

The engine exists because the dev data contains a `Critical` credential-theft detection that reads
`Medium / Alert` in the UI: the payload said `severity_name: "Critical"` and `severity: 90`, and the
receiver could only turn an integer 1—4 into a severity. These tests pin the two things that fix
buys — the transforms, and the promise that nothing an operator types can 500 the receiver — and,
just as importantly, they pin the *old* behaviour that must survive: a source with a legacy flat
config, or no config at all, ingests exactly as it did before the engine had a schema (AC4, AC5).

The engine is pure, so most of this file needs no database at all. The two tests that go through the
receiver are marked `django_db` explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse

from alerts.models import Alert
from identity.models import IngestionToken, Organisation
from ingest.mapping import (
    DEFAULT_SCORE_BANDS,
    TARGETS,
    AlertDraft,
    apply_mapping,
    validate_config,
)
from ingest.models import IngestionSource

# The payload a real CrowdStrike EDR connector posts, trimmed to the fields the rules use. Kept
# verbatim (including the nanosecond timestamp) so the contract test fails if the engine stops
# understanding what the feed actually sends.
CROWDSTRIKE = {
    "name": "HiveCredTheft",
    "display_name": "Credential theft (Hive)",
    "description": "A process opened and read the SAM hive.\n\n  Host: WKS-01",
    "severity": 90,
    "severity_name": "Critical",
    "confidence": 80,
    "timestamp": "2026-10-15T15:31:38.403385204Z",
    "id": "ind:2f1c9a",
    "type": "ldt",
    "tactic": "CredentialAccess",
    "technique": "T1003",
    "host_names": ["WKS-01"],
    "user_name": "j.doe",
    "sha256": "a" * 64,
}

CROWDSTRIKE_RULES = {
    "version": 2,
    "fields": {
        "title": {"paths": ["$.name", "$.display_name"], "transform": "str", "max_length": 200},
        "description": {"paths": ["$.description"], "transform": "str", "max_length": 2000},
        "severity": {"paths": ["$.severity"], "transform": "severity_score"},
        "date": {"paths": ["$.timestamp"], "transform": "datetime"},
        "type": {"paths": ["$.type"], "transform": "str"},
        "source_ref": {"paths": ["$.id"], "transform": "str"},
    },
}


# --- AC1: purity ------------------------------------------------------------


def test_the_engine_touches_no_database(monkeypatch: pytest.MonkeyPatch, db: None) -> None:
    """AC1: `apply_mapping` is usable from the preview dialog, which persists nothing.

    Asserted structurally rather than by inspection: every cursor entry point is replaced by a
    tripwire, and the engine still answers. A future "let's look the source up here" fails loudly.
    """
    from django.db.backends.utils import CursorWrapper

    calls: list[str] = []

    def record(*args: object, **kwargs: object) -> None:
        calls.append("cursor")

    for method in ("execute", "executemany", "callproc"):
        monkeypatch.setattr(CursorWrapper, method, record)

    draft = apply_mapping(CROWDSTRIKE_RULES, CROWDSTRIKE)

    # The patch is lifted before the assertion so the fixture's own teardown (`PRAGMA
    # foreign_key_check`) has a working cursor back.
    monkeypatch.undo()

    assert calls == [], "apply_mapping must not touch the database"
    assert draft.title == "HiveCredTheft"
    assert draft.severity == 4


def test_apply_mapping_does_not_mutate_its_inputs() -> None:
    """A pure function cannot corrupt the config it is handed — the source row reuses it."""
    config = {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"], "transform": "str"}},
    }
    payload = {"name": "HiveCredTheft", "nested": {"a": [1, 2]}}
    config_before = repr(config)
    payload_before = repr(payload)
    apply_mapping(config, payload)
    assert repr(config) == config_before
    assert repr(payload) == payload_before


# --- AC2: severity_name -----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Critical", 4),
        ("critical", 4),
        ("  CRITICAL  ", 4),
        ("Urgent", 4),
        ("High", 3),
        ("Malicious", 3),
        ("Medium", 2),
        ("Suspicious", 2),
        ("Low", 1),
        ("Informational", 1),
        ("Unknown", 1),
        ("3", 3),
        ("high-severity", None),
        ("", None),
        (None, None),
        (True, None),
    ],
)
def test_severity_name_synonyms(value: object, expected: int | None) -> None:
    """AC2: the synonym table, case- and whitespace-insensitive, unknown → unanswered."""
    config = {
        "version": 2,
        "fields": {"severity": {"paths": ["$.level"], "transform": "severity_name"}},
    }
    draft = apply_mapping(config, {"level": value}, default_severity=1)
    assert draft.severity == (expected if expected is not None else 1)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0, 1), (19, 1), (20, 2), (49, 2), (50, 3), (79, 3), (80, 4), (90, 4), (100, 4)],
)
def test_severity_score_bands(score: int, expected: int) -> None:
    """AC2: 0—100 → 1—4 at the documented boundaries (the dev feed sends 90 → Critical)."""
    config = {
        "version": 2,
        "fields": {"severity": {"paths": ["$.score"], "transform": "severity_score"}},
    }
    assert apply_mapping(config, {"score": score}).severity == expected


def test_severity_score_bands_are_overridable_per_rule() -> None:
    config = {
        "version": 2,
        "fields": {
            "severity": {
                "paths": ["$.score"],
                "transform": "severity_score",
                "bands": [[10, 4]],
            }
        },
    }
    assert apply_mapping(config, {"score": 15}).severity == 4
    assert apply_mapping(config, {"score": 5}).severity == 1


def test_a_score_outside_0_100_is_not_a_score() -> None:
    """`5` almost certainly means the model's own 1—4 vocabulary, not "5 out of 100"."""
    config = {
        "version": 2,
        "fields": {"severity": {"paths": ["$.score"], "transform": "severity_score"}},
    }
    assert apply_mapping(config, {"score": 500}, default_severity=2).severity == 2


def test_value_maps_win_over_the_synonym_table() -> None:
    config = {
        "version": 2,
        "fields": {"severity": {"paths": ["$.level"], "transform": "severity_name"}},
        "value_maps": {"severity": {"low": 3}},
    }
    assert apply_mapping(config, {"level": "low"}).severity == 3


# --- AC3: the real feed -----------------------------------------------------


def test_the_crowdstrike_contract_payload_maps_end_to_end() -> None:
    """AC3: the stored dev payload, mapped by the rules the plan specifies.

    Before the engine this alert was `Alert / Medium`; the whole point of the wave is this assertion.
    """
    draft = apply_mapping(CROWDSTRIKE_RULES, CROWDSTRIKE)

    assert draft.title == "HiveCredTheft"
    assert draft.severity == 4
    assert draft.type == "ldt"
    assert draft.source_ref == "ind:2f1c9a"
    assert draft.date == datetime(2026, 10, 15, 15, 31, 38, 403385, tzinfo=UTC)
    assert draft.description is not None and draft.description.startswith(
        "A process opened and read"
    )
    assert draft.summary is None
    assert draft.warnings == ()


def test_the_same_payload_may_read_severity_from_its_name() -> None:
    """Either half of the vendor's severity may drive the column; `severity_name` needs no bands."""
    rules = {
        "version": 2,
        "fields": {"severity": {"paths": ["$.severity_name"], "transform": "severity_name"}},
    }
    assert apply_mapping(rules, CROWDSTRIKE).severity == 4


def test_title_falls_through_to_the_second_candidate() -> None:
    """AC2: ordered candidates — the first *answering* path wins, not the first path."""
    rules = {
        "version": 2,
        "fields": {"title": {"paths": ["$.display_name", "$.name"], "transform": "str"}},
    }
    assert apply_mapping(rules, {"name": "HiveCredTheft"}).title == "HiveCredTheft"
    assert apply_mapping(rules, {"display_name": "Hive", "name": "HiveCredTheft"}).title == "Hive"
    # An empty string is not an answer, so the next candidate gets a turn.
    assert (
        apply_mapping(rules, {"display_name": "  ", "name": "HiveCredTheft"}).title
        == "HiveCredTheft"
    )


def test_max_length_truncates_and_whitespace_is_collapsed() -> None:
    rules = {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"], "transform": "str", "max_length": 10}},
    }
    assert apply_mapping(rules, {"name": "  Hive   CredTheft  "}).title == "Hive CredT"


# --- AC6: defaulting and precedence -----------------------------------------


def test_rule_default_beats_the_source_default() -> None:
    rules = {
        "version": 2,
        "fields": {
            "severity": {"paths": ["$.severity"], "transform": "severity_score", "default": 4}
        },
    }
    assert apply_mapping(rules, {}, default_severity=1).severity == 4


def test_the_source_default_is_used_when_the_rule_says_nothing() -> None:
    """AC6: `IngestionSource.default_severity` is honoured — it used to be ignored entirely."""
    rules = {"version": 2, "fields": {"severity": {"paths": ["$.severity"], "transform": "int"}}}
    draft = apply_mapping(rules, {"severity": "Critical"}, default_severity=3)
    assert draft.severity == 3
    assert any(w.startswith("severity_defaulted:") for w in draft.warnings), draft.warnings


def test_the_module_default_is_low_medium_not_silence() -> None:
    draft = apply_mapping({}, {"nothing": "here"})
    assert draft.severity == 2


# --- AC4: the legacy path, unchanged ----------------------------------------


def test_legacy_flat_config_reads_jsonpaths_then_literal_keys() -> None:
    """AC4: `{"title": "$.subject"}` behaved this way before the engine and still does."""
    mapping = {"title": "$.subject", "severity": "$.severity", "sourceRef": "$.id"}
    draft = apply_mapping(mapping, CROWDSTRIKE)
    assert draft.title is None  # `$.subject` misses, and CrowdStrike has no literal `title`
    assert draft.source_ref == "ind:2f1c9a"

    payload = {"subject": "Phish", "severity": 3, "id": "evt-9", "title": "ignored"}
    draft = apply_mapping(mapping, payload)
    assert draft.title == "Phish"
    assert draft.severity == 3
    assert draft.source_ref == "evt-9"


def test_legacy_literal_fallback_keeps_the_old_key_chain() -> None:
    """`title` also answers to `summary` and `message`; `type` to `event`/`alertType`.

    These chains are the old `_mapped(payload, mapping, "title", "summary", "message")` call sites. A
    source that was never configured depends on them entirely, so they are part of the contract, not
    an implementation detail.
    """
    assert apply_mapping({}, {"message": "from the message key"}).title == "from the message key"
    assert apply_mapping({}, {"summary": "from the summary key"}).title == "from the summary key"
    # Earlier keys win.
    assert apply_mapping({}, {"title": "t", "message": "m"}).title == "t"
    assert apply_mapping({}, {"event": "mailbox.rule.created"}).type == "mailbox.rule.created"
    assert apply_mapping({}, {"alertType": "phishing"}).type == "phishing"


def test_an_unconfigured_source_still_ingests() -> None:
    """AC4.3/AC5: no config, no crash — literal keys, `generic` type, a recorded default."""
    draft = apply_mapping(
        {}, {"title": "Suspicious inbox rule", "severity": 3, "sourceRef": "evt-1"}
    )
    assert draft.title == "Suspicious inbox rule"
    assert draft.severity == 3
    assert draft.source_ref == "evt-1"
    assert draft.type is None  # the caller's `or "generic"` default still applies


def test_legacy_severity_is_coerced_into_range() -> None:
    """`_coerce_severity`'s old job: an out-of-range or non-numeric severity defaults, loudly."""
    assert apply_mapping({"severity": "$.severity"}, {"severity": "4"}).severity == 4
    draft = apply_mapping({"severity": "$.severity"}, {"severity": 91}, default_severity=1)
    assert draft.severity == 1
    assert any(w.startswith("severity_defaulted:") for w in draft.warnings)


def test_an_empty_legacy_value_falls_through_to_the_literal_key() -> None:
    """A rule that matched an empty string has not answered the question."""
    mapping = {"title": "$.subject"}
    assert apply_mapping(mapping, {"subject": "   ", "title": "Literal"}).title == "Literal"


# --- AC5: nothing raises ----------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        "not a dict",
        42,
        {"title": 5},
        {"titel": "$.title"},  # a typo is not a crash, it is simply not a rule
        {"title": "$.[oops"},
        {"title": "$.a.b[c"},
        {"version": 2},
        {"version": 2, "fields": None},
        {"version": 2, "fields": "nope"},
        {"version": 2, "fields": {"nope": {"paths": ["$.x"]}}},
        {"version": 2, "fields": {"title": "not-a-rule"}},
        {"version": 2, "fields": {"title": {"paths": "$.title"}}},
        {"version": 2, "fields": {"title": {"paths": [5, None, "$.ok"]}}},
        {"version": 2, "fields": {"title": {"paths": ["$.x"], "transform": "nonsense"}}},
        {"version": 2, "fields": {"title": {"paths": ["$.x"], "max_length": -1}}},
        {
            "version": 2,
            "fields": {
                "severity": {"paths": ["$.x"], "transform": "severity_score", "bands": "wide"}
            },
        },
        {
            "version": 2,
            "fields": {
                "severity": {"paths": ["$.x"], "transform": "severity_score", "bands": [[1, 2, 3]]}
            },
        },
        {"version": 2, "fields": {"date": {"paths": ["$.x"], "transform": "datetime"}}},
        {"version": 2, "value_maps": {"severity": "nope"}},
        {"version": 2, "fields": {"title": {"paths": ["$.x"]}}, "value_maps": [1, 2]},
    ],
)
@pytest.mark.parametrize("payload", [None, 5, "string", [], {"a": 1}, CROWDSTRIKE])
def test_no_config_and_no_payload_can_raise(config: object, payload: object) -> None:
    """AC5: an operator's typo, or a sender's non-object JSON, must never surface as a 500."""
    draft = apply_mapping(config, payload)  # type: ignore[arg-type]
    assert isinstance(draft, AlertDraft)
    assert draft.severity in (None, 1, 2, 3, 4)


def test_a_malformed_config_does_not_stop_its_valid_rules() -> None:
    """Degrade per rule: one broken field must not blind the other six."""
    config = {
        "version": 2,
        "fields": {
            "title": {"paths": ["$.name"], "transform": "str"},
            "severity": {"paths": ["$.severity"], "transform": "sevrtiy"},  # typo
            "type": {"paths": ["$.type"], "transform": "str"},
        },
    }
    draft = apply_mapping(config, CROWDSTRIKE, default_severity=1)
    assert draft.title == "HiveCredTheft"
    assert draft.type == "ldt"
    assert draft.severity == 1


def test_a_path_that_misses_is_reported_not_guessed() -> None:
    """AC7: a rule that found nothing says so, with the paths it tried."""
    config = {"version": 2, "fields": {"title": {"paths": ["$.subject"], "transform": "str"}}}
    draft = apply_mapping(config, {"name": "Hive"})
    assert draft.title is None
    # Severity is reported too, and unconditionally: it is the field that decides triage order.
    assert next(w.as_dict() for w in draft.field_warnings) == {
        "field": "title",
        "reason": "not_found",
        "paths": ["$.subject"],
    }
    assert any(w.startswith("title_unmapped:") for w in draft.warnings)


def test_structured_warnings_preview_the_offending_value() -> None:
    config = {
        "version": 2,
        "fields": {"severity": {"paths": ["$.level"], "transform": "severity_name"}},
    }
    draft = apply_mapping(config, {"level": "high-severity"}, default_severity=2)
    assert draft.severity == 2
    (warning,) = [w for w in draft.field_warnings if w.reason == "unknown_value"]
    assert warning.as_dict()["value_preview"] == "high-severity"


def test_an_unconfigured_field_is_not_a_warning() -> None:
    """Silence here is honest: no rule means the operator never expected that field."""
    draft = apply_mapping({"title": "$.name"}, {"name": "Hive"})
    assert [w.target for w in draft.field_warnings] == ["severity"]


# --- validate_config (the form's half of the contract) ----------------------


def test_a_good_config_validates_and_a_bad_one_names_the_field() -> None:
    assert validate_config(CROWDSTRIKE_RULES) == []
    errors = validate_config(
        {
            "version": 2,
            "fields": {
                "nope": {"paths": ["$.x"]},
                "title": {"paths": ["subject"], "transform": "str"},
                "severity": {"paths": ["$.x"], "transform": "severity_score"},
            },
            "value_maps": {"severity": {"low": 9}},
        }
    )
    joined = "\n".join(errors)
    assert "fields.nope" in joined
    assert "must start with $" in joined
    assert "value_maps.severity" in joined


def test_validate_config_rejects_a_legacy_path_that_is_not_a_path() -> None:
    errors = validate_config({"title": "subject"})
    assert errors and "JSONPath" in errors[0]


def test_validate_config_tolerates_an_empty_config() -> None:
    """AC4.3 — "don't map anything" is a legitimate setting, not an error."""
    assert validate_config({}) == []
    assert validate_config(None) == []


def test_the_documented_defaults_are_what_the_plan_says() -> None:
    """A single place to notice that the bands or the target list moved."""
    assert DEFAULT_SCORE_BANDS == ((20, 2), (50, 3), (80, 4))
    assert TARGETS == ("title", "description", "summary", "severity", "date", "type", "source_ref")


# --- The receiver, end to end ----------------------------------------------


@pytest.fixture
def crowdstrike_source(default_org: Organisation, db: None) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=default_org,
        slug="crowdstrike-edr",
        name="Crowdstrike EDR",
        mapping_config=CROWDSTRIKE_RULES,
    )


@pytest.mark.django_db
def test_the_receiver_stores_what_the_rules_say(
    crowdstrike_source: IngestionSource, default_org: Organisation
) -> None:
    """AC3 + AC6, through the real endpoint: the stored alert is the one the payload describes."""
    _token, plaintext = IngestionToken.create_token(
        source=crowdstrike_source, organisation=default_org, name="edr"
    )
    response = Client().post(
        reverse("alerts-webhook"),
        data=CROWDSTRIKE,
        content_type="application/json",
        headers={"X-Webhook-Secret": plaintext},
    )
    assert response.status_code == 201, response.content

    alert = Alert.objects.get(owner_org=default_org)
    assert alert.title == "HiveCredTheft"
    assert alert.severity == 4
    assert alert.type == "ldt"
    assert alert.source_ref == "ind:2f1c9a"
    assert alert.date == datetime(2026, 10, 15, 15, 31, 38, 403385, tzinfo=UTC)
    assert alert.ingestion_warnings["mapping"] == []
    # The old `severity_defaulted` blanket warning is gone: nothing was defaulted.
    assert not any(
        w.startswith("severity_defaulted:") for w in alert.ingestion_warnings["warnings"]
    ), alert.ingestion_warnings


@pytest.mark.django_db
def test_the_receiver_honours_the_sources_own_severity_floor(
    crowdstrike_source: IngestionSource, default_org: Organisation
) -> None:
    """AC6: `IngestionSource.default_severity` reaches the alert (it used to be ignored)."""
    crowdstrike_source.mapping_config = {
        "version": 2,
        "fields": {"title": {"paths": ["$.name"], "transform": "str"}},
    }
    crowdstrike_source.default_severity = 3
    crowdstrike_source.save()

    _token, plaintext = IngestionToken.create_token(
        source=crowdstrike_source, organisation=default_org, name="edr"
    )
    response = Client().post(
        reverse("alerts-webhook"),
        data=CROWDSTRIKE,
        content_type="application/json",
        headers={"X-Webhook-Secret": plaintext},
    )
    assert response.status_code == 201, response.content
    alert = Alert.objects.get(owner_org=default_org)
    assert alert.severity == 3
    assert any(w.startswith("severity_defaulted:") for w in alert.ingestion_warnings["warnings"]), (
        alert.ingestion_warnings
    )
