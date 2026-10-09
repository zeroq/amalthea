"""`/api/v1/case/...` and `/api/v1/observable/...` routes (T1 subset).

**Ordering is load-bearing.** `<str:case_id>` matches any single segment, so a bare
`case/<str:case_id>` registered first would swallow `case/task`, `case/timeline` and
`case/observable` as a case whose identifier is the word "task". The specific sub-resources are
registered first for that reason; keep them above the bare detail route.

`{idOrNumber}` is one `<str:...>` segment and the identifier is resolved in the view, not by the URL
pattern: Django cannot route "42 or a UUID" with two converters without trying both, and the
resolution rule (UUID first, then `number`) belongs to `alerts.escalation`, documented next to it.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Sub-resources first: they are more specific than the bare `{idOrNumber}` segment.
    # One entry per collection. `case_task_list` dispatches GET/POST itself: two `path()` entries for
    # the same pattern means Django resolves the first one for *both* verbs, so the create view is
    # unreachable and POST silently 405s.
    path("case/<str:case_id>/task", views.case_task_list, name="case-task-list"),
    path("case/<str:case_id>/task/", views.case_task_list, name="case-task-list-slash"),
    path("case/<str:case_id>/observable", views.case_observable_list, name="case-observable-list"),
    path(
        "case/<str:case_id>/observable/",
        views.case_observable_list,
        name="case-observable-list-slash",
    ),
    path("case/<str:case_id>/timeline", views.case_timeline, name="case-timeline"),
    # T2 tag link. Registered with the other sub-resources, i.e. above the bare detail route:
    # `<str:case_id>` would otherwise swallow `tag` as a case identifier.
    path("case/<str:case_id>/tag", views.case_tag_link, name="case-tag-link"),
    path("case/<str:case_id>/tag/", views.case_tag_link, name="case-tag-link-slash"),
    # Unlink and customEvent are two segments deep: registered *above* the bare detail route for
    # the same reason as the collections — `<str:case_id>` would otherwise swallow `alert` and
    # `customEvent` as a case identifier. Both spellings of customEvent point at the one
    # POST-only view, so there is no verb collision between the twin entries.
    path(
        "case/<str:case_id>/alert/<str:alert_id>",
        views.case_alert_remove,
        name="case-alert-remove",
    ),
    path(
        "case/<str:case_id>/customEvent",
        views.case_custom_event_create,
        name="case-custom-event-create",
    ),
    path(
        "case/<str:case_id>/customEvent/",
        views.case_custom_event_create,
        name="case-custom-event-create-slash",
    ),
    path("case/<str:case_id>", views.case_detail, name="case-detail"),
    path("case/<str:case_id>/", views.case_detail, name="case-detail-slash"),
    # T2 tag link, above `observable/<id>` so `<str:observable_id>` cannot swallow `tag`.
    path(
        "observable/<str:observable_id>/tag",
        views.observable_tag_link,
        name="observable-tag-link",
    ),
    path(
        "observable/<str:observable_id>/tag/",
        views.observable_tag_link,
        name="observable-tag-link-slash",
    ),
    path("observable/<str:observable_id>", views.observable_detail, name="observable-detail"),
    # T2 vocabularies. `caseStatus` is a single segment, disjoint from `case/<id>` (two segments)
    # and from `task/`; `tag`/`tag/<id>` are disjoint from every other prefix here except
    # `task/`, which differs at the third character. All registered before `compat.urls`.
    path("caseStatus", views.case_status_collection, name="case-status-collection"),
    path("caseStatus/", views.case_status_collection, name="case-status-collection-slash"),
    path("caseStatus/<str:status_id>", views.case_status_detail, name="case-status-detail"),
    path(
        "caseStatus/<str:status_id>/",
        views.case_status_detail,
        name="case-status-detail-slash",
    ),
    path("tag", views.tag_collection, name="tag-collection"),
    path("tag/", views.tag_collection, name="tag-collection-slash"),
    path("tag/<str:tag_id>", views.tag_detail, name="tag-detail"),
    path("tag/<str:tag_id>/", views.tag_detail, name="tag-detail-slash"),
    # Distinct top-level prefixes (`task/`, `customEvent/`, `customField/`) — no collision with
    # `case/<id>`, and all of them land before `compat.urls`' catch-all by construction.
    path("task/<str:task_id>", views.task_detail, name="task-detail"),
    path("customEvent/<str:event_id>", views.custom_event_detail, name="custom-event-detail"),
    path("customField", views.custom_field_list, name="custom-field-list"),
    path("customField/", views.custom_field_list, name="custom-field-list-slash"),
    # Collection routes are registered with and without the trailing slash: CommonMiddleware's
    # APPEND_SLASH would redirect a POST and lose its body, so the slashless spellings are explicit.
    path("case", views.case_collection, name="case-collection"),
    path("case/", views.case_collection, name="case-collection-slash"),
]
