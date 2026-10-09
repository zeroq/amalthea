"""Domain event → Celery task, the bridge between case management and automation (Module D).

Three properties are the whole design, and each one is a bug the plan named before this code existed:

* **`transaction.on_commit`.** Dispatch happens when a transaction *commits*. A playbook that fires
  on an observable created inside a transaction that later rolls back would query the database for a
  row that does not exist, and its result would be appended to a case that was never written (AC6.6).
* **`idempotency_key`, unique.** `AutomationRun.idempotency_key` has a unique constraint. Dispatch
  creates the run row first and passes the row's id to the worker, so a replayed trigger, a retried
  task and a duplicated signal all collapse onto one row (AC6.5). The key is derived from the
  trigger and its subject — not from a timestamp — so it is stable across retries by construction.
* **Failure is recorded, never raised.** A failing action yields `Failed` plus an `error` and never
  propagates into the request that triggered it (AC6.4). The ledger is the record of what automation
  did, including when it did nothing useful.
"""

from __future__ import annotations

import hashlib
from functools import partial
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from automation.models import AutomationRun, Playbook
from cases.ledger import append_timeline_event
from cases.models import Case, TimelineEvent
from core.events import AlertIngested, CaseStatusChanged, ObservableCreated, TaskCompleted
from observables.models import Observable

#: Event name → the `Playbook.trigger_event` a playbook binds to.
TRIGGER_EVENTS: tuple[str, ...] = (
    "observable.created",
    "alert.ingested",
    "case.status_changed",
    "task.completed",
)


def _event_name(event: object) -> str:
    if isinstance(event, ObservableCreated):
        return "observable.created"
    if isinstance(event, AlertIngested):
        return "alert.ingested"
    if isinstance(event, CaseStatusChanged):
        return "case.status_changed"
    if isinstance(event, TaskCompleted):
        return "task.completed"
    raise TypeError(f"no trigger event name for {type(event).__name__}")


def idempotency_key(playbook: str, event_name: str, subject: str) -> str:
    """A stable key for "this playbook, triggered by this subject".

    Stable means *deterministic from its inputs*: no timestamp, no UUID, no counter. Two triggers
    for the same subject must produce the same string, because that string is what the unique
    constraint collapses them on.
    """
    digest = hashlib.sha256(f"{playbook}\x1f{event_name}\x1f{subject}".encode()).hexdigest()
    return f"{event_name}:{playbook}:{digest[:32]}"


def _subject_id(event: object) -> str:
    for attribute in ("observable_id", "alert_id", "case_id", "task_id"):
        value = getattr(event, attribute, None)
        if value:
            return str(value)
    raise TypeError(f"{type(event).__name__} carries no subject id")


def _idempotent_subject(event: object) -> str:
    """The identity a run's idempotency key binds to.

    Most events are one-to-one with their subject (`alert.ingested` ↔ alert, `case.status_changed`
    ↔ case). `observable.created` is not: an `Observable` is a *shared* entity (plan Module C), so
    linking the same artifact into a second case must fire the playbook for that case, not collapse
    onto the first case's run. The subject therefore includes the case: two links of one observable
    into two cases produce two runs, while replaying the *same* link still collapses on the unique
    `idempotency_key` (AC6.5).

    Every subject string is formed from UUIDs, so the `:` separator cannot collide with a UUID
    character set — each half parses unambiguously.
    """
    case_id = getattr(event, "case_id", None)
    if isinstance(event, ObservableCreated):
        if not case_id:
            raise TypeError("ObservableCreated without a case_id cannot be dispatched")
        return f"{_subject_id(event)}:{case_id}"
    return _subject_id(event)


def _case_id(event: object) -> str | None:
    value = getattr(event, "case_id", None)
    return str(value) if value else None


@transaction.atomic
def dispatch(event: object) -> list[AutomationRun]:
    """Create a pending `AutomationRun` per bound playbook, then queue the workers on commit.

    Returns the runs created by *this* call, so a caller can assert on them. A playbook bound to a
    subject that already has a run contributes nothing: the unique `idempotency_key` says so and
    the insert is skipped rather than raced.
    """
    event_name = _event_name(event)
    subject = _idempotent_subject(event)
    playbooks = list(Playbook.objects.filter(trigger_event=event_name, is_active=True))
    if not playbooks:
        return []

    created: list[AutomationRun] = []
    for playbook in playbooks:
        key = idempotency_key(playbook.name, event_name, subject)
        run, was_created = AutomationRun.objects.get_or_create(
            idempotency_key=key,
            defaults={
                "case_id": _case_id(event),
                "playbook": playbook,
                # The immutable snapshot, kept because the ledger must still read "we ran the
                # playbook called X" after X is renamed (automation/models.AutomationRun).
                "playbook_name": playbook.name,
                "trigger_event": event_name,
                "status": "Pending",
                "triggered_by_observable_id": getattr(event, "observable_id", None),
            },
        )
        if not was_created:
            continue
        created.append(run)

    if created:
        from automation.tasks import execute_run

        for run in created:
            # AC6.6: nothing is queued until the transaction that produced the observable commits,
            # so a rollback emits no event and leaves no run. `run_id` is bound as a default argument
            # rather than captured: a loop-variable lambda would read the *last* run's id on all of
            # them, so N playbooks would queue N copies of the same worker call.
            transaction.on_commit(partial(execute_run.delay, str(run.id)))

    return created


def run_now(
    playbook: Playbook,
    *,
    case: Case | None = None,
    observable: Observable | None = None,
) -> AutomationRun:
    """Queue one **manual** execution of `playbook` (plan §6.1, T2.4).

    Two differences from :func:`dispatch`, and both are the point:

    * **the key does not deduplicate.** `dispatch` derives its key from the event and its subject
      so a replay collapses; a manual run binds to a fresh `uuid4`, so an analyst who presses
      *Run* twice gets two runs. Collapsing two deliberate actions would be a silent dropped
      request, not idempotency.
    * **`triggered_by="manual"`.** The ledger question "did a person run this, or did a trigger"
      is answered by a column, not by inference from `trigger_event`.

    The `AutomationRun` row and the enqueue are written in one transaction and the worker is
    queued on commit, exactly as event dispatch is: a request that rolls back must not leave a
    worker reading a run row that was never stored (AC6.6).

    `case` and `observable` are both optional but **at least one is required** — the feedback
    loop writes back to a case (`record_result` refuses a case-less run), so a run with neither
    has nowhere to report and is refused up front rather than failing in the worker.
    """
    if case is None and observable is None:
        raise ValueError("run_now needs a case or an observable to run against")
    if case is None and observable is not None:
        link = observable.case_observables.select_related("case").first()
        if link is None:
            raise ValueError("the observable is not linked to a case, so there is nowhere to write")
        case = link.case

    run = AutomationRun.objects.create(
        case=case,
        playbook=playbook,
        playbook_name=playbook.name,
        trigger_event=playbook.trigger_event or "manual",
        triggered_by="manual",
        status="Pending",
        triggered_by_observable=observable,
        idempotency_key=f"manual:{uuid4()}",
    )

    from automation.tasks import execute_run

    transaction.on_commit(partial(execute_run.delay, str(run.id)))
    return run


def record_result(run: AutomationRun, result: Any, *, subject_label: str = "") -> TimelineEvent:
    """Append the outcome to the case timeline and stamp the run's terminal state.

    This is the feedback loop AGENTS.md Module D promises: the result is in `output_log` **and** on
    the ledger, so an analyst reading the case does not have to know that automation exists.
    """
    ok = bool(getattr(result, "ok", False))
    run.status = "Success" if ok else "Failed"
    run.output_log = str(getattr(result, "output_log", ""))
    run.error = str(getattr(result, "error", "")) if not ok else ""
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "output_log", "error", "finished_at", "updated_at"])

    # The case is resolved through the descriptor rather than `case_id=`, because the ledger
    # service takes the instance: it publishes `str(case.id)` on commit. A run whose case was
    # cleared (`SET_NULL`, REVIEW M14) has nothing to write back to.
    case = run.case
    if case is None:
        raise ValueError(f"run {run.id} has no case to write its result back to")

    label = subject_label or (run.playbook_name)
    return append_timeline_event(
        case,
        title=f"Automation {run.status.lower()}: {label}",
        description=(run.output_log or run.error)[:4000],
        kind="automation-run",
        metadata={
            "runId": str(run.id),
            "playbook": run.playbook_name,
            "triggerEvent": run.trigger_event,
            "status": run.status,
        },
    )
