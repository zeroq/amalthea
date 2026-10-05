from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("api/v1/", include("ingest.urls")),
    path("api/v1/", include("alerts.urls")),
    path("api/v1/", include("cases.urls")),
    path("api/v1/", include("compat.urls")),
]
