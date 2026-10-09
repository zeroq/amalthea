"""`/api/v1/observable/type...` routes (T2 observable vocabulary).

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
]
