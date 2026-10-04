from __future__ import annotations

from enum import Enum, StrEnum

from core.enums import PAP_CHOICES, SEVERITY_CHOICES, TLP_CHOICES

# The label dicts are *derived* from core.enums (the same tuples the ORM `choices`
# and the database CHECK constraints are built from) so a wire label can never drift
# from a value the database accepts. REVIEW-2026-10-03 H5.


class Severity(int, Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


SEVERITY_LABELS = dict(SEVERITY_CHOICES)


class TLP(int, Enum):
    WHITE = 0
    GREEN = 1
    AMBER = 2
    RED = 3
    UNKNOWN = 4


TLP_LABELS = dict(TLP_CHOICES)


class PAP(int, Enum):
    WHITE = 0
    GREEN = 1
    AMBER = 2
    RED = 3


PAP_LABELS = dict(PAP_CHOICES)


class TaskStatus(StrEnum):
    WAITING = "Waiting"
    INPROGRESS = "InProgress"
    COMPLETED = "Completed"
    CANCEL = "Cancel"


def stage_from_case_status(status: str) -> str:
    s = status.lower()
    if s in ("new",):
        return "New"
    if s in ("inprogress", "investigating", "contained"):
        return "InProgress"
    if s in ("closed", "dismissed"):
        return "Closed"
    return "New"


def stage_from_alert_status(status: str) -> str:
    s = status.lower()
    if s in ("new",):
        return "New"
    if s in ("triaged", "inprogress"):
        return "InProgress"
    # REVIEW-2026-10-03 M12: `"imported"` used to be listed in the Closed tuple *and*
    # in a branch below, so the branch was unreachable while the schema seeded
    # AlertStatus("Imported", stage="Imported") — a schema<->compat contract mismatch
    # that would surface in Phase 5 on import/{caseId}. ADR-002 §D4 lists `Imported`
    # as a legal alert stage, so it maps to itself and the two now agree.
    if s in ("imported",):
        return "Imported"
    if s in ("closed", "dismissed"):
        return "Closed"
    return "New"
