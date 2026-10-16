from django.urls import path

from . import views

# The receiver is a single URL: the credential identifies both the feed (its `IngestionSource`, hence
# the mapping rules) and the tenant, so no source slug appears in the path. The explicit no-slash twin
# exists because `APPEND_SLASH` would answer a slugless POST with a 301 and drop the body.
urlpatterns = [
    path("alerts/webhook/", views.webhook_alerts, name="alerts-webhook"),
    path("alerts/webhook", views.webhook_alerts, name="alerts-webhook-no-slash"),
]
