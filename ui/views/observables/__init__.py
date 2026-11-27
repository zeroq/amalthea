"""Observable views and case-scoped observable CRUD.

Compatibility shim for the historical ``ui.views.observables`` module. Global CRUD lives in
`observables.py`; case-link actions live in `case_observables.py`.
"""

from .case_observables import (
    case_observable_create,
    case_observable_remove,
    case_observable_update,
)
from .observables import (
    _observable_bulk_redirect,
    _render_observable_edit_form,
    case_extract_observables,
    logger,
    observable_bulk,
    observable_delete,
    observable_detail,
    observable_edit,
    observable_list,
)

__all__ = [
    "_observable_bulk_redirect",
    "_render_observable_edit_form",
    "case_extract_observables",
    "case_observable_create",
    "case_observable_remove",
    "case_observable_update",
    "logger",
    "observable_bulk",
    "observable_delete",
    "observable_detail",
    "observable_edit",
    "observable_list",
]
