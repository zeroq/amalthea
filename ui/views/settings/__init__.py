"""Compatibility wrapper for the split settings views."""

from __future__ import annotations

import logging

from .settings_home import settings_home
from .settings_orgs import (
    _org_blockers_text,
    settings_organisations,
    settings_organisations_delete,
    settings_organisations_edit,
)
from .settings_users import settings_users, settings_users_delete, settings_users_edit

logger = logging.getLogger(__name__)

__all__ = [
    "_org_blockers_text",
    "logger",
    "settings_home",
    "settings_organisations",
    "settings_organisations_delete",
    "settings_organisations_edit",
    "settings_users",
    "settings_users_delete",
    "settings_users_edit",
]
