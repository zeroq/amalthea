"""`/api/v1/login` and `/api/v1/logout`, ahead of the catch-all.

**Ordering is load-bearing.** `re_path(r"^.*$", ...)` matches every remaining path under
`/api/v1/`, so these two must be registered *above* it — the same rule `cases/urls.py`
applies to its sub-resources, for the same reason: first match wins.
"""

from django.urls import path, re_path

from . import views

urlpatterns = [
    path("login", views.api_login, name="api-login"),
    path("login/", views.api_login, name="api-login-slash"),
    path("logout", views.api_logout, name="api-logout"),
    path("logout/", views.api_logout, name="api-logout-slash"),
    # Describe, above the catch-all for the same reason as login/logout. `_all` is registered
    # before `<model>` so the literal is not read as a model named "_all" (the recorded path is
    # `/describe/_all`; there is no bare `/describe`).
    path("describe/_all", views.describe_all, name="describe-all"),
    path("describe/_all/", views.describe_all, name="describe-all-slash"),
    path("describe/<str:model>", views.describe_model, name="describe-model"),
    path("describe/<str:model>/", views.describe_model, name="describe-model-slash"),
    re_path(r"^.*$", views.not_found, name="compat-catchall"),
]
