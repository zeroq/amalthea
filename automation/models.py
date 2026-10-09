from __future__ import annotations

from django.db import models

from core.enums import (
    AUTOMATION_RUN_STATUS_CHOICES,
    AUTOMATION_RUN_TRIGGERED_BY_CHOICES,
    in_values,
)
from core.models import TimeStampedModel, UUIDModel


class Playbook(UUIDModel, TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    trigger_event = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    config = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "playbook"
        indexes = [
            # Resolved on every domain event, so this lookup is on the hot path (C2).
            models.Index(fields=["trigger_event", "is_active"], name="playbook_trigger_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class AutomationRun(UUIDModel, TimeStampedModel):
    """One execution of a playbook for one case/alert (AGENTS.md Module D).

    `playbook` is the referential link; `playbook_name` is the *immutable snapshot* of
    the name at dispatch time. They are deliberately not one field: the ledger must keep
    reading "we ran the playbook called X" even after X is renamed, and the FK is what
    stops a run referencing a playbook that does not exist (REVIEW M14).
    """

    # db_index=False: `case` is the left prefix of ar_case_started_idx below (M1).
    case = models.ForeignKey(
        "cases.Case",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="automation_runs",
        db_index=False,
    )
    alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="automation_runs",
    )
    playbook = models.ForeignKey(Playbook, on_delete=models.PROTECT, related_name="runs")
    playbook_name = models.CharField(max_length=200)
    trigger_event = models.CharField(max_length=100, blank=True)
    #: Who started this run: a domain event (`trigger`, the default) or an analyst (`manual`).
    #: Without it the ledger cannot tell a human-run playbook from a triggered one (plan §5.1).
    triggered_by = models.CharField(
        max_length=20, default="trigger", choices=list(AUTOMATION_RUN_TRIGGERED_BY_CHOICES)
    )
    status = models.CharField(
        max_length=20, default="Pending", choices=list(AUTOMATION_RUN_STATUS_CHOICES)
    )
    output_log = models.TextField(blank=True)
    error = models.TextField(blank=True)
    triggered_by_observable = models.ForeignKey(
        "observables.Observable",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="automation_runs",
    )
    celery_task_id = models.CharField(max_length=100, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)

    class Meta:
        db_table = "automation_run"
        indexes = [
            models.Index(fields=["case", "-started_at"], name="ar_case_started_idx"),
            models.Index(fields=["celery_task_id"], name="ar_celery_task_idx"),
            # C2: without this the pending-run dispatch beat is a full scan + sort over a
            # monotonically growing table on every tick. A partial index is portable —
            # SQLite and Postgres both support `CREATE INDEX ... WHERE`.
            models.Index(
                fields=["created_at"],
                name="ar_pending_idx",
                condition=models.Q(status="Pending"),
            ),
        ]
        constraints = [
            in_values(
                "automation_run_status_valid",
                "status",
                tuple(s for s, _ in AUTOMATION_RUN_STATUS_CHOICES),
            ),
            in_values(
                "automation_run_triggered_by_valid",
                "triggered_by",
                tuple(v for v, _ in AUTOMATION_RUN_TRIGGERED_BY_CHOICES),
            ),
        ]

    def __str__(self) -> str:
        return f"AR({self.playbook_name}:{self.status})"
