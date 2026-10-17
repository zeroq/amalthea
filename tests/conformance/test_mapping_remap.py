"""P7 — re-applying a mapping to a feed's existing alerts (plan §8 P7, AC17-AC19).

The receiver applies a mapping exactly once, at ingest. So when the *rules* were wrong, every alert the
feed accepted before the fix keeps the values the broken rules produced, and there is no way to repair
them short of re-sending the payloads. `ingest/remap.py` is that repair; this module pins its two
load-bearing claims.

* **AC17 — the candidate set is `this feed AND case IS NULL AND stage in (New, InProgress)`.** Nothing in
  the module widens it. An escalated alert is human-authored evidence rather than a mapping product
  (`alerts/escalation.py` preserves the row it was escalated with), and a `Dismissed`/`Imported` alert is
  a terminal decision rather than a mapping error. The exclusions are asserted *positively* — those rows
  must still hold what they held before the pass — so the test fails if the predicate changes shape
  rather than merely starting to skip more rows.
* **AC18/AC19 — the fields written are `title`/`description`/`summary`/`severity`/`date`, and only where
  the new mapping disagrees with what is stored.** `type`/`source_ref` are the dedupe key *and* the
  replay key (`uniq_alert_source_type_ref`), so rewriting them is how a re-map would collide or change an
  alert's identity (**MAP-9**); `correlation_key` is not a v2 field. A text field the new mapping does
  not answer keeps its value, so clearing a rule cannot retitle stored alerts (**MAP-10**); and a second
  pass over unchanged inputs writes nothing at all — not even an `updated_at`.

The page's two-step confirmation is a UI concern and lives in `test_settings_source_mapping.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alerts.models import Alert, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import Organisation
from ingest.models import IngestionSource
from ingest.remap import (
    REMAP_FIELDS,
    candidate_alerts,
    preview_alerts,
    remap_alerts,
    summarise_candidates,
)

pytestmark = pytest.mark.django_db

#: The *fixed* rules: one path per field the re-map may write. A test that cares about one column changes
#: that single rule, so nothing rides on a side effect of another.
RULES = {
    "version": 2,
    "fields": {
        "title": {"paths": ["$.name"]},
        "description": {"paths": ["$.details"]},
        "summary": {"paths": ["$.summary"]},
        "severity": {"paths": ["$.severity"], "transform": "severity_name"},
        "date": {"paths": ["$.ts"], "transform": "datetime"},
    },
}

STAMP = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

#: The payload `RULES` answers completely. `severity: "critical"` → 4, so a stored 2 must move.
PAYLOAD = {
    "name": "Mapped title",
    "details": "Mapped description",
    "summary": "Mapped summary",
    "severity": "critical",
    "ts": "2026-03-01T12:00:00Z",
}

#: What a pass under `RULES` leaves on an alert that was seeded with the "before" values.
EXPECTED = {
    "title": "Mapped title",
    "description": "Mapped description",
    "summary": "Mapped summary",
    "severity": 4,
    "date": STAMP,
}


def _status(value: str, stage: str) -> AlertStatus:
    """`get_or_create`, not an assumption that the seed migration ran — as the other suites do."""
    status, _ = AlertStatus.objects.get_or_create(value=value, defaults={"stage": stage})
    return status


def _mk_source(
    organisation: Organisation,
    *,
    slug: str = "remap-feed",
    mapping_config: dict | None = None,
    default_severity: int = 2,
) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=organisation,
        slug=slug,
        name=slug,
        mapping_config=RULES if mapping_config is None else mapping_config,
        default_severity=default_severity,
    )


def _mk_case(organisation: Organisation) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(title="Escalated", status=status, owner_org=organisation)


def _mk_alert(
    source: IngestionSource,
    *,
    source_ref: str = "ref-1",
    payload: dict | None = None,
    title: str = "Alert",
    description: str = "",
    summary: str = "",
    severity: int = 2,
    date: datetime = datetime(2024, 1, 1, tzinfo=UTC),
    status: AlertStatus | None = None,
    case: Case | None = None,
    warnings: dict | None = None,
) -> Alert:
    """An alert as ingest left it — i.e. carrying whatever the *old* rules produced.

    `source` is the wire string and is deliberately unlike the feed's slug: the re-map keys on the FK,
    and a test that confused the two would pass for the wrong reason.
    """
    return Alert.objects.create(
        title=title,
        description=description,
        summary=summary,
        severity=severity,
        date=date,
        type="phishing",
        source="sender-reported-name",
        source_ref=source_ref,
        status=status or _status("New", "New"),
        raw_payload=dict(PAYLOAD) if payload is None else dict(payload),
        ingestion_source=source,
        case=case,
        correlation_key=f"kept-{source_ref}",
        ingestion_warnings={} if warnings is None else warnings,
        owner_org=source.organisation,
    )


def _read(alert: Alert) -> Alert:
    """A fresh read, so a row the pass never wrote is compared against the database, not the instance."""
    return Alert.objects.get(pk=alert.pk)


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Remap Org")


@pytest.fixture
def feed(org: Organisation) -> IngestionSource:
    return _mk_source(org)


class TestTheCandidatePredicate:
    """AC17 — the predicate is the safety story, so both of its halves are asserted by exclusion."""

    def test_it_takes_the_feeds_unlinked_open_alerts(self, org: Organisation) -> None:
        feed = _mk_source(org, slug="target")
        sibling = _mk_source(org, slug="other-feed")

        fresh = _mk_alert(feed, source_ref="fresh")
        # `Triaged` is `stage="InProgress"`, so it is open and counts.
        triaged = _mk_alert(feed, source_ref="triaged", status=_status("Triaged", "InProgress"))
        # Excluded, one way or another.
        linked = _mk_alert(feed, source_ref="linked", case=_mk_case(org))
        dismissed = _mk_alert(feed, source_ref="dismissed", status=_status("Dismissed", "Closed"))
        imported = _mk_alert(feed, source_ref="imported", status=_status("Imported", "Imported"))
        elsewhere = _mk_alert(sibling, source_ref="fresh-elsewhere")

        result = remap_alerts(feed, RULES)

        assert result.candidates == 2
        assert result.updated == 2
        for alert in (fresh, triaged):
            assert _read(alert).severity == 4
            assert _read(alert).title == EXPECTED["title"]
        for alert in (linked, dismissed, imported, elsewhere):
            assert _read(alert).severity == 2, "a stage/case/feed exclusion must not be re-mapped"
            assert _read(alert).title == "Alert"

    def test_a_linked_alert_keeps_the_values_it_was_escalated_with(self, org: Organisation) -> None:
        feed = _mk_source(org)
        linked = _mk_alert(feed, source_ref="linked", case=_mk_case(org))

        assert candidate_alerts(feed).count() == 0
        remap_alerts(feed, RULES)

        assert _read(linked).title == "Alert"
        assert _read(linked).severity == 2

    def test_an_alert_with_no_feed_is_never_a_candidate(self, org: Organisation) -> None:
        """A TheHive-compatible POST has no `ingestion_source`, so no feed can re-map it."""
        feed = _mk_source(org)
        _mk_alert(feed, source_ref="mine")
        detached = Alert.objects.create(
            title="From the API",
            severity=2,
            type="phishing",
            source="api",
            source_ref="api-1",
            status=_status("New", "New"),
            raw_payload=dict(PAYLOAD),
            owner_org=org,
        )

        assert candidate_alerts(feed).count() == 1
        remap_alerts(feed, RULES)

        assert _read(detached).title == "From the API"
        assert _read(detached).severity == 2

    def test_the_summary_counts_candidates_per_status(self, org: Organisation) -> None:
        feed = _mk_source(org)
        _mk_alert(feed, source_ref="one")
        _mk_alert(feed, source_ref="two")
        _mk_alert(feed, source_ref="three", status=_status("Triaged", "InProgress"))
        _mk_alert(feed, source_ref="closed", status=_status("Dismissed", "Closed"))

        summary = summarise_candidates(feed)

        assert summary.candidates == 3
        assert summary.by_status == {"New": 2, "Triaged": 1}
        assert not summary.over_cap


class TestWhatItWrites:
    """AC18/AC19 — the fields it may write, and the ones it must leave readable."""

    def test_it_writes_every_mapped_field(self, feed: IngestionSource) -> None:
        alert = _mk_alert(feed, source_ref="stale")

        result = remap_alerts(feed, RULES)

        assert result.candidates == 1
        assert result.updated == 1
        assert result.unchanged == 0
        assert result.touched == list(REMAP_FIELDS)
        for target, value in EXPECTED.items():
            assert getattr(_read(alert), target) == value, f"{target} was not written"

    def test_it_leaves_the_identity_fields_alone(self, feed: IngestionSource) -> None:
        """MAP-9 — `type`/`source_ref` are both the dedupe key and the replay key."""
        alert = _mk_alert(feed, source_ref="keep-me")

        remap_alerts(feed, RULES)

        after = _read(alert)
        assert after.type == "phishing"
        assert after.source_ref == "keep-me"
        assert after.source == "sender-reported-name"
        assert after.correlation_key == "kept-keep-me"

    def test_a_text_field_the_new_mapping_cannot_answer_keeps_its_value(
        self, feed: IngestionSource
    ) -> None:
        """MAP-10 — the destructive-title guard. Clearing a rule must not blank stored text.

        A target the v2 config omits is *not* unanswered on its own: it falls through to the legacy list
        and the literal payload key (plan §7; `ingest.mapping._normalize`). So the honest way to write
        "the new mapping cannot answer this field" is a rule-less target **and** a payload that does not
        carry the key either — which is exactly the state a cleared rule leaves an alert in.

        The point of the test is that the row keeps its authored text. Ingest may fill an unanswered
        title with the literal `"Alert"`; a re-map must not, or clearing one rule would retitle every
        unlinked open alert of the feed (**MAP-10**).
        """
        only_severity = {
            "version": 2,
            "fields": {"severity": {"paths": ["$.severity"], "transform": "severity_name"}},
        }
        alert = _mk_alert(
            feed,
            source_ref="titled",
            payload={},
            title="Retitled by the first pass",
            description="Written by hand",
            severity=3,
        )

        result = remap_alerts(feed, only_severity)

        after = _read(alert)
        assert result.touched == ["severity"], "only the field the mapping answers may be written"
        assert after.title == "Retitled by the first pass"
        assert after.description == "Written by hand"
        assert after.severity == 2, "the feed's `default_severity` is the fallback"

    def test_severity_is_written_from_the_feeds_default_when_no_rule_answers_it(
        self, org: Organisation
    ) -> None:
        """Severity is never left unanswered: absent a rule it falls back to the feed's own floor."""
        feed = _mk_source(org, default_severity=1, mapping_config={"version": 2, "fields": {}})
        alert = _mk_alert(feed, source_ref="by-default", payload={}, severity=4)

        result = remap_alerts(feed, {"version": 2, "fields": {}})

        assert result.updated == 1
        assert result.touched == ["severity"]
        assert _read(alert).severity == 1

    def test_an_alert_already_at_the_mapped_values_is_not_written(
        self, feed: IngestionSource
    ) -> None:
        """The second pass over the same inputs writes nothing — not even `updated_at`."""
        alert = _mk_alert(feed, source_ref="already", **EXPECTED)

        before = _read(alert)
        result = remap_alerts(feed, RULES)
        after = _read(alert)

        assert result.candidates == 1
        assert result.updated == 0
        assert result.unchanged == 1
        assert result.touched == []
        assert after.updated_at == before.updated_at


class TestTheWarningsItLeaves:
    """AC19 — `mapping` describes the row (replace), `warnings` is its log (append)."""

    def test_mapping_is_replaced_and_the_log_is_appended(self, feed: IngestionSource) -> None:
        alert = _mk_alert(
            feed,
            source_ref="warned",
            warnings={
                "mapping": [{"field": "severity", "reason": "not_found"}],
                "warnings": ["severity_defaulted: $.severity → 2"],
            },
        )

        remap_alerts(feed, RULES)

        after = _read(alert).ingestion_warnings
        # Fully answered by `RULES`, so the stale `mapping` entry is gone rather than merely stale.
        assert after["mapping"] == []
        assert after["warnings"][0] == "severity_defaulted: $.severity → 2"
        assert len(after["warnings"]) == 2
        assert after["warnings"][1].startswith(
            "remapped: re-applied the mapping of feed 'remap-feed'"
        )
        assert "title" in after["warnings"][1]

    def test_a_rerun_does_not_grow_the_log(self, feed: IngestionSource) -> None:
        """The log line is written with the change, so an unchanged row keeps its history as it is."""
        alert = _mk_alert(feed, source_ref="twice")
        remap_alerts(feed, RULES)
        first = _read(alert).ingestion_warnings

        remap_alerts(feed, RULES)

        assert _read(alert).ingestion_warnings == first


class TestTheCap:
    """AC21 — the pass is bounded, and the bound is *reported* rather than silently applied.

    A "save & apply" that quietly drops its apply half is the worse failure, so the UI disables its
    confirm control over the cap and this module reports `over_cap` for it to read. Truncation via an
    explicit `limit` stays available for a caller that means it (a batch, a management command).
    """

    def test_a_feed_over_the_cap_says_so(self, feed: IngestionSource, monkeypatch) -> None:
        monkeypatch.setattr("ingest.remap.MAX_REMAP_ALERTS", 1)
        _mk_alert(feed, source_ref="one")
        _mk_alert(feed, source_ref="two")

        summary = summarise_candidates(feed)

        assert summary.candidates == 2
        assert summary.over_cap is True

    def test_a_pass_at_the_cap_is_not_over_it(self, feed: IngestionSource, monkeypatch) -> None:
        monkeypatch.setattr("ingest.remap.MAX_REMAP_ALERTS", 2)
        _mk_alert(feed, source_ref="one")
        _mk_alert(feed, source_ref="two")

        summary = summarise_candidates(feed)

        assert summary.candidates == 2
        assert summary.over_cap is False

    def test_a_truncating_pass_touches_exactly_the_limit(self, feed: IngestionSource) -> None:
        for index in range(3):
            _mk_alert(feed, source_ref=f"row-{index}")

        result = remap_alerts(feed, RULES, limit=2)

        assert result.candidates == 2
        assert result.updated == 2
        assert Alert.objects.filter(ingestion_source=feed, severity=4).count() == 2, (
            "the third row must be left as it was, not partially mapped"
        )


#: A rule that *runs* and produces a value the target cannot hold: `$.severity` holds the 0-100 vendor
#: score, and `str` turns 70 into `"70"`, which is neither a 1-4 severity nor one of the names. This is
#: the real-world shape the whole report exists for (a rule that looks right in the editor), so it is the
#: fixture the unresolved tests use rather than a contrived one.
UNREADABLE_SEVERITY: dict = {
    "version": 2,
    "fields": {"severity": {"paths": ["$.severity"], "transform": "str"}},
}


class TestTheRecordRefresh:
    """MAP-12 — a pass that changes no *field* can still be a pass that wrote something.

    `ingestion_warnings["mapping"]` describes the alert *as the rules currently see it*, so a rules change
    that lands on the same values still leaves a record naming paths the feed has stopped storing — the
    alert page would go on saying "severity: nothing at $.severity" under a severity that was read fine.
    Replacing that record is the write; the event log is not touched, because nothing happened to the
    alert (MAP-10).
    """

    def test_a_no_op_pass_replaces_a_stale_record_and_leaves_the_log_alone(
        self, feed: IngestionSource
    ) -> None:
        alert = _mk_alert(
            feed,
            source_ref="stale-record",
            title=EXPECTED["title"],
            description=EXPECTED["description"],
            summary=EXPECTED["summary"],
            severity=EXPECTED["severity"],
            date=STAMP,
            warnings={
                "mapping": [{"field": "severity", "reason": "not_found"}],
                "warnings": ["severity_defaulted: $.severity → 2"],
            },
        )

        result = remap_alerts(feed, RULES)

        assert result.updated == 0, "no field disagreed with the new mapping"
        assert result.unchanged == 1
        assert result.refreshed == 1, "the record was still rewritten"
        after = _read(alert).ingestion_warnings
        assert after["mapping"] == [], "RULES answers every field, so no unresolved entry survives"
        assert after["warnings"] == ["severity_defaulted: $.severity → 2"], (
            "the log records what happened to the alert, and nothing did"
        )

    def test_a_record_that_already_matches_is_not_rewritten(self, feed: IngestionSource) -> None:
        """The other half of MAP-12: `{}` from ingest and `{"mapping": [], "warnings": []}` are equal.

        Otherwise the first re-map of every alert a rule-less feed ever ingested would write, and the
        counts would call that "a pass that did something".
        """
        alert = _mk_alert(
            feed,
            source_ref="already-current",
            title=EXPECTED["title"],
            description=EXPECTED["description"],
            summary=EXPECTED["summary"],
            severity=EXPECTED["severity"],
            date=STAMP,
        )
        before = _read(alert)

        result = remap_alerts(feed, RULES)

        assert (result.updated, result.refreshed) == (0, 0)
        assert _read(alert).updated_at == before.updated_at

    def test_the_counts_add_up_over_a_mixed_pass(self, feed: IngestionSource) -> None:
        """All three outcomes in one pass: written, refreshed, and left entirely alone."""
        moved = _mk_alert(feed, source_ref="moves")  # seeded at the "before" values → written
        record_only = _mk_alert(
            feed,
            source_ref="record-only",
            title=EXPECTED["title"],
            description=EXPECTED["description"],
            summary=EXPECTED["summary"],
            severity=EXPECTED["severity"],
            date=STAMP,
            warnings={"mapping": [{"field": "title", "reason": "not_found"}], "warnings": []},
        )
        idle = _mk_alert(
            feed,
            source_ref="idle",
            title=EXPECTED["title"],
            description=EXPECTED["description"],
            summary=EXPECTED["summary"],
            severity=EXPECTED["severity"],
            date=STAMP,
            warnings={"mapping": [], "warnings": []},
        )
        idle_before = _read(idle)

        result = remap_alerts(feed, RULES)

        assert result.candidates == 3
        assert result.updated == 1, "only `moves` disagreed with the new mapping"
        assert result.unchanged == 2
        assert result.refreshed == 1, "`record-only` kept its values but not a stale record"
        assert result.candidates == result.updated + result.unchanged
        assert _read(record_only).ingestion_warnings["mapping"] == []
        assert _read(idle).updated_at == idle_before.updated_at
        assert _read(moved).title == EXPECTED["title"]


class TestTheUnresolvedReport:
    """AC25 — "0 updated" cannot say whether the rule was applied or unreadable; this does.

    A rule that produces nothing changes nothing: the target falls back to the feed's `default_severity`
    (or keeps the text it has, MAP-10), which is *identical* to the stored value whenever the alert was
    ingested under the same rule. Counting rows therefore cannot distinguish "already correct" from
    "cannot read the payload". Reporting the `FieldWarning` reason does, and names the fix.
    """

    def test_a_rule_that_produces_the_wrong_type_is_reported(self, feed: IngestionSource) -> None:
        alert = _mk_alert(feed, source_ref="unreadable", payload={"severity": 70})

        result = remap_alerts(feed, UNREADABLE_SEVERITY)

        assert result.updated == 0
        assert result.unresolved == {"severity": {"bad_type": 1}}
        assert _read(alert).severity == 2, "the feed's `default_severity` is what the operator sees"

    def test_a_path_that_holds_nothing_is_reported_as_not_found(
        self, feed: IngestionSource
    ) -> None:
        """A configured path that matches nothing is the *other* way a rule silently does nothing.

        `severity` is reported here as well, and that is the engine's standing rule rather than a quirk of
        this config: severity absence is *always* recorded, because it is the field that decides triage
        order and a silent fallback is how a Medium hides a Critical (`ingest.mapping`, the
        `severity_defaulted` line). A config with no severity rule therefore says so on every alert.
        """
        alert = _mk_alert(feed, source_ref="missing", payload={"severity": 90, "name": "T"})
        config = {
            "version": 2,
            "fields": {"description": {"paths": ["$.does_not_exist"]}},
        }

        result = remap_alerts(feed, config)

        assert result.unresolved == {
            "description": {"not_found": 1},
            "severity": {"not_found": 1},
        }
        # Nothing was written at all: the one configured rule read nothing, severity fell back to the
        # feed's default (which the alert already stored), and a target the config does not answer keeps
        # its stored text (MAP-10). This is exactly the pass that reports "0 updated" and needs a reason.
        assert result.touched == []
        assert result.updated == 0
        assert _read(alert).severity == 2
        assert _read(alert).title == "Alert"

    def test_a_configured_rule_that_answers_is_not_reported(self, feed: IngestionSource) -> None:
        _mk_alert(feed, source_ref="fine")

        result = remap_alerts(feed, RULES)

        assert result.unresolved == {}
        assert result.updated == 1

    def test_the_report_counts_alerts_not_rules(self, feed: IngestionSource) -> None:
        for index in range(3):
            _mk_alert(feed, source_ref=f"unreadable-{index}", payload={"severity": 70})

        result = remap_alerts(feed, UNREADABLE_SEVERITY)

        assert result.unresolved == {"severity": {"bad_type": 3}}
        assert result.candidates == 3


class TestThePreview:
    """AC24 — the dialog's numbers come from the same decision function the write uses.

    `preview_alerts` reads every candidate and writes none of them. It exists so the confirmation dialog
    can say what a pass *would* do per target — including where a rule produces nothing — instead of
    offering a generic "this affects N alerts" that an operator can only accept or refuse blind.
    """

    def test_it_reads_without_writing(self, feed: IngestionSource) -> None:
        alert = _mk_alert(feed, source_ref="previewed")
        before = _read(alert)

        preview = preview_alerts(feed, RULES)

        assert preview.candidates == 1
        assert preview.changes == dict.fromkeys(REMAP_FIELDS, 1)
        assert preview.unresolved == {}
        after = _read(alert)
        assert (after.title, after.severity, after.date) == (
            before.title,
            before.severity,
            before.date,
        )
        assert after.ingestion_warnings == before.ingestion_warnings
        assert after.updated_at == before.updated_at

    def test_it_matches_what_the_pass_then_writes(self, feed: IngestionSource) -> None:
        """The promise and the write are one function, so the numbers cannot drift apart."""
        _mk_alert(feed, source_ref="moves")
        _mk_alert(feed, source_ref="already-mapped", title=EXPECTED["title"], severity=4)

        preview = preview_alerts(feed, RULES)
        result = remap_alerts(feed, RULES)

        assert preview.candidates == result.candidates
        assert preview.changes == result.changed_fields
        assert preview.unresolved == result.unresolved
        # The one row that only needed a refresh is counted as unchanged, not as a change.
        assert preview.changes.get("title", 0) == result.changed_fields.get("title", 0) == 1

    def test_it_reports_the_rules_that_would_produce_nothing(self, feed: IngestionSource) -> None:
        _mk_alert(feed, source_ref="unreadable", payload={"severity": 70})

        preview = preview_alerts(feed, UNREADABLE_SEVERITY)

        assert preview.unresolved == {"severity": {"bad_type": 1}}
        assert preview.changes == {}, "a rule that reads nothing changes nothing"
        assert preview.candidates == 1

    def test_it_carries_the_cap_shape_the_page_already_reads(
        self, feed: IngestionSource, monkeypatch
    ) -> None:
        """The dialog's existing cap guard keeps reading `RemapSummary` through the preview."""
        monkeypatch.setattr("ingest.remap.MAX_REMAP_ALERTS", 1)
        _mk_alert(feed, source_ref="one")
        _mk_alert(feed, source_ref="two")

        preview = preview_alerts(feed, RULES)

        assert preview.candidates == 2
        assert preview.by_status == {"New": 2}
        assert preview.over_cap is True
