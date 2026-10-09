from django.urls import path, re_path

from . import consumers, views

websocket_urlpatterns = [
    re_path(r"ws/case/(?P<case_id>[^/]+)/$", consumers.CaseConsumer.as_asgi()),
]

# HTTP fallback for WebSocket endpoints - returns helpful error instead of 404
http_fallback_urlpatterns = [
    path("ws/case/<str:case_id>/", views.websocket_fallback, name="ws-case-fallback"),
]
