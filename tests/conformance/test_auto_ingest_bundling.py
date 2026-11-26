"""Conformance tests for automatic ingest-time alert bundling (12.3-fu1).

Covers plan AC-P5.1-P5.6, with emphasis on the service, replay/idempotence,
and the ledger/realtime trail. Settings/config conformance lives in
``tests/conformance/test_auto_ingest_bundling_settings.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import channels.db
import pytest
from asgiref.sync import async_to_sync, sync_to_async
from channels.testing import WebsocketCommunicator
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert, AlertObservable, AlertStatus
from amalthea.asgi import application
from cases.correlation import (
    AUTO_BUNDLE_SOURCE,
    auto_bundle_ingested_alert,
    find_auto_bundle_case,
)
from cases.models import Case, CaseObservable, CaseStatus, CorrelationRule, Share, TimelineEvent
from identity.models import AuditLog, IngestionToken, Organisation, User
from ingest.models import IngestionSource
from observables.models import Observable, ObservableType

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_connection_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Channels from closing the pytest-django connection inside a socket test."""
    monkeypatch.setattr(channels.db, "close_old_connections", lambda: None)


@pytest.fixture
def org(db: None) -> Organisation:
    return Organisation.objects.first() or Organisation.objects.create(name="Auto bundle org")


@pytest.fixture
def other_org(db: None, org: Organisation) -> Organisation:
    return Organisation.objects.exclude(pk=org.pk).first() or Organisation.objects.create(
        name="Other auto bundle org"
    )


@pytest.fixture
def rule(db: None, org: Organisation) -> CorrelationRule:
    return CorrelationRule.objects.create(
        organisation=org,
        enabled=True,
        match_mode="correlation_key",
        window_minutes=10,
    )


@pytest.fixture
def source(db: None, org: Organisation) -> IngestionSource:
    return IngestionSource.objects.create(
        organisation=org,
        slug="auto-bundle-source",
        name="Auto bundle source",
        correlation_enabled=True,
        default_severity=2,
        mapping_config={
            "title": "$.title",
            "severity": "$.severity",
            "sourceRef": "$.sourceRef",
            "correlation_key": "$.sourceRef",
        },
    )


@pytest.fixture
def source_token(source: IngestionSource, org: Organisation) -> str:
    IngestionToken.objects.filter(source=source).delete()
    _token, plaintext = IngestionToken.create_token(
        source=source, organisation=org, name="auto bundle token"
    )
    return plaintext


def _alert_status() -> AlertStatus:
    return AlertStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]


def _case_status(stage: str = "InProgress") -> CaseStatus:
    return CaseStatus.objects.get_or_create(value=stage, defaults={"stage": stage, "order": 1})[0]


def _mk_case(org: Organisation, *, stage: str = "InProgress", title: str = "Existing case") -> Case:
    return Case.objects.create(title=title, status=_case_status(stage), owner_org=org)


def _mk_alert(
    org: Organisation,
    *,
    ref: str,
    key: str,
    case: Case | None = None,
    when: datetime | None = None,
) -> Alert:
    return Alert.objects.create(
        type="test",
        source="test-source",
        source_ref=ref,
        title=f"Alert {ref}",
        severity=2,
        status=_alert_status(),
        date=when or timezone.now(),
        owner_org=org,
        correlation_key=key,
        case=case,
        raw_payload={"event": {"ref": ref}},
    )


def _mk_observable(org: Organisation, value: str, *, type_name: str = "ip") -> Observable:
    obs_type = ObservableType.objects.get_or_create(
        name=type_name, defaults={"is_case_sensitive": False}
    )[0]
    return Observable.objects.create(
        data_type=obs_type,
        data=value,
        normalized_data=value,
        owner_org=org,
    )


def _bundle_counts(case: Case) -> tuple[int, int, int]:
    bundle = TimelineEvent.objects.filter(case=case, kind="correlation-bundle").count()
    merged = TimelineEvent.objects.filter(case=case, kind="alert-merged").count()
    audit = AuditLog.objects.filter(
        action="case.alert_bundled", target_type="Case", target_id=str(case.id)
    ).count()
    return bundle, merged, audit


# --- AC-P5.2: correlation-key seek ----------------------------------------


def test_correlation_key_finds_open_case_in_window(
    rule: CorrelationRule, org: Organisation
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) == case


def test_correlation_key_ignores_out_of_window_candidate(
    rule: CorrelationRule, org: Organisation
) -> None:
    case = _mk_case(org)
    _mk_alert(
        org,
        ref="stale",
        key="dest_ip:10.0.0.5",
        case=case,
        when=timezone.now() - timedelta(minutes=30),
    )
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_correlation_key_skips_blank_key(rule: CorrelationRule, org: Organisation) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_correlation_key_excludes_closed_case(rule: CorrelationRule, org: Organisation) -> None:
    case = _mk_case(org, stage="Closed")
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_correlation_key_excludes_cross_org_case(
    rule: CorrelationRule, org: Organisation, other_org: Organisation
) -> None:
    case = _mk_case(other_org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_correlation_key_excludes_shared_case(
    rule: CorrelationRule, org: Organisation, other_org: Organisation
) -> None:
    case = _mk_case(other_org)
    Share.objects.create(case=case, organisation=org, permissions={"read": True, "write": True})
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_correlation_key_allows_unlinked_incoming_alert(
    rule: CorrelationRule, org: Organisation
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5", case=None)

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) == case


def test_duplicate_candidates_resolve_to_one_case(rule: CorrelationRule, org: Organisation) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate-1", key="dest_ip:10.0.0.5", case=case)
    _mk_alert(org, ref="candidate-2", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) == case


def test_deterministic_tie_break_uses_date_then_id(
    rule: CorrelationRule, org: Organisation
) -> None:
    first_case = _mk_case(org, title="First")
    second_case = _mk_case(org, title="Second")
    same_instant = timezone.now()
    first = _mk_alert(
        org, ref="candidate-1", key="dest_ip:10.0.0.5", case=first_case, when=same_instant
    )
    second = _mk_alert(
        org, ref="candidate-2", key="dest_ip:10.0.0.5", case=second_case, when=same_instant
    )
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")
    expected = second_case if second.id > first.id else first_case

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) == expected


def test_candidate_cap_is_applied_at_the_database(
    rule: CorrelationRule, org: Organisation, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ui.pagination.PICKER_PAGE_SIZE", 0)
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


# --- AC-P5.3: shared-observable seek --------------------------------------


def test_shared_observable_finds_open_case(rule: CorrelationRule, org: Organisation) -> None:
    rule.match_mode = "shared_observable"
    rule.save(update_fields=["match_mode"])
    case = _mk_case(org)
    observable = _mk_observable(org, "10.0.0.5")
    CaseObservable.objects.create(case=case, observable=observable)
    incoming = _mk_alert(org, ref="incoming", key="")
    AlertObservable.objects.create(alert=incoming, observable=observable)

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) == case


def test_shared_observable_skips_alert_with_no_observables(
    rule: CorrelationRule,
    source: IngestionSource,
    org: Organisation,
) -> None:
    rule.match_mode = "shared_observable"
    rule.save(update_fields=["match_mode"])
    incoming = _mk_alert(org, ref="incoming", key="")

    decision = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)
    assert decision.status == "skipped"
    assert decision.reason == "no_observables"


def test_shared_observable_excludes_ignored_similarity(
    rule: CorrelationRule, org: Organisation
) -> None:
    rule.match_mode = "shared_observable"
    rule.save(update_fields=["match_mode"])
    case = _mk_case(org)
    observable = _mk_observable(org, "10.0.0.9")
    observable.ignore_similarity = True
    observable.save(update_fields=["ignore_similarity"])
    CaseObservable.objects.create(case=case, observable=observable)
    incoming = _mk_alert(org, ref="incoming", key="")
    AlertObservable.objects.create(alert=incoming, observable=observable)

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_shared_observable_excludes_cross_org_case(
    rule: CorrelationRule, org: Organisation, other_org: Organisation
) -> None:
    rule.match_mode = "shared_observable"
    rule.save(update_fields=["match_mode"])
    case = _mk_case(other_org)
    observable = _mk_observable(org, "10.0.0.5")
    CaseObservable.objects.create(case=case, observable=observable)
    incoming = _mk_alert(org, ref="incoming", key="")
    AlertObservable.objects.create(alert=incoming, observable=observable)

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_shared_observable_excludes_stale_links(rule: CorrelationRule, org: Organisation) -> None:
    rule.match_mode = "shared_observable"
    rule.save(update_fields=["match_mode"])
    case = _mk_case(org)
    observable = _mk_observable(org, "10.0.0.5")
    link = CaseObservable.objects.create(case=case, observable=observable)
    CaseObservable.objects.filter(pk=link.pk).update(
        created_at=timezone.now() - timedelta(minutes=30)
    )
    incoming = _mk_alert(org, ref="incoming", key="")
    AlertObservable.objects.create(alert=incoming, observable=observable)

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


def test_shared_observable_cap_is_applied_to_alert_links(
    rule: CorrelationRule, org: Organisation, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cases.correlation.MAX_CORRELATION_OBS", 0)
    rule.match_mode = "shared_observable"
    rule.save(update_fields=["match_mode"])
    case = _mk_case(org)
    observable = _mk_observable(org, "10.0.0.5")
    CaseObservable.objects.create(case=case, observable=observable)
    incoming = _mk_alert(org, ref="incoming", key="")
    AlertObservable.objects.create(alert=incoming, observable=observable)

    assert find_auto_bundle_case(incoming, rule=rule, org_id=org.id) is None


# --- AC-P3/P4: pre-query skips --------------------------------------------


def test_source_kill_switch_skips_before_query(
    rule: CorrelationRule, source: IngestionSource, org: Organisation
) -> None:
    source.correlation_enabled = False
    source.save(update_fields=["correlation_enabled"])
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    decision = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)
    assert decision.status == "skipped"
    assert decision.reason == "source_correlation_disabled"
    assert decision.case_id is None


def test_tenant_mismatch_skips_before_query(
    rule: CorrelationRule, source: IngestionSource, org: Organisation, other_org: Organisation
) -> None:
    incoming = _mk_alert(other_org, ref="incoming", key="dest_ip:10.0.0.5")

    decision = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)
    assert decision.status == "skipped"
    assert decision.reason == "tenant_mismatch"


# --- AC-P5.4: bundle trail -------------------------------------------------


def test_successful_bundle_writes_complete_trail(
    rule: CorrelationRule, source: IngestionSource, org: Organisation
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")
    observable = _mk_observable(org, "user@example.net", type_name="mail")
    AlertObservable.objects.create(alert=incoming, observable=observable)

    with patch("automation.registry.dispatch_observable_linked") as dispatch:
        decision = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)

    assert decision.status == "bundled"
    assert decision.case_id == case.id
    dispatch.assert_called_once_with(observable, case.pk)

    bundle_events = list(TimelineEvent.objects.filter(case=case, kind="correlation-bundle"))
    assert len(bundle_events) == 1
    assert bundle_events[0].metadata["source"] == AUTO_BUNDLE_SOURCE
    assert "dest_ip:10.0.0.5" in bundle_events[0].metadata["reason"]

    audit_rows = list(
        AuditLog.objects.filter(
            action="case.alert_bundled", target_type="Case", target_id=str(case.id)
        )
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].details["source"] == AUTO_BUNDLE_SOURCE

    assert TimelineEvent.objects.filter(case=case, kind="alert-merged").count() == 1
    incoming.refresh_from_db()
    assert incoming.case_id == case.id
    assert CaseObservable.objects.filter(case=case, observable=observable).exists()


def test_known_refusal_rolls_back_link_ledger_audit_and_observables(
    rule: CorrelationRule, source: IngestionSource, org: Organisation
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")
    observable = _mk_observable(org, "user@example.net", type_name="mail")
    AlertObservable.objects.create(alert=incoming, observable=observable)

    with patch("cases.correlation.record_alert_bundled", side_effect=PermissionDenied("refused")):
        decision = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)

    assert decision.status == "skipped"
    assert decision.reason == "bundle_failed"
    incoming.refresh_from_db()
    assert incoming.case_id is None
    assert _bundle_counts(case) == (0, 0, 0)
    assert not CaseObservable.objects.filter(case=case, observable=observable).exists()


def test_unexpected_exception_returns_failed_and_rolls_back(
    rule: CorrelationRule, source: IngestionSource, org: Organisation
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    with patch("cases.correlation.merge_alert_into_case", side_effect=RuntimeError("boom")):
        decision = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)

    assert decision.status == "failed"
    assert decision.reason == "bundle_failed"
    incoming.refresh_from_db()
    assert incoming.case_id is None
    assert _bundle_counts(case) == (0, 0, 0)


# --- AC-P5.5: replay/idempotence -------------------------------------------


def test_webhook_replay_never_re_bundles(
    rule: CorrelationRule,
    source: IngestionSource,
    source_token: str,
    org: Organisation,
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    client = Client()
    payload = {"title": "Replay alert", "severity": 2, "sourceRef": "dest_ip:10.0.0.5"}

    first = client.post(
        reverse("alerts-webhook"),
        data=payload,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {source_token}",
    )
    assert first.status_code == 201
    assert first.json()["auto_bundle"]["status"] == "bundled"
    assert first.json()["auto_bundle"]["case_id"] == str(case.id)
    assert _bundle_counts(case)[0] == 1

    second = client.post(
        reverse("alerts-webhook"),
        data=payload,
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {source_token}",
    )
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["auto_bundle"]["status"] is None
    assert _bundle_counts(case) == (1, 1, 1)


def test_manual_retry_after_refusal_has_no_duplicate_rows(
    rule: CorrelationRule, source: IngestionSource, org: Organisation
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    with patch("cases.correlation.record_alert_bundled", side_effect=PermissionDenied("refused")):
        refused = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)
    assert refused.status == "skipped"
    assert _bundle_counts(case) == (0, 0, 0)

    with patch("automation.registry.dispatch_observable_linked"):
        retried = auto_bundle_ingested_alert(incoming, source=source, org_id=org.id, created=True)
    assert retried.status == "bundled"
    assert _bundle_counts(case) == (1, 1, 1)


# --- AC-P5.6: failure containment and realtime -----------------------------


def test_known_refusal_returns_warning_and_no_partial_rows(
    rule: CorrelationRule,
    source: IngestionSource,
    source_token: str,
    org: Organisation,
) -> None:
    rule.enabled = False
    rule.save(update_fields=["enabled"])
    client = Client()

    response = client.post(
        reverse("alerts-webhook"),
        data={"title": "Refused alert", "severity": 2, "sourceRef": "dest_ip:10.0.0.5"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {source_token}",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["auto_bundle"]["status"] == "skipped"
    assert body["auto_bundle"]["reason"] == "rule_disabled"
    assert "auto_bundle_skipped: rule_disabled" in body["warnings"]

    alert = Alert.objects.get(pk=body["id"])
    assert alert.case_id is None
    assert not TimelineEvent.objects.filter(kind="correlation-bundle").exists()
    assert not AuditLog.objects.filter(action="case.alert_bundled").exists()


def test_bundle_events_reach_case_room_and_owner_org_only(
    rule: CorrelationRule,
    source: IngestionSource,
    org: Organisation,
    other_org: Organisation,
    django_capture_on_commit_callbacks: Any,
) -> None:
    case = _mk_case(org)
    _mk_alert(org, ref="candidate", key="dest_ip:10.0.0.5", case=case)
    incoming = _mk_alert(org, ref="incoming", key="dest_ip:10.0.0.5")

    analyst = User.objects.create_user(
        username="bundle-analyst", password="correct-horse-battery", email="a@example.net", org=org
    )
    foreign = User.objects.create_user(
        username="bundle-foreign",
        password="correct-horse-battery",
        email="b@example.net",
        org=other_org,
    )
    browser = Client()
    assert browser.login(username=analyst.username, password="correct-horse-battery")
    foreign_browser = Client()
    assert foreign_browser.login(username=foreign.username, password="correct-horse-battery")

    def _room(client: Client, url: str) -> WebsocketCommunicator:
        cookie = client.cookies["sessionid"].OutputString()
        return WebsocketCommunicator(
            application, url, headers=[(b"cookie", cookie.encode("ascii"))]
        )

    def _do_bundle() -> None:
        with (
            patch("automation.registry.dispatch_observable_linked"),
            django_capture_on_commit_callbacks(execute=True),
        ):
            decision = auto_bundle_ingested_alert(
                incoming, source=source, org_id=org.id, created=True
            )
        assert decision.status == "bundled"

    async def scenario() -> None:
        case_room = _room(browser, f"/ws/case/{case.id}/")
        org_room = _room(browser, f"/ws/org/{org.id}/")
        foreign_room = _room(foreign_browser, f"/ws/org/{other_org.id}/")
        for communicator in (case_room, org_room, foreign_room):
            connected, code = await communicator.connect(timeout=5)
            assert connected is True, f"socket refused with {code}"

        await sync_to_async(_do_bundle)()

        case_types = [
            (await case_room.receive_json_from(timeout=5))["event_type"] for _ in range(2)
        ]
        assert case_types == ["alert-merged", "correlation-bundle"]

        refresh = await org_room.receive_json_from(timeout=5)
        assert refresh["event_type"] == "refresh"
        assert refresh["payload"]["surface"] == "cases"
        assert refresh["payload"]["reason"] == "case-updated"
        assert refresh["payload"]["object_id"] == str(case.id)

        assert await foreign_room.receive_nothing(timeout=1)

        for communicator in (case_room, org_room, foreign_room):
            await communicator.disconnect()
            await communicator.wait(timeout=5)

    async_to_sync(scenario)()
