"""Shared business logic for organisation/user management.

This module extracts the deletion guard and other mutation logic so it can be
reused by both the API (identity/views.py) and the UI (ui/views.py) without
duplicating the guarded-delete behaviour.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError

from identity.audit import record_organisation_change, record_user_change
from identity.models import Organisation, User

if TYPE_CHECKING:
    pass


def as_uuid(value: object) -> UUID | None:
    """Parse `value` as a UUID without letting a malformed one reach the ORM.

    `filter(pk="not-a-uuid")` raises `ValidationError` out of the query compiler —
    a 500 where the contract promises a 404. Shared by the API and the UI.
    """
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def resolve_organisation(identifier: object) -> Organisation | None:
    """Resolve `identifier` (UUID **or** name) to an Organisation, or `None`."""
    if identifier is None:
        return None
    pk = as_uuid(identifier)
    if pk is not None:
        return Organisation.objects.filter(pk=pk).first()
    return Organisation.objects.filter(name=str(identifier)).first()


def resolve_user(identifier: object) -> User | None:
    """Resolve `identifier` (UUID **or** login) to a User, or `None`."""
    if identifier is None:
        return None
    pk = as_uuid(identifier)
    if pk is not None:
        return User.objects.select_related("org").filter(pk=pk).first()
    return User.objects.select_related("org").filter(login=str(identifier)).first()


def parse_bool(value: object) -> bool | None:
    """Coerce a JSON/form boolean, rejecting truthy-but-wrong values (F8).

    Only real booleans and the explicit strings ``"true"``/``"false"`` are accepted;
    anything else returns ``None`` so the caller can 400 instead of silently treating
    ``"false"`` as ``True`` (``bool("false") is True``).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def lock_active_superadmins() -> int:
    """Lock every active superAdmin row and return how many exist.

    Must be called inside ``transaction.atomic()``: the row locks are held until the
    transaction commits, so a concurrent demote/deactivate/delete blocks and re-reads
    the count *after* the other transaction lands (F5 — closes the check-then-act race
    that could otherwise leave zero administrators). ``select_for_update`` is a no-op
    on SQLite (dev/test); it is effective on the PostgreSQL deployment target.
    """
    return len(
        list(
            User.objects.select_for_update()
            .filter(role="superAdmin", is_active=True)
            .values_list("pk", flat=True)
        )
    )


def locks_active_superadmins[F: Callable[..., Any]](fn: F) -> F:
    """Run `fn` inside a transaction that holds the active-superAdmin locks (F5).

    For service functions (first positional argument is the target, not a request).
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with transaction.atomic():
            lock_active_superadmins()
            return fn(*args, **kwargs)

    return cast(F, wrapper)


def serializes_superadmin_changes[F: Callable[..., Any]](view: F) -> F:
    """Lock active superAdmins for mutating HTTP requests only (F5).

    Read-only requests must not take the row locks; a settings *page* load is a GET.
    """

    @wraps(view)
    def wrapper(request: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(request, "method", "GET") in {"POST", "PUT", "PATCH", "DELETE"}:
            with transaction.atomic():
                lock_active_superadmins()
                return view(request, *args, **kwargs)
        return view(request, *args, **kwargs)

    return cast(F, wrapper)


class OrganisationDeleteBlockedError(Exception):
    """Raised when an organisation cannot be deleted due to blockers.

    ``reason`` distinguishes the cases so callers can render a precise message:
    ``members_required`` | ``invalid_target`` | ``self_lockout`` |
    ``last_superadmin`` | ``race`` (None means "owns tenant data").
    """

    def __init__(
        self,
        blockers: dict[str, object],
        members: int | None = None,
        *,
        reason: str | None = None,
    ):
        self.blockers = blockers
        self.members = members
        self.reason = reason
        super().__init__(f"Organisation delete blocked: {blockers}, members={members}")


def organisation_blockers(org: Organisation) -> dict[str, int]:
    """Return counts of tenant data that PROTECT the organisation from deletion."""
    return {
        "cases": org.cases.count(),
        "alerts": org.alerts.count(),
        "observables": org.observables.count(),
        "ingestionTokens": org.ingestion_tokens.count(),
    }


@locks_active_superadmins
def delete_organisation(
    org: Organisation,
    *,
    members: str | None,
    target: Organisation | None,
    actor: User,
    request: Any,
) -> None:
    """
    Delete an organisation with member handling, inside a single transaction.

    This implements the exact behaviour of the API's DELETE endpoint:
    - Pre-flight: if the org owns any tenant data (cases/alerts/observables/tokens)
      → raise OrganisationDeleteBlockedError (409-equivalent). No member option overrides.
    - If members exist: require `members` ∈ {"reassign", "delete"}.
      - "reassign" requires a valid different `target` org; move all members there.
      - "delete" removes the member accounts (FKs are SET_NULL, tenant data survives).
    - Delete the org and write audit logs (org.deleted + user.deleted for members).

    Args:
        org: The organisation to delete.
        members: "reassign" | "delete" | None. Required if org has members.
        target: Target organisation for reassignment (required when members="reassign").
        actor: The superAdmin performing the action.
        request: Optional request for audit IP extraction.

    Raises:
        OrganisationDeleteBlockedError: If tenant data exists or members handling is invalid.
    """
    # Pre-flight: check for tenant data (PROTECT FKs)
    blockers = organisation_blockers(org)
    if any(blockers.values()):
        raise OrganisationDeleteBlockedError(dict(blockers))

    members_qs = org.users.all()
    members_exist = members_qs.exists()
    members_count = members_qs.count()

    # Validate member handling up front so we never mutate member rows and then
    # fail. A "reassign" must name a real, different target organisation —
    # otherwise ``members_qs.update(org=None)`` would silently orphan members.
    reassign_target: Organisation | None = None
    if members_exist:
        if members == "reassign":
            if target is None or target.pk == org.pk:
                raise OrganisationDeleteBlockedError(
                    dict(blockers), members=members_count, reason="invalid_target"
                )
            reassign_target = target
        elif members == "delete":
            # Lock-out protection (plan R1): deleting an organisation must never
            # remove the acting superAdmin or the last active superAdmin as a side
            # effect — that would brick tenant management.
            if members_qs.filter(pk=actor.pk).exists():
                raise OrganisationDeleteBlockedError(
                    dict(blockers), members=members_count, reason="self_lockout"
                )
            active_in_org = members_qs.filter(role="superAdmin", is_active=True).count()
            if (
                active_in_org
                and active_in_org == User.objects.filter(role="superAdmin", is_active=True).count()
            ):
                raise OrganisationDeleteBlockedError(
                    dict(blockers), members=members_count, reason="last_superadmin"
                )
        else:
            raise OrganisationDeleteBlockedError(
                dict(blockers), members=members_count, reason="members_required"
            )

    # Capture target_id before deletion for audit
    target_id = str(org.id)

    try:
        # Member mutations, the audit rows and the org deletion share one
        # transaction: a raced ProtectedError rolls back everything, so members
        # are never moved/removed and no audit row survives a failed delete.
        # Audits are written *before* the referenced rows disappear so the
        # nullable actor/actor_org/target_org FKs never dangle.
        with transaction.atomic():
            if members_exist:
                if reassign_target is not None:
                    members_qs.update(org=reassign_target)
                else:
                    # members == "delete": audit each removal, then remove.
                    for member in members_qs:
                        record_user_change(
                            request,
                            actor=actor,
                            action="user.deleted",
                            target_id=str(member.id),
                            target_org=member.org,
                        )
                    members_qs.delete()
            record_organisation_change(
                request,
                actor=actor,
                action="organisation.deleted",
                target_org=None,
                target_id=target_id,
            )
            org.delete()
    except ProtectedError as exc:
        # Race condition: some tenant data was created after pre-flight. The
        # transaction rolls back, so no member was moved or removed.
        raise OrganisationDeleteBlockedError(
            {"race": True, "detail": str(exc)}, reason="race"
        ) from exc


def create_user(
    *,
    username: str,
    email: str,
    password: str,
    org: Organisation,
    role: str,
    is_active: bool = True,
    actor: User,
    request: Any,
) -> User:
    """
    Create a user with validation, password hashing, and audit.

    Mirrors the API's POST /user behaviour.
    """
    from core.enums import USER_ROLE_VALUES

    errors: dict[str, list[str]] = {}
    if not username:
        errors.setdefault("username", []).append("required")
    if not email:
        errors.setdefault("email", []).append("required")
    if not password:
        errors.setdefault("password", []).append("required")
    if role not in USER_ROLE_VALUES:
        errors.setdefault("role", []).append(f"must be one of {', '.join(USER_ROLE_VALUES)}")
    if not org:
        errors.setdefault("org", []).append("required")

    if User.objects.filter(username=username).exists():
        errors.setdefault("username", []).append("already exists")
    if User.objects.filter(login=username).exists():
        errors.setdefault("username", []).append("already exists")

    if errors:
        from django.core.exceptions import ValidationError as DjangoValidationError

        raise DjangoValidationError(errors)

    user = User(
        username=username,
        email=email,
        role=role,
        org=org,
        is_active=is_active,
    )
    try:
        validate_password(password, user=user)
    except ValidationError as e:
        raise DjangoValidationError({"password": e.messages}) from e

    user.set_password(password)
    user.save()

    record_user_change(
        request, actor=actor, action="user.created", target_id=str(user.id), target_org=org
    )
    return user


@locks_active_superadmins
def update_user(
    user: User,
    *,
    email: str | None = None,
    role: str | None = None,
    org: Organisation | None = None,
    is_active: bool | None = None,
    password: str | None = None,
    actor: User,
    request: Any,
) -> list[str]:
    """
    Update a user with validation, guards, and audit.

    Returns the list of changed fields. Raises ValidationError on validation failure.
    """
    from core.enums import USER_ROLE_VALUES

    errors: dict[str, list[str]] = {}
    changed: list[str] = []

    if email is not None:
        email = email.strip()
        if user.email != email:
            user.email = email
            changed.append("email")

    if role is not None:
        if role not in USER_ROLE_VALUES:
            errors.setdefault("role", []).append(f"must be one of {', '.join(USER_ROLE_VALUES)}")
        else:
            # Guard: cannot change your own role away from superAdmin
            if user.pk == actor.pk and actor.role == "superAdmin" and role != "superAdmin":
                errors.setdefault("role", []).append("self_lockout")
            # Guard: cannot demote the last active superAdmin
            elif user.role == "superAdmin" and role != "superAdmin":
                active_superadmins = User.objects.filter(role="superAdmin", is_active=True).count()
                if active_superadmins == 1:
                    errors.setdefault("role", []).append("last_superadmin")
            if user.role != role:
                user.role = role
                changed.append("role")

    if org is not None:
        if user.org_id != org.id:
            user.org = org
            changed.append("org")

    if is_active is not None:
        # Guard: cannot deactivate yourself as superAdmin
        if user.pk == actor.pk and actor.role == "superAdmin" and not is_active:
            errors.setdefault("is_active", []).append("self_lockout")
        # Guard: cannot deactivate the last active superAdmin
        elif user.role == "superAdmin" and user.is_active and not is_active:
            active_superadmins = User.objects.filter(role="superAdmin", is_active=True).count()
            if active_superadmins == 1:
                errors.setdefault("is_active", []).append("last_superadmin")
        if user.is_active != is_active:
            user.is_active = is_active
            changed.append("is_active")

    if password is not None and password != "":
        try:
            validate_password(password, user=user)
        except ValidationError as e:
            errors.setdefault("password", []).extend(e.messages)
        else:
            user.set_password(password)
            changed.append("password")

    if errors:
        from django.core.exceptions import ValidationError as DjangoValidationError

        raise DjangoValidationError(errors)

    if not changed:
        return []

    user.save(
        update_fields=[
            f
            for f in ["email", "role", "org", "is_active", "password"]
            if f in changed or f == "password"
        ]
    )

    if changed:
        record_user_change(
            request, actor=actor, action="user.updated", target_id=str(user.id), target_org=user.org
        )
    return changed


@locks_active_superadmins
def delete_user(
    user: User,
    *,
    actor: User,
    request: Any,
) -> None:
    """
    Delete a user with guards and audit.

    Guards: cannot delete self, cannot delete last active superAdmin.
    """
    # Guard: cannot delete yourself
    if user.pk == actor.pk:
        from django.core.exceptions import ValidationError as DjangoValidationError

        raise DjangoValidationError({"user": ["self_delete"]})

    # Guard: cannot delete the last active superAdmin
    if user.role == "superAdmin" and user.is_active:
        active_superadmins = User.objects.filter(role="superAdmin", is_active=True).count()
        if active_superadmins == 1:
            from django.core.exceptions import ValidationError as DjangoValidationError

            raise DjangoValidationError({"user": ["last_superadmin"]})

    target_id = str(user.id)
    target_org = user.org
    user.delete()

    record_user_change(
        request, actor=actor, action="user.deleted", target_id=target_id, target_org=target_org
    )
