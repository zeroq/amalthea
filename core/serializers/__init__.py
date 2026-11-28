"""Wire representations for the MVP endpoints.

Compatibility shim for the historical ``core.serializers`` module. Renderers now live in the
domain modules alongside this package; the single-renderer rule is unchanged: the alert list,
the alert detail and the merge response all read from `alert_json`, and the case page, the case
API and the import response all read from `case_json`.

Two conventions come from the recorded TheHive 5.8.0 examples and are deliberate:

* the id is exposed as **`_id`** (TheHive's own spelling) *and* as `id`, because a client written
  against either reading should not 400 on the other;
* timestamps are ISO-8601 with a timezone, never naive, because `USE_TZ` is on and a naive
  rendering is a bug that only shows up on the other side of the Atlantic.

`raw_payload` is **not** inlined here. It has its own `/raw` endpoint because D9 promises the
sender's bytes; a list of 200 alerts each carrying a full SIEM event would make that promise
meaningless and the payload enormous.
"""

from ._common import _iso, _username
from .alerts import alert_json
from .automation import automation_run_json, playbook_json
from .cases import (
    CustomFieldWireError,
    attachment_json,
    case_json,
    case_template_json,
    comment_json,
    custom_event_json,
    custom_field_json,
    custom_field_updates_from_wire,
    custom_field_value_json,
    page_json,
    share_json,
    task_json,
    timeline_event_json,
)
from .identity import (
    organisation_admin_json,
    organisation_json,
    user_admin_json,
    user_json,
)
from .observables import (
    observable_json,
    observable_suggestion_json,
    rejected_observable_json,
)
from .taxonomy import (
    _status_json,
    alert_status_json,
    case_status_json,
    observable_type_json,
    procedure_json,
    tag_json,
    ttp_json,
)

__all__ = [
    "CustomFieldWireError",
    "_iso",
    "_status_json",
    "_username",
    "alert_json",
    "alert_status_json",
    "attachment_json",
    "automation_run_json",
    "case_json",
    "case_status_json",
    "case_template_json",
    "comment_json",
    "custom_event_json",
    "custom_field_json",
    "custom_field_updates_from_wire",
    "custom_field_value_json",
    "observable_json",
    "observable_suggestion_json",
    "observable_type_json",
    "organisation_admin_json",
    "organisation_json",
    "page_json",
    "playbook_json",
    "procedure_json",
    "rejected_observable_json",
    "share_json",
    "tag_json",
    "task_json",
    "timeline_event_json",
    "ttp_json",
    "user_admin_json",
    "user_json",
]
