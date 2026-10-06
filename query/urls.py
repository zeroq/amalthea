"""`/api/v1/query` routes.

Twin entries per `alerts/urls.py`: Django's APPEND_SLASH redirect turns a POST into a GET when
only one spelling exists, which silently breaks the query endpoint for clients that send the
trailing slash. Registered before `compat.urls` — whose `re_path(r"^.*$")` catch-all would
otherwise swallow the path (same ordering rule as every other `/api/v1/` mount).
"""

from django.urls import path

from . import views

urlpatterns = [
    path("query", views.query_view, name="query-api"),
    path("query/", views.query_view, name="query-api-slash"),
]
