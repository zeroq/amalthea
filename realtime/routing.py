from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/case/(?P<case_id>[^/]+)/$", consumers.CaseConsumer.as_asgi()),
]
