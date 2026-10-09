"""Wire parsing for TheHive's `InputProcedure` (T2 P5), shared by the case and alert edges.

TheHive exposes the identical procedure shape on two parents — `POST /case/{id}/procedure(s)` and
`POST /alert/{id}/procedure(s)` — so the field-name knowledge (which is part of the wire boundary)
lives here once rather than being copied into both view modules. `compat/` is an allowed boundary
for TheHive wire literals; `cases.procedures.create_procedure` takes the already-parsed values and
never sees a wire key.

Errors are returned as a `(field, message)` pair rather than a DRF `Response`, because authorization
and response shape belong to the view, not to the parser.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from compat.time import parse_timestamp


def parse_procedure(
    payload: Any,
    *,
    resolve_ttp: Callable[[str], Any | None],
) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
    """Translate one `InputProcedure` into `create_procedure` kwargs.

    Returns `(kwargs, None)` on success or `(None, (field, message))` on a bad shape. `occurDate`
    accepts the epoch-milliseconds integer TheHive sends (and the ISO strings our own export
    emits); `ttpId` is the Amalthea extension — an unknown one is a 400, not a silently dropped FK.
    """
    if not isinstance(payload, Mapping):
        return None, ("procedure", "must be an object")

    occur_date = None
    if "occurDate" in payload and payload["occurDate"] is not None:
        occur_date = parse_timestamp(payload["occurDate"])
        if occur_date is None:
            return None, ("occurDate", "is not a valid timestamp")

    ttp = None
    if payload.get("ttpId"):
        ttp = resolve_ttp(str(payload["ttpId"]))
        if ttp is None:
            return None, ("ttpId", "unknown ttpId")

    return (
        {
            "occur_date": occur_date,
            "pattern_id": str(payload.get("patternId") or "")[:255],
            "pattern_name": str(payload.get("patternName") or "")[:255],
            "tactic": str(payload.get("tactic") or "")[:100],
            "description": str(payload.get("description") or ""),
            "ttp": ttp,
        },
        None,
    )


__all__ = ["parse_procedure"]
