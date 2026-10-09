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
    # T2 P2 — collaboration. All are sub-resources of an individual case, so they must be
    # registered ahead of the bare `case/<case_id>` route on line 54 to avoid being swallowed.
    path("case/<str:case_id>/comment", views.case_comment_list, name="case-comment-list"),
    path("case/<str:case_id>/comment/", views.case_comment_list, name="case-comment-list-slash"),
    path("case/<str:case_id>/page", views.case_page_list, name="case-page-list"),
    path("case/<str:case_id>/page/", views.case_page_list, name="case-page-list-slash"),
    path(
        "case/<str:case_id>/page/<str:page_id>",
        views.case_page_detail,
        name="case-page-detail",
    ),
    path(
        "case/<str:case_id>/page/<str:page_id>/",
        views.case_page_detail,
        name="case-page-detail-slash",
    ),
    path("case/<str:case_id>/flow", views.case_flow, name="case-flow"),
    path("case/<str:case_id>/flow/", views.case_flow, name="case-flow-slash"),
    path("case/<str:case_id>/shares", views.case_share_list, name="case-share-list"),
    path("case/<str:case_id>/shares/", views.case_share_list, name="case-share-list-slash"),
    path(
        "case/<str:case_id>/share/<str:share_id>",
        views.share_detail,
        name="case-share-detail",
    ),
    path(
        "case/<str:case_id>/share/<str:share_id>/",
        views.share_detail,
        name="case-share-detail-slash",
    ),
    # T2 P3 — attachments. `attachments` (collection) and `attachment/<id>[ /download]` are
    # case sub-resources, so they stay above the bare `case/<case_id>` route for the same reason as
    # the P2 block. `<str:attachment_id>` matches one segment, so the `/download` route cannot be
    # swallowed by the detail route even though it is listed first.
    path(
        "case/<str:case_id>/attachments",
        views.case_attachment_list,
        name="case-attachment-list",
    ),
    path(
        "case/<str:case_id>/attachments/",
        views.case_attachment_list,
        name="case-attachment-list-slash",
    ),
    path(
        "case/<str:case_id>/attachment/<str:attachment_id>/download",
        views.case_attachment_download,
        name="case-attachment-download",
    ),
    path(
        "case/<str:case_id>/attachment/<str:attachment_id>",
        views.case_attachment_detail,
        name="case-attachment-detail",
    ),
    path(
        "case/<str:case_id>/attachment/<str:attachment_id>/",
        views.case_attachment_detail,
        name="case-attachment-detail-slash",
    ),
    path("comment/<str:comment_id>", views.comment_detail, name="comment-detail"),
    path("comment/<str:comment_id>/", views.comment_detail, name="comment-detail-slash"),
    # T2 P4 — bulk, merge and case templates, all under `case/`. `_bulk`/`template` are single
    # segments that the bare `case/<case_id>` route below would otherwise swallow as identifiers,
    # so they are registered here; `_merge/<ids>` carries a comma-separated list in one segment.
    path("case/_bulk", views.case_bulk_update, name="case-bulk-update"),
    path("case/_bulk/", views.case_bulk_update, name="case-bulk-update-slash"),
    path("case/_bulk/caseTemplate", views.case_apply_template, name="case-apply-template"),
    path("case/_bulk/caseTemplate/", views.case_apply_template, name="case-apply-template-slash"),
    path("case/_merge/<str:case_ids>", views.case_merge, name="case-merge"),
    path("case/template", views.case_template_collection, name="case-template-collection"),
    path("case/template/", views.case_template_collection, name="case-template-collection-slash"),
    path(
        "case/template/<str:template_id>", views.case_template_detail, name="case-template-detail"
    ),
    path(
        "case/template/<str:template_id>/",
        views.case_template_detail,
        name="case-template-detail-slash",
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
    # T2 P4 bulk. `_bulk` is one segment, so it must precede the bare detail routes that would
    # otherwise treat the literal word `_bulk` as an id.
    path("observable/_bulk", views.observable_bulk_update, name="observable-bulk-update"),
    path("observable/_bulk/", views.observable_bulk_update, name="observable-bulk-update-slash"),
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
    path("task/_bulk", views.task_bulk_update, name="task-bulk-update"),
    path("task/_bulk/", views.task_bulk_update, name="task-bulk-update-slash"),
    path("task/<str:task_id>", views.task_detail, name="task-detail"),
    path("taxonomy", views.taxonomy, name="taxonomy"),
    path("taxonomy/", views.taxonomy, name="taxonomy-slash"),
    path("customEvent/<str:event_id>", views.custom_event_detail, name="custom-event-detail"),
    path("customField", views.custom_field_list, name="custom-field-list"),
    path("customField/", views.custom_field_list, name="custom-field-list-slash"),
    # Collection routes are registered with and without the trailing slash: CommonMiddleware's
    # APPEND_SLASH would redirect a POST and lose its body, so the slashless spellings are explicit.
    path("case", views.case_collection, name="case-collection"),
    path("case/", views.case_collection, name="case-collection-slash"),
]
