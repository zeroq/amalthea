"""Transaction-safe lifecycle operations for playbooks.

`AutomationRun.playbook` is `PROTECT` (REVIEW M14): a run must never outlive its playbook
definition. Deleting an in-use playbook therefore removes its run rows first, in the same
transaction, after refusing while a run is still `Pending`/`Running` (a worker may still hold it).

Case timeline entries are unaffected: `TimelineEvent` has no FK to a run, and the automation result
it recorded remains in the ledger after the run row is gone.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import ProtectedError

from automation.models import ACTIVE_RUN_STATUSES, Playbook


class PlaybookDeleteBlockedError(Exception):
    """The playbook cannot be deleted by the caller right now."""

    def __init__(self, *, active_runs: int, message: str | None = None) -> None:
        self.active_runs = active_runs
        super().__init__(message or f"{active_runs} run(s) are still pending or running")


@transaction.atomic
def delete_playbook(playbook: Playbook) -> int:
    """Delete `playbook` and all its runs; return the number of runs removed.

    The playbook row is locked so a concurrent dispatch cannot insert a run between the active-run
    check and the delete. A `Pending`/`Running` run means a worker may still write back to it, so
    the operation is refused rather than deleting the row under the worker.

    `PROTECT` remains the schema backstop. It should be unreachable after the run delete; if it is
    reached (for example, a backend that does not honour the row lock), the atomic block rolls
    the partial delete back and the caller gets the same user-facing refusal.
    """
    locked = Playbook.objects.select_for_update().filter(pk=playbook.pk).first()
    if locked is None:
        raise PlaybookDeleteBlockedError(active_runs=0, message="the playbook no longer exists")

    active_runs = locked.runs.filter(status__in=ACTIVE_RUN_STATUSES).count()
    if active_runs:
        raise PlaybookDeleteBlockedError(active_runs=active_runs)

    run_count = locked.runs.count()
    locked.runs.all().delete()
    try:
        locked.delete()
    except ProtectedError as exc:
        # A run was inserted after the check by a backend that ignores `select_for_update` (SQLite
        # is one). The transaction rolls back, so no history is lost.
        raise PlaybookDeleteBlockedError(
            active_runs=0,
            message="a run was created while deleting the playbook; try again",
        ) from exc
    return run_count


def run_playbook_service(
    target: Any,
    arguments: dict[str, Any],
    *,
    actor: Any | None = None,
) -> dict[str, Any]:
    """Queue one manual playbook run from an approved MCP proposal.

    Delegates to :func:`automation.dispatcher.run_now` so the run row, org check,
    on-commit Celery enqueue, audit and case-ledger feedback all follow the same
    path as the existing UI action. ``playbookId`` may be a UUID or a name; it
    must belong to the target case's organisation.
    """
    from uuid import UUID

    from automation.dispatcher import run_now
    from observables.models import Observable

    playbook_ref = arguments.get("playbookId") or arguments.get("playbook")
    if not playbook_ref:
        raise ValueError("playbookId is required")

    case = target
    observable = None
    observable_ref = arguments.get("observableId")
    if observable_ref:
        observable = None
        if _is_uuid(observable_ref):
            observable = Observable.objects.filter(
                pk=str(observable_ref), owner_org=case.owner_org
            ).first()
        if observable is None:
            observable = Observable.objects.filter(
                data=str(observable_ref), owner_org=case.owner_org
            ).first()
        if observable is None:
            raise ValueError("observableId was not found in this organisation")

    if not hasattr(case, "owner_org_id"):
        raise ValueError("a case target is required")

    try:
        pk = UUID(str(playbook_ref))
    except (TypeError, ValueError):
        pk = None
    playbook = None
    if pk is not None:
        playbook = Playbook.objects.filter(pk=pk, owner_org=case.owner_org).first()
    if playbook is None:
        playbook = Playbook.objects.filter(name=str(playbook_ref), owner_org=case.owner_org).first()
    if playbook is None:
        raise ValueError("playbookId was not found in this organisation")

    run = run_now(playbook, case=case, observable=observable)
    return {
        "runId": str(run.id),
        "playbookId": str(playbook.id),
        "playbookName": playbook.name,
        "status": run.status,
        "triggeredBy": run.triggered_by,
    }


def _is_uuid(value: Any) -> bool:
    from uuid import UUID

    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True
