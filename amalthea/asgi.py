"""ASGI entrypoint for Amalthea.

MCP Streamable HTTP is mounted at ``/mcp`` before Django.  The Channels
WebSocket branch stays in the same router.  The MCP lifecycle app starts its
session manager lazily (Daphne sends no ASGI lifespan) and still handles
lifespan when the server provides it.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "amalthea.settings.dev")

from django.core.asgi import get_asgi_application

# Initialise Django before importing the MCP app: its auth/model layer imports
# ``McpCredential`` at construction time, which requires the app registry to be
# ready.  Tests already run under pytest-django, so this only surfaced when
# Daphne loaded the real ASGI entrypoint.
get_asgi_application()

from mcp_server.asgi import build_application  # noqa: E402

application = build_application()
