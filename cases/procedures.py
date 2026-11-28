"""Procedure creation shared by the case and alert procedure endpoints (T2 P5).

Kept out of `cases/views.py`/`alerts/views.py` because both edges construct the same row, and it
stays free of TheHive field-name literals (the wire keys `occurDate`, `patternId`, … are parsed in
the view layer, which `tests/conformance/test_wire_boundary.py` recognises as the boundary).

No HTTP concerns live here: a caller resolves/authorises the parent and parses the body, then calls
this with plain Python values.
"""

from __future__ import annotations

from typing import Any

from cases.ledger import append_timeline_event
from cases.models import TTP, Procedure
from identity.audit import record_procedure_change


def create_procedure(
    *,
    case: Any = None,
    alert: Any = None,
    occur_date: Any = None,
    pattern_id: str = "",
    pattern_name: str = "",
    tactic: str = "",
    description: str = "",
    ttp: TTP | None = None,
) -> Procedure:
    """Persist one procedure; exactly one of `case`/`alert` must be given.

    The model's `procedure_exactly_one_parent` CHECK enforces that even if a caller gets it wrong,
    so this function does not duplicate the rule — it just forwards.
    """
    return Procedure.objects.create(
        case=case,
        alert=alert,
        ttp=ttp,
        occur_date=occur_date,
        pattern_id=pattern_id[:255],
        pattern_name=pattern_name[:255],
        tactic=tactic[:100],
        description=description,
    )


def attach_case_procedure(
    *,
    request: Any,
    actor: Any,
    case: Any,
    **kwargs: Any,
) -> Procedure:
    """Create a case procedure and record the two side effects callers must not forget.

    `create_procedure` stays a plain row writer for alert-scoped procedures and import paths. The
    case UI and case API both go through this wrapper so an attached TTP is auditable and visible
    on the case timeline regardless of which surface an analyst used.
    """
    procedure = create_procedure(case=case, **kwargs)
    record_procedure_change(request, actor=actor, case=case, procedure=procedure)
    label = procedure.pattern_id or procedure.pattern_name or "procedure"
    append_timeline_event(
        case,
        title=f"Procedure attached: {label}",
        kind="procedure-attached",
        description=(
            f"ATT&CK pattern {label}"
            + (f" — {procedure.pattern_name}" if procedure.pattern_name else "")
        ),
        actor=actor,
        metadata={
            "procedure_id": str(procedure.id),
            "pattern_id": procedure.pattern_id,
            "pattern_name": procedure.pattern_name,
            "tactic": procedure.tactic,
            "ttp_id": str(procedure.ttp_id) if procedure.ttp_id else None,
        },
    )
    return procedure


__all__ = ["attach_case_procedure", "create_procedure"]
