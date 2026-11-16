"""MCP server application configuration."""

from __future__ import annotations

from django.apps import AppConfig


class McpServerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mcp_server"

    def ready(self) -> None:
        # Import signals/signals handlers here if needed; keep ready lightweight.
        pass
