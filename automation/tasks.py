"""Celery entry point for playbook execution (Phase 6).

`execute_run` is the only place a playbook action is invoked from a queue. It is written to be
**re-entrant**: a task redelivered after a worker crash re-reads the run row and finds it already
terminal, and returns without re-running the action. That is what makes Celery's at-least-once
delivery safe here without a distributed lock.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def execute_run(self: Any, run_id: str) -> dict[str, str]:
    """Run one `AutomationRun` to a terminal state.

    AC6.3 is a timing property of the *caller* (the originating request returns before this
    completes), which is why dispatch is `on_commit` + `delay` and nothing here is synchronous.

    AC6.4: an action that fails produces `Failed` and a recorded error; it is never re-raised into
    the request. Retries are therefore only for *transient* conditions — a row that does not exist
    *yet* is retried, a refused URL is not.
    """
    from automation.dispatcher import record_result
    from automation.executor import ActionError, UnsafeURLError, execute
    from automation.models import AutomationRun

    try:
        run = AutomationRun.objects.select_related("playbook", "case").get(pk=run_id)
    except AutomationRun.DoesNotExist:
        # The transaction that created it rolled back between `on_commit` and now. Retrying is
        # correct: the run may still appear if the caller is mid-commit elsewhere.
        missing = AutomationRun.DoesNotExist(f"run {run_id} not found")
        raise self.retry(exc=missing) from None

    if run.status in {"Success", "Failed"}:
        return {"run_id": run_id, "status": run.status, "skipped": "already terminal"}

    run.status = "Running"
    run.started_at = run.started_at or timezone.now()
    run.celery_task_id = getattr(self.request, "id", "") or ""
    run.save(update_fields=["status", "started_at", "celery_task_id", "updated_at"])

    values = _interpolation_values(run)
    try:
        result = execute(run.playbook.config, values)
    except UnsafeURLError as exc:
        # A refused URL is a policy outcome, not a transient fault: retrying would just be refused
        # again, so it is recorded as Failed with the guard's own reason.
        result = _failure(f"blocked by the SSRF guard: {exc}")
    except ActionError as exc:
        result = _failure(str(exc))
    except Exception as exc:
        result = _failure(f"{type(exc).__name__}: {exc}")

    with transaction.atomic():
        record_result(run, result, subject_label=_subject_label(run, values))
    return {"run_id": run_id, "status": run.status}


def _failure(message: str) -> Any:
    from automation.executor import ActionResult

    return ActionResult(output_log="", ok=False, error=message)


def _interpolation_values(run: Any) -> dict[str, Any]:
    """The names a playbook's templates may reference.

    Deliberately narrow. An `http` action gets `observable` (the triggering artifact) and `case`; it
    does **not** get the whole `raw_payload`. That payload is adversary-controlled and often large,
    and a template field is an interpolation sink — handing it the entire payload would make every
    outbound request carry attacker-chosen content by default.
    """
    values: dict[str, Any] = {"run_id": str(run.id), "playbook": run.playbook_name}
    if run.triggered_by_observable_id:
        observable = run.triggered_by_observable
        values["observable"] = observable.normalized_data
        values["observable_type"] = observable.data_type.name
    if run.case_id:
        values["case"] = run.case.number
        values["case_id"] = str(run.case_id)
    return values


def _subject_label(run: Any, values: dict[str, Any]) -> str:
    if values.get("observable"):
        return f"{run.playbook_name} on {values['observable_type']} {values['observable']}"
    return f"{run.playbook_name} on case {values.get('case', run.case_id)}"
