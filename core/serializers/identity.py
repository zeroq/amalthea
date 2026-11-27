"""Identity wire representations."""

from __future__ import annotations

from typing import Any

from identity.models import Organisation, User

from ._common import _iso


def user_json(user: User) -> dict[str, Any]:
    """`OutputUser` — what `POST /api/v1/login` returns.

    `hasKey`/`hasPassword` are computed rather than hardwired: reporting `hasKey: false` for an
    account that does hold a key would be a lie on the wire, and the recorded schema describes
    both as "whether the account has …", i.e. a question, not a constant. `hasMFA`/`locked` are
    constants only because no model column answers them yet (plan §4: no migrations).
    """
    return {
        "_id": str(user.id),
        "_type": "user",
        "login": user.login,
        "name": user.get_full_name() or user.username,
        "org": user.org.name,
        "hasKey": user.api_keys.exists(),
        "hasPassword": user.has_usable_password(),
        "hasMFA": False,
        "locked": False,
        "_createdBy": None,
        "_createdAt": _iso(user.date_joined),
    }


def organisation_json(org: Organisation) -> dict[str, Any]:
    """`OutputOrganisation` for the caller's own organisation.

    Now that `Organisation` extends `TimeStampedModel`, the audit pair is populated
    from the model's `created_at`/`updated_at` (previously `null` — see deviation
    **P1-2**). `taskRule`/`observableRule`/`locked` have no column and are emitted
    as the schema-shaped constants the stored tenant actually implies.
    """
    return {
        "_id": str(org.id),
        "id": str(org.id),
        "_type": "Organisation",
        "name": org.name,
        "description": org.description,
        "taskRule": "",
        "observableRule": "",
        "locked": False,
        "_createdBy": None,
        "_createdAt": _iso(org.created_at),
        "_updatedBy": None,
        "_updatedAt": _iso(org.updated_at),
        "extraData": {},
    }


def organisation_admin_json(
    org: Organisation, counts: dict[str, int] | None = None
) -> dict[str, Any]:
    """`OutputOrganisation` extended with per-org counts for the superAdmin directory.

    This is an additive serializer — callers that do not need counts can continue
    using `organisation_json`. When `counts` is provided it is inlined under the
    `counts` key; otherwise the key is omitted to keep the payload stable for
    existing consumers.
    """
    payload = organisation_json(org)
    if counts is not None:
        payload["counts"] = counts
    return payload


def user_admin_json(user: User) -> dict[str, Any]:
    """`OutputUser` for the superAdmin user directory/mutation responses.

    Includes all fields needed for the Settings → Users table: `login`, `name`,
    `email`, `role`, `org` (name), `orgId`, `isActive`, `lastLogin`, plus the
    standard `_id`, `_type`, `_createdAt` (from `date_joined`). `login` is derived
    from `username` on save and never accepted from the client.
    """
    return {
        "_id": str(user.id),
        "_type": "user",
        "login": user.login,
        "name": user.get_full_name() or user.username,
        "email": user.email,
        "role": user.role,
        "org": user.org.name,
        "orgId": str(user.org_id),
        "isActive": user.is_active,
        "_createdAt": _iso(user.date_joined),
        "lastLogin": _iso(user.last_login),
    }
