from django.urls import path

from . import views

urlpatterns = [
    path("alerts/webhook/<slug:source_id>/", views.webhook_alerts, name="alerts-webhook"),
    path("alerts/webhook/<slug:source_id>", views.webhook_alerts, name="alerts-webhook-no-slash"),
]
