"""`/api/v1/observable/type...` and `/api/v1/suggestions...` routes (T2 observable vocabulary + P5 suggestions).

**Ordering is load-bearing, twice over.**

* Within this module, the collection `observable/type` has two segments and the detail
  `observable/type/<type_id>` has three, so they cannot shadow each other; both are still
  registered collection-first for readability.
* Across modules, this include must be mounted in `amalthea/urls.py` **before** `cases.urls`:
  that module registers `observable/<str:observable_id>`, which would otherwise swallow
  `observable/type` and resolve it to an observable whose id is the word "type".

Both spellings of every route are registered: `APPEND_SLASH` turns a POST into a GET and drops
its body, so the slashless `POST /observable/type` thehive4py would send must resolve directly.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("observable/type", views.observable_type_collection, name="observable-type-collection"),
    path(
        "observable/type/",
        views.observable_type_collection,
        name="observable-type-collection-slash",
    ),
    path(
        "observable/type/<str:type_id>",
        views.observable_type_detail,
        name="observable-type-detail",
    ),
    path(
        "observable/type/<str:type_id>/",
        views.observable_type_detail,
        name="observable-type-detail-slash",
    ),
    # P5: ObservableSuggestion endpoints
    path(
        "case/<str:case_id>/suggestions",
        views.case_suggestions_list,
        name="case-suggestions-list",
    ),
    path(
        "case/<str:case_id>/suggestions/",
        views.case_suggestions_list,
        name="case-suggestions-list-slash",
    ),
    path(
        "alert/<str:alert_id>/suggestions",
        views.alert_suggestions_list,
        name="alert-suggestions-list",
    ),
    path(
        "alert/<str:alert_id>/suggestions/",
        views.alert_suggestions_list,
        name="alert-suggestions-list-slash",
    ),
    path(
        "suggestions/<str:suggestion_id>/accept",
        views.suggestion_accept,
        name="suggestion-accept",
    ),
    path(
        "suggestions/<str:suggestion_id>/accept/",
        views.suggestion_accept,
        name="suggestion-accept-slash",
    ),
    path(
        "suggestions/<str:suggestion_id>/reject",
        views.suggestion_reject,
        name="suggestion-reject",
    ),
    path(
        "suggestions/<str:suggestion_id>/reject/",
        views.suggestion_reject,
        name="suggestion-reject-slash",
    ),
    path(
        "suggestions/<str:suggestion_id>/ignore",
        views.suggestion_ignore,
        name="suggestion-ignore",
    ),
    path(
        "suggestions/<str:suggestion_id>/ignore/",
        views.suggestion_ignore,
        name="suggestion-ignore-slash",
    ),
    path(
        "suggestions/bulk-accept",
        views.suggestions_bulk_accept,
        name="suggestions-bulk-accept",
    ),
    path(
        "suggestions/bulk-accept/",
        views.suggestions_bulk_accept,
        name="suggestions-bulk-accept-slash",
    ),
    path(
        "suggestions/bulk-reject",
        views.suggestions_bulk_reject,
        name="suggestions-bulk-reject",
    ),
    path(
        "suggestions/bulk-reject/",
        views.suggestions_bulk_reject,
        name="suggestions-bulk-reject-slash",
    ),
    path(
        "suggestions/bulk-ignore",
        views.suggestions_bulk_ignore,
        name="suggestions-bulk-ignore",
    ),
    path(
        "suggestions/bulk-ignore/",
        views.suggestions_bulk_ignore,
        name="suggestions-bulk-ignore-slash",
    ),
]
