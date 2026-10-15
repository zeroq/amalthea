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
database accepts. `tests/conformance/test_enum_contracts.py` asserts that agreement.
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

#: Plan §5.1 AutomationRun: who started the run — a domain event or an analyst.
#: The plan's own spelling for the first value is ``event``; the implemented value is
#: ``trigger`` so it cannot be confused with ``trigger_event`` (the event *name*), and the
#: deviation is recorded in ``docs/spec/deviations.md`` (P12-3).
AUTOMATION_RUN_TRIGGERED_BY_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("trigger", "Trigger"),
    ("manual", "Manual"),
)

#: Plan §6.2: the stable 3-bucket grouping for metrics and automation triggers.
#: Case statuses map onto these; `IngestionSource`-driven custom statuses must too.
CASE_STAGES: Final[tuple[str, ...]] = ("New", "InProgress", "Closed")
#: ADR-002 §D4 lists `Imported` as a legal *alert* stage alongside New/InProgress/Closed.
ALERT_STAGES: Final[tuple[str, ...]] = ("New", "InProgress", "Closed", "Imported")

#: Django requires `(value, label)` pairs for `choices`. Derived from ALERT_STAGES so the model and
#: `compat/enums.py` cannot disagree about which stages exist. The label is the value itself: the
#: human-readable TheHive wording is applied at the wire boundary, not baked into the domain.
ALERT_STAGE_CHOICES: Final[tuple[tuple[str, str], ...]] = tuple((s, s) for s in ALERT_STAGES)

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

#: `identity.User.role` (plan §6.2, ADR-002 §D4).
#:
#: `viewer` is the read-only role. It was spelled `read-only` until ADR-006 renamed it (the value
#: is what API clients and the admin see; "viewer" is the noun for the person, and it stops the
#: role from being confused with the unrelated *share* vocabulary — see `core/serializers.py`,
#: where a TheHive share still reports `profileName: "read-only"`).
USER_ROLE_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("superAdmin", "Super Administrator"),
    ("admin", "Organisation Administrator"),
    ("analyst", "Analyst"),
    ("viewer", "Viewer"),
)

#: Every valid `User.role` value, in choice order.
USER_ROLE_VALUES: Final[tuple[str, ...]] = tuple(value for value, _ in USER_ROLE_CHOICES)

#: The read-only role value. Never hardcode the literal: import this (ADR-006 decision 1).
VIEWER_ROLE: Final[str] = "viewer"

#: The two administrative roles. Named so a caller never hardcodes the literal, and so
#: "who may perform an administrative write" has exactly one definition (`is_admin`).
SUPER_ADMIN_ROLE: Final[str] = "superAdmin"
ADMIN_ROLE: Final[str] = "admin"

#: Roles permitted to change a case's `data_classification` (PLAN-2026-10-14 Q-D5): reclassifying
#: an incident changes how it is reported, so it is an administrative act. The *other* three
#: classification fields (`scope`/`case_type`/`phase`) are any-writer.
CASE_CLASSIFICATION_ROLES: Final[tuple[str, ...]] = (ADMIN_ROLE, SUPER_ADMIN_ROLE)


# --- Amalthea-native case classification (PLAN-2026-10-14) --------------------
#
# These four vocabularies are **Amalthea extensions**, not TheHive fields: TheHive 5 has no
# case-level scope / type / data-classification / phase, so ``compat/enums.py`` does not map
# them and the ``case_json`` key names (`scope`, `caseType`, `dataClassification`, `phase`) are
# ours alone (Q-D3) — we claim no TheHive semantics for them.
#
# `phase` is a **separate IR-lifecycle axis** from `CaseStatus.stage`: `stage` stays the stable
# 3-bucket New/InProgress/Closed *openness* axis that automation triggers and ``CASE_STAGES``
# key on, while `phase` records the analyst's position in the incident-response loop. Keep the
# two distinct — do **not** fold `phase` into `CASE_STAGES` (Q-D2).
#
# Every set carries an explicit ``unset`` ("Unset/Unknown") so a fresh case can say "not yet
# classified" rather than guessing (Q-D8). `case_type` is a closed enum: a new type costs a
# small additive migration rather than being an open string (Q-D9).
CASE_SCOPE_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("single_asset_user", "Single Asset/User"),
    ("multiple_assets_users", "Multiple Assets/Users"),
    ("single_department", "Single Department"),
    ("multiple_departments", "Multiple Departments"),
    ("complete_enterprise", "Complete Enterprise"),
    ("third_party_supplier", "Third Party/Supplier"),
)
CASE_TYPE_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("malware", "Malware"),
    ("phishing", "Phishing"),
    ("password_leak", "Password Leak"),
    ("ransomware", "Ransomware"),
    ("bec", "BEC"),
)
DATA_CLASSIFICATION_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("no_data", "No Data"),
    ("public_data", "Public Data"),
    ("personal_data", "Personal Data (PII)"),
    ("sensitive_data", "Sensitive Data (Credentials, IP)"),
)
CASE_PHASE_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    ("unset", "Unset/Unknown"),
    ("initial_triage", "Initial Triage"),
    ("containment", "Containment"),
    ("analysis", "Analysis"),
    ("remediation", "Remediation"),
    ("cleanup", "Cleanup"),
    ("close", "Close"),
)


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
