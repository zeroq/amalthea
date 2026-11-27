"""Automation ledger and playbook views.

Compatibility shim for the historical ``ui.views.automation`` module. Parser helpers live in
`playbook_config.py`; request handlers live in `playbooks.py`.
"""

from .playbook_config import _get_script_source, _parse_playbook_config
from .playbooks import (
    _resolve_playbook_ui,
    automation_list,
    logger,
    playbook_create,
    playbook_delete,
    playbook_detail,
    playbook_edit,
    playbook_list,
    playbook_run,
    playbook_scripts,
)

__all__ = [
    "_get_script_source",
    "_parse_playbook_config",
    "_resolve_playbook_ui",
    "automation_list",
    "logger",
    "playbook_create",
    "playbook_delete",
    "playbook_detail",
    "playbook_edit",
    "playbook_list",
    "playbook_run",
    "playbook_scripts",
]
