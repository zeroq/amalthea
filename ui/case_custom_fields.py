"""Case custom-field read/format/coerce/update helpers.

The case-detail Custom tab and its edit endpoint both need the same value formatting and
type coercion rules. Keeping them here avoids a template filter that would have to duplicate
`CustomField.type` knowledge, and keeps `ui/views.py` focused on request flow.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any

from cases.ledger import append_timeline_event
from cases.models import Case, CaseCustomFieldValue
from cases.update import CaseUpdateError

MAX_CUSTOM_FIELD_VALUE_BYTES = 64 * 1024


def _row_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _row(value: CaseCustomFieldValue) -> dict[str, Any]:
    field = value.custom_field
    raw = value.value

    if isinstance(raw, (dict, list)):
        json_text = _row_json(raw)
        display = None
        edit_value = json_text
        edit_is_json = True
    elif raw is None or raw == "":
        json_text = None
        display = "—"
        edit_value = ""
        edit_is_json = False
    elif isinstance(raw, bool):
        json_text = None
        display = "Yes" if raw else "No"
        edit_value = "true" if raw else "false"
        edit_is_json = False
    else:
        json_text = None
        display = str(raw)
        edit_value = str(raw)
        edit_is_json = False

    return {
        "id": str(value.id),
        "name": field.name,
        "group": field.group,
        "type": field.type,
        "display": display,
        "json_text": json_text,
        "edit_value": edit_value,
        "edit_is_json": edit_is_json,
    }


def case_custom_field_rows(case: Case) -> list[dict[str, Any]]:
    """Return the ordered row contract for every custom-field value on *case*."""
    values = case.custom_field_values.select_related("custom_field").order_by(
        "order", "custom_field__name"
    )
    return [_row(value) for value in values]


def _bad_value(message: str) -> CaseUpdateError:
    return CaseUpdateError("BadRequest", message, {"value": [message]})


def coerce_custom_field_value(field_type: str, raw: Any, *, is_json: bool = False) -> Any:
    """Coerce a submitted value to the canonical JSON type for *field_type*.

    Empty/whitespace input is a deliberate clear and returns `""`. Invalid typed input raises a
    `CaseUpdateError` so the API and UI can report the same 400/message.
    """
    if raw is None:
        raw = ""
    if isinstance(raw, str):
        raw = raw.strip()
    if raw == "":
        return ""

    if isinstance(raw, str) and len(raw.encode("utf-8")) > MAX_CUSTOM_FIELD_VALUE_BYTES:
        raise _bad_value("Value exceeds the 64 KiB limit")

    if is_json:
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise _bad_value(f"Invalid JSON: {exc.msg}") from exc

    if not isinstance(raw, str):
        raw = str(raw)

    if field_type == "integer":
        try:
            return int(raw)
        except ValueError as exc:
            raise _bad_value("Value must be an integer") from exc
    if field_type == "float":
        try:
            value = float(raw)
        except ValueError as exc:
            raise _bad_value("Value must be a number") from exc
        if not math.isfinite(value):
            raise _bad_value("Value must be a finite number")
        return value
    if field_type == "boolean":
        lowered = raw.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise _bad_value("Value must be true or false")
    if field_type == "date":
        try:
            return dt.date.fromisoformat(raw).isoformat()
        except ValueError as exc:
            raise _bad_value("Value must be a date in YYYY-MM-DD form") from exc
    return raw


def update_case_custom_field_value(
    case: Case, value: CaseCustomFieldValue, raw: Any, *, actor: Any
) -> dict[str, Any]:
    """Update one existing value and append its timeline event.

    The row is supplied by the caller after a case-scoped lookup, so a foreign value id can never
    reach this helper. A no-op edit returns without writing or emitting an event.
    """
    coerced = coerce_custom_field_value(
        value.custom_field.type,
        raw,
        is_json=isinstance(value.value, (dict, list)),
    )
    if coerced == value.value:
        return _row(value)

    value.value = coerced
    value.save(update_fields=["value", "updated_at"])
    append_timeline_event(
        case,
        title=f"Custom field updated: {value.custom_field.name}",
        kind="custom-field-updated",
        actor=actor,
        metadata={"field": value.custom_field.name},
    )
    return _row(value)
