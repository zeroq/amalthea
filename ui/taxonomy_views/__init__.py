# ruff: noqa: F401
"""Compatibility package for the taxonomy mutation views."""

from ._common import (
    _DELETE_PARTIAL,
    _STATUS_PARTIALS,
    _TYPE_PARTIAL,
    _audit,
    _audit_details,
    _delete_error,
    _status_error,
    _success,
    _type_error,
)
from .alert_statuses import (
    alert_status_create,
    alert_status_delete,
    alert_status_edit,
)
from .case_statuses import (
    case_status_create,
    case_status_delete,
    case_status_edit,
)
from .observable_types import (
    observable_type_create,
    observable_type_delete,
    observable_type_edit,
)
from .terms import (
    _term_create_context,
    _term_edit_context,
    taxonomy_term_create,
    taxonomy_term_delete,
    taxonomy_term_edit,
)
