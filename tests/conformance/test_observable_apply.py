"""Observable-only apply to a feed's existing unlinked open alerts (PLAN-2026-11-09)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alerts.models import Alert, AlertObservable, AlertStatus
from cases.models import Case, CaseStatus
from identity.models import AuditLog, Organisation
from ingest.models import IngestionSource
from ingest.observable_apply import apply_observable_rules, preview_observable_rules
from ingest.observable_rules import MAX_OBSERVABLE_CANDIDATES
from observables.alert_sync import sync_alert_observables
from observables.models import Observable, ObservableType

pytestmark = pytest.mark.django_db

CONFIG = {
    "version": 2,
    "fields": {"title": {"paths": ["$.name"]}},
    "observables": [{"type": "mail", "paths": ["$.email"]}],
}


def _status(value: str, stage: str) -> AlertStatus:
    status, _ = AlertStatus.objects.get_or_create(value=value, defaults={"stage": stage})
    return status


def _case(org: Organisation) -> Case:
    status, _ = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})
    return Case.objects.create(title="Linked", status=status, owner_org=org)


def _source(org: Organisation, slug: str = "observable-apply") -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=org,
        slug=slug,
        name=slug,
        mapping_config=CONFIG,
    )


def _alert(
    source: IngestionSource,
    *,
    ref: str = "ref-1",
    email: str = "analyst@example.test",
    status: AlertStatus | None = None,
    case: Case | None = None,
    payload: dict | None = None,
) -> Alert:
    return Alert.objects.create(
        title="Stored title",
        description="Stored description",
        summary="Stored summary",
        severity=2,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        type="phishing",
        source="wire-source",
        source_ref=ref,
        status=status or _status("New", "New"),
        raw_payload={"name": "New title", "email": email} if payload is None else payload,
        ingestion_source=source,
        case=case,
        correlation_key=f"kept-{ref}",
        ingestion_warnings={"mapping": [{"field": "title", "reason": "no_value"}]},
        owner_org=source.organisation,
    )


@pytest.fixture
def org() -> Organisation:
    return Organisation.objects.create(name="Observable Apply Org")


@pytest.fixture
def mail_type() -> ObservableType:
    obs_type, _ = ObservableType.objects.get_or_create(name="mail", defaults={"name": "mail"})
    return obs_type


class TestPreview:
    def test_it_uses_the_same_candidate_predicate_and_writes_nothing(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        sibling = _source(org, slug="other-feed")
        fresh = _alert(feed, ref="fresh")
        triaged = _alert(feed, ref="triaged", status=_status("Triaged", "InProgress"))
        _alert(feed, ref="linked", case=_case(org))
        _alert(feed, ref="dismissed", status=_status("Dismissed", "Closed"))
        _alert(feed, ref="imported", status=_status("Imported", "Imported"))
        _alert(sibling, ref="elsewhere")
        before = (
            feed.updated_at,
            AlertObservable.objects.count(),
            Observable.objects.count(),
            AuditLog.objects.count(),
            Alert.objects.get(pk=fresh.pk).title,
            Alert.objects.get(pk=triaged.pk).title,
        )

        preview = preview_observable_rules(feed, CONFIG)

        assert preview.candidates == 2
        assert preview.by_type == {
            "mail": type(preview.by_type["mail"])(found=2, new_links=2, existing_links=0)
        }
        assert preview.alerts_with_values == 2
        assert not preview.over_cap
        assert before == (
            IngestionSource.objects.get(pk=feed.pk).updated_at,
            AlertObservable.objects.count(),
            Observable.objects.count(),
            AuditLog.objects.count(),
            Alert.objects.get(pk=fresh.pk).title,
            Alert.objects.get(pk=triaged.pk).title,
        )

    def test_an_existing_link_is_counted_as_already_present(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        alert = _alert(feed)
        sync_alert_observables(alert, config=CONFIG)

        preview = preview_observable_rules(feed, CONFIG)

        row = preview.by_type["mail"]
        assert (row.found, row.new_links, row.existing_links) == (1, 0, 1)

    def test_two_raw_values_that_normalize_identically_are_one_new_and_one_existing(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        config = {
            "version": 2,
            "fields": {},
            "observables": [{"type": "mail", "paths": ["$.a", "$.b"]}],
        }
        _alert(feed, payload={"a": "User@Example.TEST", "b": "user@example.test"})

        preview = preview_observable_rules(feed, config)
        result = apply_observable_rules(feed, config)

        row = preview.by_type["mail"]
        assert (row.found, row.new_links, row.existing_links) == (2, 1, 1)
        assert (result.created, result.existing) == (1, 1)
        assert AlertObservable.objects.count() == 1

    def test_unknown_types_are_reported_and_never_created(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        _alert(feed, payload={"future": "x"})
        config = {
            "version": 2,
            "fields": {},
            "observables": [{"type": "future-type", "paths": ["$.future"]}],
        }

        preview = preview_observable_rules(feed, config)

        assert preview.unknown_types == ("future-type",)
        assert preview.alerts_with_values == 0
        assert not ObservableType.objects.filter(name="future-type").exists()

    def test_truncation_is_reported(self, org: Organisation, mail_type: ObservableType) -> None:
        feed = _source(org)
        values = [f"analyst-{i}@example.test" for i in range(MAX_OBSERVABLE_CANDIDATES + 1)]
        _alert(feed, payload={"items": values})
        config = {
            "version": 2,
            "fields": {},
            "observables": [{"type": "mail", "paths": ["$.items[*]"]}],
        }

        preview = preview_observable_rules(feed, config)

        assert preview.truncated_alerts == 1
        assert preview.by_type["mail"].found == MAX_OBSERVABLE_CANDIDATES

    def test_over_cap_reports_the_full_set_but_scans_only_the_limit(
        self, org: Organisation, mail_type: ObservableType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ingest import remap

        monkeypatch.setattr(remap, "MAX_REMAP_ALERTS", 1)
        feed = _source(org)
        _alert(feed, ref="one")
        _alert(feed, ref="two")

        preview = preview_observable_rules(feed, CONFIG, limit=1)

        assert preview.candidates == 2
        assert preview.over_cap is True
        assert preview.by_type["mail"].found == 1
        assert AlertObservable.objects.count() == 0


class TestApply:
    def test_it_is_append_only_and_idempotent(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        _alert(feed, ref="one")
        _alert(feed, ref="two")

        first = apply_observable_rules(feed, CONFIG)
        second = apply_observable_rules(feed, CONFIG)

        assert (first.candidates, first.created, first.existing, first.alerts_with_values) == (
            2,
            2,
            0,
            2,
        )
        assert (second.created, second.existing) == (0, 2)
        assert AlertObservable.objects.count() == 2
        assert Observable.objects.count() == 1

    def test_removing_a_rule_does_not_remove_evidence(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        _alert(feed)
        apply_observable_rules(feed, CONFIG)

        apply_observable_rules(
            feed,
            {"version": 2, "fields": CONFIG["fields"], "observables": []},
        )

        assert AlertObservable.objects.count() == 1

    def test_it_never_rewrites_fields_or_mapping_records(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        alert = _alert(feed)
        before = Alert.objects.get(pk=alert.pk)

        apply_observable_rules(feed, CONFIG)

        after = Alert.objects.get(pk=alert.pk)
        for name in ("title", "description", "summary", "severity", "date", "status", "case"):
            assert getattr(after, name) == getattr(before, name), f"{name} was rewritten"
        assert after.ingestion_warnings == before.ingestion_warnings

    def test_excluded_alerts_receive_no_links(
        self, org: Organisation, mail_type: ObservableType
    ) -> None:
        feed = _source(org)
        sibling = _source(org, slug="sibling")
        included = _alert(feed, ref="included")
        _alert(feed, ref="linked", case=_case(org))
        _alert(feed, ref="dismissed", status=_status("Dismissed", "Closed"))
        _alert(feed, ref="imported", status=_status("Imported", "Imported"))
        _alert(sibling, ref="elsewhere")

        result = apply_observable_rules(feed, CONFIG)

        assert result.candidates == 1
        assert list(AlertObservable.objects.values_list("alert_id", flat=True)) == [included.pk]
        assert not AlertObservable.objects.exclude(alert_id=included.pk).exists()
