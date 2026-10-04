"""Canonical value sets for every enumerated field, plus the DB constraints that back them.

Why this module exists (REVIEW-2026-10-03 **H5** / **L1**): until now no enum field had
`choices` *or* a `CheckConstraint`, so a typo like `stage="inprogres"` or `severity=7`
was accepted by every layer — the ORM, a future serializer, and the database.

Every closed set is now enforced **twice**:

* ``choices=`` on the field → `full_clean()` / DRF serializers reject out-of-range
  values before a query is built (this is what makes AC2.5's "out-of-range → 400"
  implementable once serializers exist);
* ``CheckConstraint`` on the model → the *database* refuses the row even for raw SQL,
  a `bulk_create`, or a direct ``UPDATE``.

Both SQLite and Postgres enforce CHECK constraints, so the constraint half is testable
in dev (it is not one of the nine Postgres-only features listed in plan §12 R10).

This is the *domain* side of the wire contract; ``compat/enums.py`` derives its
TheHive labels from these tuples so a label can never drift from the value the
database accepts. `tests/conformance/test_enum_constraints.py` asserts that agreement.
"""

from __future__ import annotations

from typing import Any, Final

from django.db import models
from django.db.models import Q

# --- Value sets (ADR-002 §D4, plan §6.1/§6.2) -------------------------------

#: ``(value, TheHive display label)``. ``compat.enums.SEVERITY_LABELS`` is built from
#: this tuple; the labels are the wire labels, so they live here too.
SEVERITY_CHOICES: Final[tuple[tuple[int, str], ...]] = (
    (1, "Low"),
    (2, "Medium"),
    (3, "High"),
    (4, "Critical"),
)
SEVERITY_MIN: Final[int] = 1
SEVERITY_MAX: Final[int] = 4

TLP_CHOICES: Final[tuple[tuple[int, str], ...]] = (
    (0, "White"),
    (1, "Green"),
    (2, "Amber"),
    (3, "Red"),
    (4, "Unknown"),
)
TLP_MIN: Final[int] = 0
TLP_MAX: Final[int] = 4

PAP_CHOICES: Final[tuple[tuple[int, str], ...]] = (
    (0, "White"),
    (1, "Green"),
    (2, "Amber"),
    (3, "Red"),
)
PAP_MIN: Final[int] = 0
PAP_MAX: Final[int] = 3

#: Plan §6.1 Task: `Waiting`/`InProgress`/`Completed`/`Cancel`.
TASK_STATUS_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("Waiting", "Waiting"),
    ("InProgress", "InProgress"),
    ("Completed", "Completed"),
    ("Cancel", "Cancel"),
)

#: Plan §6.1 AutomationRun: `Pending`/`Running`/`Success`/`Failed`.
AUTOMATION_RUN_STATUS_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("Pending", "Pending"),
    ("Running", "Running"),
    ("Success", "Success"),
    ("Failed", "Failed"),
)

#: Plan §6.2: the stable 3-bucket grouping for metrics and automation triggers.
#: Case statuses map onto these; `IngestionSource`-driven custom statuses must too.
CASE_STAGES: Final[tuple[str, ...]] = ("New", "InProgress", "Closed")
#: ADR-002 §D4 lists `Imported` as a legal *alert* stage alongside New/InProgress/Closed.
ALERT_STAGES: Final[tuple[str, ...]] = ("New", "InProgress", "Closed", "Imported")

#: Plan §6.2 CustomField: one definition serves both case and alert values.
CUSTOM_FIELD_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("string", "string"),
    ("integer", "integer"),
    ("float", "float"),
    ("boolean", "boolean"),
    ("date", "date"),
    ("url", "url"),
)

#: `identity.ApiKey.scope` (plan §6.2).
API_KEY_SCOPES: Final[tuple[str, ...]] = ("read", "readwrite")


# --- Constraint builders ------------------------------------------------------


def in_range(name: str, field: str, low: int, high: int) -> models.CheckConstraint:
    """CHECK that ``field`` is within the inclusive ``low..high`` range."""
    condition = Q(**{f"{field}__gte": low, f"{field}__lte": high})
    return models.CheckConstraint(condition=condition, name=name)


def in_values(name: str, field: str, values: tuple[Any, ...]) -> models.CheckConstraint:
    """CHECK that ``field`` is one of ``values``."""
    condition = Q(**{f"{field}__in": values})
    return models.CheckConstraint(condition=condition, name=name)


def severity_choice_kwargs() -> dict[str, Any]:
    """`choices` fragment shared by every field carrying TheHive severity."""
    return {"choices": list(SEVERITY_CHOICES)}
