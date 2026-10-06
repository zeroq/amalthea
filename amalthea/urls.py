from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # The UI is mounted last-but-one so `compat.urls` stays the final catch-all (BRIEF §0.4 note on
    # compat being the legacy prefix): anything unmatched by a real route must fall through to it.
    path("", include("ui.urls")),
    path("", include("core.urls")),
    path("api/v1/", include("ingest.urls")),
    path("api/v1/", include("alerts.urls")),
    path("api/v1/", include("cases.urls")),
    path("api/v1/", include("query.urls")),
    path("api/v1/", include("compat.urls")),
]
