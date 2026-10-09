"""`/api/v1/alert/...` routes (T1 subset).

Registered **before** `compat.urls`, whose `re_path(r"^.*$")` catch-all would otherwise swallow
every path under `/api/v1/` and answer 404.

Method dispatch lives inside the views (`alert_detail` serves GET/PATCH/PUT) rather than in separate
URL entries: Django resolves by path, first match wins, so two entries on one pattern would leave
the second permanently unreachable.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("alert", views.alert_list, name="alert-list"),
    path("alert/", views.alert_list, name="alert-list-slash"),
    path("alert/<str:alert_id>/raw", views.alert_raw, name="alert-raw"),
    path("alert/<str:alert_id>/import", views.alert_import, name="alert-import"),
    path(
        "alert/<str:alert_id>/observable", views.alert_observable_add, name="alert-observable-add"
    ),
    path("alert/<str:alert_id>/merge/<str:case_id>", views.alert_merge, name="alert-merge"),
    path("alert/<str:alert_id>/import/<str:case_id>", views.alert_merge, name="alert-import-into"),
    # T2 tag link, above the bare detail route so `<str:alert_id>` cannot swallow `tag`.
    path("alert/<str:alert_id>/tag", views.alert_tag_link, name="alert-tag-link"),
    path("alert/<str:alert_id>/tag/", views.alert_tag_link, name="alert-tag-link-slash"),
    # T2 P2 — alert comments (`GET|POST`), above the bare detail route.
    path("alert/<str:alert_id>/comment", views.alert_comment_list, name="alert-comment-list"),
    path(
        "alert/<str:alert_id>/comment/", views.alert_comment_list, name="alert-comment-list-slash"
    ),
    # T2 P4 — bulk patch. `_bulk` is a single segment, so it must precede the bare detail route
    # which would otherwise treat the literal `_bulk` as an alert id.
    path("alert/_bulk", views.alert_bulk_update, name="alert-bulk-update"),
    path("alert/_bulk/", views.alert_bulk_update, name="alert-bulk-update-slash"),
    path("alert/<str:alert_id>", views.alert_detail, name="alert-detail"),
    # T2 alert-status vocabulary. `alertStatus` is disjoint from `alert/<id>` (single segment vs
    # two), so ordering here is convention rather than load-bearing; all land before `compat.urls`.
    path("alertStatus", views.alert_status_collection, name="alert-status-collection"),
    path("alertStatus/", views.alert_status_collection, name="alert-status-collection-slash"),
    path("alertStatus/<str:status_id>", views.alert_status_detail, name="alert-status-detail"),
    path(
        "alertStatus/<str:status_id>/",
        views.alert_status_detail,
        name="alert-status-detail-slash",
    ),
]
