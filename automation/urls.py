"""`/api/v1/playbook/...` routes (plan §6.1, T2.3).

Registered **before** `compat.urls`, whose `re_path(r"^.*$")` catch-all would otherwise swallow
every path under `/api/v1/` and answer 404.

Ordering inside the block: `_meta` and `{idOrName}/run` are two-segment spellings, so they land
above the bare `playbook/<str:playbook_id>` detail route, which would otherwise swallow `_meta`
as a playbook whose name is the word "_meta" — the same "specific first" rule `cases/urls.py`
documented for its own sub-resources.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("playbook/_meta", views.playbook_meta, name="playbook-meta"),
    path("playbook/_meta/", views.playbook_meta, name="playbook-meta-slash"),
    path("playbook/<str:playbook_id>/run", views.playbook_run, name="playbook-run"),
    path("playbook/<str:playbook_id>/run/", views.playbook_run, name="playbook-run-slash"),
    path("playbook", views.playbook_collection, name="playbook-collection"),
    path("playbook/", views.playbook_collection, name="playbook-collection-slash"),
    path("playbook/<str:playbook_id>", views.playbook_detail, name="playbook-detail"),
    path("playbook/<str:playbook_id>/", views.playbook_detail, name="playbook-detail-slash"),
]
