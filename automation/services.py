"""Transaction-safe lifecycle operations for playbooks.

`AutomationRun.playbook` is `PROTECT` (REVIEW M14): a run must never outlive its playbook
definition. Deleting an in-use playbook therefore removes its run rows first, in the same
transaction, after refusing while a run is still `Pending`/`Running` (a worker may still hold it).

Case timeline entries are unaffected: `TimelineEvent` has no FK to a run, and the automation result
it recorded remains in the ledger after the run row is gone.
"""

from __future__ import annotations

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
