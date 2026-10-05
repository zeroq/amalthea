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
    path("case/<str:case_id>/task", views.case_task_list, name="case-task-list"),
    path("case/<str:case_id>/task", views.case_task_create, name="case-task-create"),
    path("case/<str:case_id>/task/", views.case_task_create, name="case-task-create-slash"),
    path("case/<str:case_id>/observable", views.case_observable_list, name="case-observable-list"),
    path("case/<str:case_id>/observable/", views.case_observable_add, name="case-observable-add"),
    path("case/<str:case_id>/timeline", views.case_timeline, name="case-timeline"),
    path("case/<str:case_id>", views.case_detail, name="case-detail"),
    path("case/<str:case_id>/", views.case_detail, name="case-detail-slash"),
    path("observable/<str:observable_id>", views.observable_detail, name="observable-detail"),
    # Collection routes are registered with and without the trailing slash: CommonMiddleware's
    # APPEND_SLASH would redirect a POST and lose its body, so the slashless spellings are explicit.
    path("case", views.case_collection, name="case-collection"),
    path("case/", views.case_collection, name="case-collection-slash"),
]
