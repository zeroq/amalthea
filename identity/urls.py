"""`/api/v1/user/...` and `/api/v1/organisation/...` routes (T2 identity).

**Ordering is load-bearing.** `user/<str:user_id>` matches any single segment, so a bare
`user/<str:user_id>` registered first would swallow `user/current` as the user whose login is
the word "current". The literal is registered first for that reason.

Every route is registered with and without the trailing slash: `APPEND_SLASH` would 301 a PATCH
and drop its body, so the slashless spelling thehive4py sends must resolve directly.

These prefixes (`user/`, `organisation/`) are disjoint from `cases/urls.py`'s `case/`,
`observable/`, `task/`, so the `amalthea/urls.py` mounts are order-independent with respect to
that module; they are nonetheless placed above `compat.urls`, whose catch-all would answer 404.
"""

from django.urls import path

from . import views

urlpatterns = [
    # `current` before `<user_id>`: the literal would otherwise be read as a login.
    path("user/current", views.user_current, name="user-current"),
    path("user/current/", views.user_current, name="user-current-slash"),
    path("user/<str:user_id>", views.user_detail, name="user-detail"),
    path("user/<str:user_id>/", views.user_detail, name="user-detail-slash"),
    path("organisation", views.organisation_collection, name="organisation-collection"),
    path("organisation/", views.organisation_collection, name="organisation-collection-slash"),
    path("organisation/<str:org_id>", views.organisation_detail, name="organisation-detail"),
    path(
        "organisation/<str:org_id>/",
        views.organisation_detail,
        name="organisation-detail-slash",
    ),
]
