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
    path("alert/<str:alert_id>/merge/<str:case_id>", views.alert_merge, name="alert-merge"),
    path("alert/<str:alert_id>/import/<str:case_id>", views.alert_merge, name="alert-import-into"),
    path("alert/<str:alert_id>", views.alert_detail, name="alert-detail"),
]
