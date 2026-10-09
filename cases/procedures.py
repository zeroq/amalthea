"""Procedure creation shared by the case and alert procedure endpoints (T2 P5).

Kept out of `cases/views.py`/`alerts/views.py` because both edges construct the same row, and it
stays free of TheHive field-name literals (the wire keys `occurDate`, `patternId`, … are parsed in
the view layer, which `tests/conformance/test_wire_boundary.py` recognises as the boundary).

No HTTP concerns live here: a caller resolves/authorises the parent and parses the body, then calls
this with plain Python values.
"""

from __future__ import annotations

from typing import Any

from cases.models import TTP, Procedure


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


__all__ = ["create_procedure"]
