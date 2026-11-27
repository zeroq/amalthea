"""Compatibility shim for the former ``alerts.views`` module.

``alerts.urls`` imports ``views`` from this package. All routed handlers are re-exported here so
existing imports and callers keep working after the §13.4 split.
"""

from .alerts import alert_detail, alert_list, alert_raw
from .bulk import alert_bulk_update as alert_bulk_update
from .comments import alert_comment_list
from .merge import alert_import, alert_merge
from .observables import alert_observable_add
from .procedures import alert_procedure_create as alert_procedure_create
from .procedures import alert_procedures_create as alert_procedures_create
from .statuses import alert_status_collection, alert_status_detail
from .tags import alert_tag_add, alert_tag_link

__all__ = [
    "alert_comment_list",
    "alert_detail",
    "alert_import",
    "alert_list",
    "alert_merge",
    "alert_observable_add",
    "alert_raw",
    "alert_status_collection",
    "alert_status_detail",
    "alert_tag_add",
    "alert_tag_link",
]
