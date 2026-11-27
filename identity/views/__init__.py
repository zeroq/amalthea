"""T2 identity endpoints: caller, organisation and user management.

Compatibility shim for the historical ``identity.views`` module. Shared helpers live in
`common.py`, organisation endpoints in `organisations.py`, and user endpoints in `users.py`.
"""

from identity.models import DEFAULT_USER_ROLE

from .common import (
    _as_uuid,
    _bad,
    _conflict,
    _not_found,
    _own_org,
    _require_superadmin,
    _resolve_org,
    _resolve_user,
)
from .organisations import (
    _delete_organisation,
    _update_organisation,
    _update_organisation_superadmin,
    organisation_collection,
    organisation_detail,
)
from .users import (
    _delete_user,
    _update_user,
    user_collection,
    user_current,
    user_detail_management,
)

__all__ = [
    "DEFAULT_USER_ROLE",
    "_as_uuid",
    "_bad",
    "_conflict",
    "_delete_organisation",
    "_delete_user",
    "_not_found",
    "_own_org",
    "_require_superadmin",
    "_resolve_org",
    "_resolve_user",
    "_update_organisation",
    "_update_organisation_superadmin",
    "_update_user",
    "organisation_collection",
    "organisation_detail",
    "user_collection",
    "user_current",
    "user_detail_management",
]
