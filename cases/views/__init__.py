# ruff: noqa: F401
"""Compatibility package for the T1 case/task/observable API views."""

from ._common import _as_uuid, _case_org_scope
from ._common import _resolve_login as _resolve_login
from .attachments import case_attachment_detail, case_attachment_download, case_attachment_list
from .bulk import case_bulk_update, case_merge, observable_bulk_update, task_bulk_update
from .cases import (
    case_alert_remove,
    case_collection,
    case_custom_event_create,
    case_detail,
    case_flow,
    case_timeline,
    custom_event_detail,
    custom_field_list,
)
from .comments import case_comment_list, comment_detail
from .export import _case_export_document, case_export
from .observables import (
    _observable_affected_cases,
    _observable_cross_case_guard,
    _set_observable_fields,
    case_observable_add,
    case_observable_list,
    observable_detail,
)
from .pages import case_page_detail, case_page_list
from .procedures import (
    _resolve_ttp_value,
    case_procedure_create,
    case_procedures_create,
    procedure_bulk_delete,
    procedure_detail,
    ttp_collection,
    ttp_detail,
)
from .shares import case_share_list, share_detail
from .statuses import case_status_collection, case_status_detail, taxonomy
from .tags import case_tag_link, observable_tag_link, tag_collection, tag_detail
from .tasks import case_task_create, case_task_list, task_detail
from .templates import (
    _apply_template,
    _resolve_case_template,
    case_apply_template,
    case_template_collection,
    case_template_detail,
)

__all__ = [
    "case_alert_remove",
    "case_apply_template",
    "case_attachment_detail",
    "case_attachment_download",
    "case_attachment_list",
    "case_bulk_update",
    "case_collection",
    "case_comment_list",
    "case_custom_event_create",
    "case_detail",
    "case_export",
    "case_flow",
    "case_merge",
    "case_observable_add",
    "case_observable_list",
    "case_page_detail",
    "case_page_list",
    "case_procedure_create",
    "case_procedures_create",
    "case_share_list",
    "case_status_collection",
    "case_status_detail",
    "case_tag_link",
    "case_task_create",
    "case_task_list",
    "case_template_collection",
    "case_template_detail",
    "case_timeline",
    "comment_detail",
    "custom_event_detail",
    "custom_field_list",
    "observable_bulk_update",
    "observable_detail",
    "observable_tag_link",
    "procedure_bulk_delete",
    "procedure_detail",
    "share_detail",
    "tag_collection",
    "tag_detail",
    "task_bulk_update",
    "task_detail",
    "taxonomy",
    "ttp_collection",
    "ttp_detail",
]
