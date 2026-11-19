"""Request-free task create/toggle/assign logic shared by the API, the UI and the MCP executor (P2 C2).

One definition of the field set, so the three surfaces cannot drift.
"""

from __future__ import annotations

from typing import Any

from cases.models import Case, Task
from identity.models import User


class TaskServiceError(ValueError):
    """Invalid input, carrying the exact error shape the API returns."""

    def __init__(self, message: str, fields: dict[str, list[str]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or {}


def create_task(
    case: Case,
    title: str,
    *,
    assignee: User | None = None,
    due_date: Any = None,
) -> Task:
    """Create a new task on `case`.

    The caller must already have organisational permission (enforced by the caller / view).
    Returns the new task row.
    """
    if not title.strip():
        raise TaskServiceError("title is required", {"title": ["required"]})

    task = Task.objects.create(
        case=case,
        title=title[:500],
        assignee=assignee,
        due_date=due_date,
        status="Waiting",
    )
    return task


def toggle_task(task: Task) -> Task:
    """Toggle a task status between `Waiting` and `InProgress`.

    The task's `status` field is flipped. If it was `Waiting` it becomes `InProgress`; if it was
    `InProgress` it becomes `Waiting`. The function is idempotent: calling it twice restores the
    original status.
    """
    if task.status == "Waiting":
        task.status = "InProgress"
    elif task.status == "InProgress":
        task.status = "Waiting"
    else:
        # Already some other status (Completed etc.); leave as-is.
        pass
    task.save(update_fields=["status", "updated_at"])
    return task


def assign_task(task: Task, assignee: User) -> Task:
    """Reassign a task to a new user within the task's case organisation."""
    task.assignee = assignee
    task.save(update_fields=["assignee", "updated_at"])
    return task
