from django.contrib import admin
from django.urls import include, path

from mcp_server.views import oauth_protected_resource_metadata
from realtime.routing import http_fallback_urlpatterns

# Error handlers are referenced by dotted path, not imported. `ui` is deliberately outside the
# mypy pinned scope (pyproject `[tool.mypy].files`), so importing it here would drag the whole
# untyped UI module into the type gate. Django resolves string handlers via `get_callable`.
handler404 = "ui.views.handler404"
handler500 = "ui.views.handler500"
handler403 = "ui.views.handler403"

urlpatterns = [
    path("admin/", admin.site.urls),
    # The UI is mounted last-but-one so `compat.urls` stays the final catch-all (BRIEF §0.4 note on
    # compat being the legacy prefix): anything unmatched by a real route must fall through to it.
    path("", include("ui.urls")),
    path("", include("core.urls")),
    path("api/v1/", include("ingest.urls")),
    # `identity.urls` and `observables.urls` are mounted **before** `cases.urls`: the latter
    # registers `observable/<str:observable_id>`, which is a single-segment match that would
    # otherwise resolve `observable/type` to an observable whose id is the word "type".
    path("api/v1/", include("identity.urls")),
    path("api/v1/", include("observables.urls")),
    path("api/v1/", include("alerts.urls")),
    path("api/v1/", include("cases.urls")),
    path("api/v1/", include("automation.urls")),
    path("api/v1/", include("query.urls")),
    path("api/v1/", include("compat.urls")),
    # MCP endpoints (Phase 3: OAuth 2.1 resource server)
    # The metadata endpoint must be registered before the catch-all below.
    path(
        ".well-known/oauth-protected-resource/mcp",
        oauth_protected_resource_metadata,
        name="mcp_oauth_protected_resource_metadata",
    ),
    # Realtime HTTP fallback (friendly error for WebSocket endpoints accessed via HTTP)
    path("", include(http_fallback_urlpatterns)),
]
