"""HTTP fallback views for WebSocket endpoints.

When a client makes a regular HTTP request to a WebSocket endpoint (instead of a
WebSocket upgrade), they get a 404 from the HTTP router. This module provides
friendly fallback views that explain what went wrong.
"""

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_GET


@require_GET
def websocket_fallback(request: HttpRequest, case_id: str) -> HttpResponse:
    """Friendly fallback for WebSocket endpoint accessed via HTTP.

    Returns a helpful error message explaining that this endpoint requires
    a WebSocket connection, not a regular HTTP request.
    """
    return HttpResponse(
        "This endpoint requires a WebSocket connection. "
        "Please connect via WebSocket protocol (ws:// or wss://) "
        "instead of HTTP.",
        status=400,
        content_type="text/plain",
    )
