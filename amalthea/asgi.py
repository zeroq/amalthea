"""
ASGI config for Amalthea.
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "amalthea.settings.dev")

django_asgi_app = get_asgi_application()

from realtime import routing as realtime_routing  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(realtime_routing.websocket_urlpatterns)),
    }
)
