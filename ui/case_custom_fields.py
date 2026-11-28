"""Backwards-compatible imports for the shared case custom-field helpers.

The helpers now live in `cases.custom_fields` so the API and the UI share one
implementation. Existing UI imports stay valid while callers migrate.
"""

from cases.custom_fields import (
    MAX_CUSTOM_FIELD_VALUE_BYTES,
    case_custom_field_rows,
    coerce_custom_field_value,
    update_case_custom_field_value,
)

__all__ = [
    "MAX_CUSTOM_FIELD_VALUE_BYTES",
    "case_custom_field_rows",
    "coerce_custom_field_value",
    "update_case_custom_field_value",
]
