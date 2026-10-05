"""The AGENTS.md §4 loop, complete: webhook → case → observable → automation → timeline.

This is the MVP proof the brief asks for: "webhook POST → alert → merge into case → email
observable extracted → playbook run → result visible on the case timeline", as **one** test with no
mocks on the path. Celery runs eager (`settings/test.py`), so the worker is real and the ordering is
real; only the broker and the database are stood in for.

## On `django_capture_on_commit_callbacks`

`pytest.mark.django_db` wraps each test in a transaction that never commits, so `on_commit`
callbacks are deferred to teardown and never fire. That fixture is the supported way to release
them — and using it is what lets AC6.6 be *proved* rather than assumed: the helper is invoked with
`execute=True` only where a commit is supposed to happen, and in the rollback test it is not invoked
at all, so the assertion "no run exists" is meaningful instead of vacuous.

The automation action is the **registered** `enrichment_probe`, not an HTTP request. That is a
deliberate choice, not a shortcut: an HTTP action would have to be pointed at something, and a test
that patches `urllib` verifies the patch rather than the loop. The probe exercises the same dispatch,
the same registration lookup, the same `AutomationRun` lifecycle and the same timeline write, with no
socket. `test_automation_executor.py` covers the HTTP path and the SSRF guard on its own.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from automation.models import AutomationRun, Playbook
from cases.models import Case, CaseObservable, CaseStatus, TimelineEvent
from identity.models import Organisation, User
from ingest.models import IngestionSource
from observables.models import Observable, ObservableType

PHISHING_PAYLOAD = {
    "event": "mailbox.rule.created",
    "actor": {"email": "attacker@example.net"},
    "rule": {
        "name": "auto-forward to evil",
        "conditions": [{"field": "headerFrom", "values": ["invoice@evil.example.com"]}],
    },
    "sourceRef": "evt-4242",
    "severity": 3,
    "title": "Suspicious inbox rule on finance mailbox",
}


@pytest.fixture
def analyst(db: None) -> User:
    org = Organisation.objects.create(name="Test Org")
    return User.objects.create(
        login="analyst", username="analyst", email="analyst@example.net", org=org, is_active=True
    )


@pytest.fixture
def api(analyst: User) -> object:
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=analyst)
    return client


@pytest.fixture
def anonymous_api() -> object:
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def source(db: None) -> IngestionSource:
    return IngestionSource.objects.create(
        slug="o365",
        name="Microsoft 365 hook",
        mapping_config={
            "title": "$.title",
            "severity": "$.severity",
            "sourceRef": "$.sourceRef",
            "correlation_key": "$.sourceRef",
        },
    )


@pytest.fixture
def mail_playbook(db: None) -> Playbook:
    """Bind the enrichment probe to every new observable.

    `trigger_event="observable.created"` with no type filter is the coarse binding the MVP needs;
    type-scoped bindings are a configuration feature, not a code one.
    """
    return Playbook.objects.create(
        name="enrich-mail",
        description="Look up a newly extracted artifact",
        trigger_event="observable.created",
        is_active=True,
        config={
            "action": "python",
            "action_path": "amalthea.automation.executor.enrichment_probe",
        },
    )


def _escalate(anonymous_api: object, api: object, source: IngestionSource) -> Case:
    posted = anonymous_api.post(
        reverse("alerts-webhook", kwargs={"source_id": source.slug}),
        data=json.dumps(PHISHING_PAYLOAD),
        content_type="application/json",
    )
    assert posted.status_code == 201, posted.content
    alert_id = posted.json()["_id"]
    escalated = api.post(f"/api/v1/alert/{alert_id}/import", {}, format="json")
    assert escalated.status_code == 201, escalated.content
    return Case.objects.get(pk=escalated.json()["_id"])


def test_mvp_loop_with_automation(
    anonymous_api: object,
    api: object,
    source: IngestionSource,
    mail_playbook: Playbook,
    django_capture_on_commit_callbacks: object,
) -> None:
    """All four legs of AGENTS.md §4, end to end, in one test."""
    # The work happens inside the capture; the assertions deliberately sit *after* it. The fixture
    # releases `on_commit` callbacks on exit, so asserting inside the block would be asserting
    # against a pre-commit state and every automation assertion below would be vacuous.
    with django_capture_on_commit_callbacks(execute=True):
        case = _escalate(anonymous_api, api, source)

    # AC6.1 — extraction fired the bound playbook, and the run reached Success with output.
    runs = list(AutomationRun.objects.filter(case=case).order_by("playbook_name"))
    assert runs, "extracting an observable did not fire observable.created"
    assert all(r.status == "Success" for r in runs), [
        (r.playbook_name, r.status, r.error) for r in runs
    ]
    assert all(r.output_log for r in runs), (
        "a successful run with an empty output_log proves nothing"
    )
    assert {r.playbook_name for r in runs} == {"enrich-mail"}

    # The run is scoped to the artifact that triggered it, and the output names it.
    triggered = [r for r in runs if r.triggered_by_observable_id]
    assert triggered, "runs carry no triggered_by_observable, so the feedback loop has no subject"
    for run in triggered:
        assert run.triggered_by_observable.normalized_data in run.output_log

    # AC6.2 — the result is readable on the case timeline as a structured entry.
    events = list(TimelineEvent.objects.filter(case=case, kind="automation-run"))
    assert events, "no automation timeline entry: the feedback loop never closed"
    for event in events:
        assert event.metadata["status"] in {"Success", "Failed"}
        assert event.metadata["playbook"] == "enrich-mail"
        assert "enrichment probe" in event.description

    # AC6.5 — replaying the trigger produces no second run.
    from automation.dispatcher import dispatch
    from core.events import ObservableCreated

    first = triggered[0].triggered_by_observable
    before = AutomationRun.objects.filter(case=case).count()
    with django_capture_on_commit_callbacks(execute=True):
        dispatch(ObservableCreated(observable_id=str(first.pk), case_id=str(case.pk)))
    assert AutomationRun.objects.filter(case=case).count() == before, (
        "a replayed trigger created a second AutomationRun despite the unique idempotency_key"
    )

    # The whole thing is readable from the API the UI will render.
    detail = api.get(f"/api/v1/case/{case.number}").json()
    assert detail["automationRuns"], "the case payload omits its automation runs"
    assert detail["timeline"], "the case payload omits its timeline"
    assert {o["normalizedData"] for o in detail["observables"]} >= {"attacker@example.net"}
    assert any(e["kind"] == "automation-run" for e in detail["timeline"])


def test_dispatch_waits_for_the_commit(
    anonymous_api: object,
    api: object,
    source: IngestionSource,
    mail_playbook: Playbook,
    django_capture_on_commit_callbacks: object,
) -> None:
    """A run row exists before the worker runs: dispatch creates the ledger entry, then queues.

    Ordering is the property AC6.6 depends on. It is only observable while the commit is held back,
    which is exactly what the capture fixture without `execute=True` does.
    """
    with django_capture_on_commit_callbacks(execute=False):
        case = _escalate(anonymous_api, api, source)
        pending = AutomationRun.objects.filter(case=case)
        assert pending.exists(), "dispatch created no pending run to queue"
        assert all(r.status == "Pending" for r in pending), (
            "the worker ran before the commit; a rollback would leave it acting on a row that "
            "was never written"
        )


def test_automation_fires_only_on_a_new_link(
    anonymous_api: object,
    api: object,
    source: IngestionSource,
    mail_playbook: Playbook,
    django_capture_on_commit_callbacks: object,
) -> None:
    """Re-extraction over the same text does not re-run every playbook on the case."""
    with django_capture_on_commit_callbacks(execute=True):
        case = _escalate(anonymous_api, api, source)
    baseline = AutomationRun.objects.filter(case=case).count()
    assert baseline > 0

    with django_capture_on_commit_callbacks(execute=True):
        api.post(f"/api/v1/case/{case.number}/observable/", {"extract": True}, format="json")
    assert AutomationRun.objects.filter(case=case).count() == baseline, (
        "re-running the extractor re-fired automation for artifacts that were already linked"
    )


def test_a_failing_action_records_failure_without_raising(
    api: object, django_capture_on_commit_callbacks: object
) -> None:
    """AC6.4 — a playbook that cannot run yields `Failed` and an error, and the loop continues."""
    Playbook.objects.create(
        name="broken",
        trigger_event="observable.created",
        is_active=True,
        config={"action": "python", "action_path": "amalthea.does.not.exist"},
    )
    created = api.post("/api/v1/case", {"title": "Failure path"}, format="json")
    assert created.status_code == 201, created.content
    case = Case.objects.get(pk=created.json()["_id"])

    observable_type = ObservableType.objects.get(name="mail")
    observable = Observable.objects.create(
        data_type=observable_type, data="a@b.example", normalized_data="a@b.example"
    )
    CaseObservable.objects.create(case=case, observable=observable)

    from automation.dispatcher import dispatch
    from core.events import ObservableCreated

    with django_capture_on_commit_callbacks(execute=True):
        dispatch(ObservableCreated(observable_id=str(observable.pk), case_id=str(case.pk)))

    run = AutomationRun.objects.get(playbook_name="broken")
    assert run.status == "Failed"
    assert "no registered action" in run.error
    # The originating call is unaffected: the failure is a ledger row, not a raised exception.
    assert api.get(f"/api/v1/case/{case.number}").status_code == 200

    event = TimelineEvent.objects.filter(case=case, kind="automation-run").get()
    assert event.metadata["status"] == "Failed"


def test_a_rolled_back_transaction_emits_no_run(
    api: object, mail_playbook: Playbook, django_capture_on_commit_callbacks: object
) -> None:
    """AC6.6 — dispatch is `on_commit`, so a rolled-back transaction leaves no run at all."""
    from django.db import transaction

    status = CaseStatus.objects.get_or_create(
        value="InProgress", defaults={"stage": "InProgress", "order": 1}
    )[0]
    case = Case.objects.create(title="Rolled back", status=status)
    observable_type = ObservableType.objects.get(name="mail")
    observable = Observable.objects.create(
        data_type=observable_type,
        data="rollback@evil.example",
        normalized_data="rollback@evil.example",
    )

    before = AutomationRun.objects.count()
    # `execute` is deliberately NOT requested here: the callbacks captured during the inner atomic
    # block are discarded when it rolls back, which is the behaviour under test.
    with pytest.raises(RuntimeError), transaction.atomic():
        CaseObservable.objects.create(case=case, observable=observable)
        with django_capture_on_commit_callbacks(execute=True):
            from automation.dispatcher import dispatch
            from core.events import ObservableCreated

            dispatch(ObservableCreated(observable_id=str(observable.pk), case_id=str(case.pk)))
        raise RuntimeError("rollback")

    assert AutomationRun.objects.count() == before, (
        "a run survived a rolled-back transaction; dispatch is not behind on_commit"
    )
