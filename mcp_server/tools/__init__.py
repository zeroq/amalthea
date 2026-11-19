"""MCP tool registration.

Phase 1 read tools remain available; Phase 2 proposal and summary tools add
write-adjacent capabilities through explicit approval or labelled append-only
timeline events.
"""

from __future__ import annotations

from typing import Any

import mcp_server.tools.proposals as proposals
import mcp_server.tools.summaries as summaries
from mcp_server.tools import alerts, cases, observables, playbooks, search, tasks

MODULES = (
    alerts,
    cases,
    observables,
    tasks,
    playbooks,
    search,
    proposals,
    summaries,
)


def register_all(server: Any) -> None:
    for module in MODULES:
        module.register(server)


__all__ = [
    "MODULES",
    "alerts",
    "cases",
    "observables",
    "playbooks",
    "proposals",
    "register_all",
    "search",
    "summaries",
    "tasks",
]
