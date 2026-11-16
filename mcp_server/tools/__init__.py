"""MCP Phase 1 read tools."""

from __future__ import annotations

from typing import Any

from mcp_server.tools import alerts, cases, observables, playbooks, search, tasks

MODULES = (alerts, cases, observables, tasks, playbooks, search)


def register_all(server: Any) -> None:
    for module in MODULES:
        module.register(server)


__all__ = [
    "MODULES",
    "alerts",
    "cases",
    "observables",
    "playbooks",
    "register_all",
    "search",
    "tasks",
]
