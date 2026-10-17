"""Audit logging helpers (T3-d / P3 — Read enforcement).

Thin wrappers around `identity.models.AuditLog` so call sites stay one line
and the logic is easy to test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpRequest

if TYPE_CHECKING:
    from identity.models import IngestionToken, Organisation, User
    from ingest.models import IngestionSource


def _get_client_ip(request: HttpRequest) -> str | None:
    """Extract client IP, respecting the same trust boundary as the rest of the app.

    We do NOT blindly trust X-Forwarded-For. In production the app should run
    behind a trusted proxy that sets `REMOTE_ADDR` correctly, or a middleware
    that validates XFF against a known proxy CIDR. Here we just read
    `REMOTE_ADDR` which is the Django standard for the direct client.
    """
    return request.META.get("REMOTE_ADDR")


def _is_superadmin(user: User | None) -> bool:
    """True iff the caller is an authenticated superAdmin."""
    return (
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "role", "") == "superAdmin"
    )


def record_cross_org_read(
    request: HttpRequest,
    *,
    actor: User,
    actor_org: Organisation,
    target_org: Organisation,
    target_type: str,
    target_id: str,
    action: str = "cross_org_read",
) -> None:
    """
    Record a cross-org read by a superAdmin.

    Args:
        request: The HTTP request (for IP extraction).
        actor: The superAdmin user performing the read.
        actor_org: The superAdmin's organisation.
        target_org: The organisation that owns the resource.
        target_type: The type of resource (e.g. "Case", "Alert", "Observable", "Comment").
        target_id: The UUID of the resource.
        action: Action name (default "cross_org_read").

    Preconditions (asserted):
        - actor is a superAdmin
        - actor_org != target_org
    """
    from identity.models import AuditLog

    # Defensive: only log for genuine cross-org superAdmin reads
    if not _is_superadmin(actor):
        return
    if actor_org_id := getattr(actor_org, "id", None):
        if target_org_id := getattr(target_org, "id", None):
            if actor_org_id == target_org_id:
                return  # own-org read — no audit
        else:
            return  # target has no org — nothing to log
    else:
        return  # actor has no org — nothing to log

    ip = _get_client_ip(request)
    AuditLog.objects.create(
        actor=actor,
        actor_org=actor_org,
        target_org=target_org,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        ip=ip,
    )


def maybe_record_cross_org_read(
    request: HttpRequest,
    *,
    actor: User,
    target_obj: object,
    target_type: str,
    target_id: str,
    target_org_field: str = "owner_org",
) -> None:
    """
    Convenience wrapper that infers `target_org` from the resolved object.

    Use when you have the resolved model instance and want to log only if
    the caller is a superAdmin and the object belongs to a different org.

    Args:
        request: The HTTP request.
        actor: The requesting user.
        target_obj: The resolved model instance (must have `target_org_field`).
        target_type: Resource type string.
        target_id: Resource UUID.
        target_org_field: Field name on `target_obj` pointing to its Organisation.
    """
    if not _is_superadmin(actor):
        return

    actor_org = getattr(actor, "org", None)
    if actor_org is None:
        return

    target_org = getattr(target_obj, target_org_field, None)
    if target_org is None:
        return

    if actor_org.id == target_org.id:
        return

    record_cross_org_read(
        request,
        actor=actor,
        actor_org=actor_org,
        target_org=target_org,
        target_type=target_type,
        target_id=target_id,
    )


def record_organisation_change(
    request: HttpRequest,
    *,
    actor: User,
    action: str,
    target_org: Organisation | None,
    target_id: str,
) -> None:
    """
    Record an organisation create/update/delete by a superAdmin.

    Args:
        request: The HTTP request (for IP extraction).
        actor: The superAdmin user performing the action.
        action: One of "organisation.created", "organisation.updated", "organisation.deleted".
        target_org: The organisation being acted on (for `target_org` FK). For delete, this is None
            because the org is already deleted and the FK is SET_NULL.
        target_id: The UUID of the organisation (as string, captured before delete).
    """
    from identity.models import AuditLog

    ip = _get_client_ip(request)
    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=target_org,
        action=action,
        target_type="Organisation",
        target_id=str(target_id),
        ip=ip,
    )


def record_user_change(
    request: HttpRequest,
    *,
    actor: User,
    action: str,
    target_id: str,
    target_org: Organisation | None,
) -> None:
    """
    Record a user create/update/delete by a superAdmin.

    Args:
        request: The HTTP request (for IP extraction).
        actor: The superAdmin user performing the action.
        action: One of "user.created", "user.updated", "user.deleted".
        target_id: The UUID of the user (as string, captured before delete).
        target_org: The user's organisation at the time of the action (for `target_org` FK).
    """
    from identity.models import AuditLog

    ip = _get_client_ip(request)
    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=target_org,
        action=action,
        target_type="User",
        target_id=str(target_id),
        ip=ip,
    )


def record_ingestion_token_change(
    request: HttpRequest,
    *,
    actor: User,
    action: str,
    token: IngestionToken,
) -> None:
    """
    Record an ingestion token lifecycle change.

    Args:
        request: The HTTP request (for IP extraction).
        actor: The user performing the action (admin or superAdmin).
        action: One of "ingestion_token.created", "ingestion_token.revoked",
            "ingestion_token.deactivated", "ingestion_token.activated",
            "ingestion_token.deleted".
        token: The IngestionToken instance. For a deletion pass the instance **before**
            `delete()` — Django clears the pk afterwards, so `token.id` would be lost.

    A deletion is recorded *before* the row goes, in the same transaction, so the audit
    trail keeps the token's id and owning org after the credential itself is gone.
    """
    from identity.models import AuditLog

    ip = _get_client_ip(request)
    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=token.organisation,
        action=action,
        target_type="IngestionToken",
        target_id=str(token.id),
        ip=ip,
    )


def record_ingestion_source_created(
    request: HttpRequest,
    *,
    actor: User,
    source: IngestionSource,
) -> None:
    """
    Record the on-demand creation of an `IngestionSource` from Settings → Sources.

    Since ADR-009 the source is org-owned, so `target_org` is the org that now owns it: the tenant
    column is what makes "who configured which tenant's feed, and when" answerable after the fact.
    """
    from identity.models import AuditLog

    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=source.organisation,
        action="ingestion_source.created",
        target_type="IngestionSource",
        target_id=str(source.id),
        ip=_get_client_ip(request),
    )


def record_ingestion_source_deleted(
    request: HttpRequest,
    *,
    actor: User,
    source: IngestionSource,
) -> None:
    """Record the deletion of a feed whose last token was just deleted (§10c, ADR-009).

    A feed exists to carry credentials, so deleting its final token leaves the row with nothing to
    do and it goes too (`ui.views.settings_token_delete`). Written **before** `source.delete()` in the
    same transaction — Django clears the pk afterwards, so `target_id` and `target_org` on this row
    are all that outlives the feed. It is the other end of `ingestion_source.created`.

    No `details` payload: the mapping rules are gone, so recording them (or their dot-paths) would make
    the audit row a stale copy of a deleted config. The row answers "which feed, which tenant, by whom,
    when" — which is what an operator asks after a feed has quietly stopped existing.
    """
    from identity.models import AuditLog

    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=source.organisation,
        action="ingestion_source.deleted",
        target_type="IngestionSource",
        target_id=str(source.id),
        ip=_get_client_ip(request),
    )


def record_ingestion_source_updated(
    request: HttpRequest,
    *,
    actor: User,
    source: IngestionSource,
    changed_keys: list[str],
) -> None:
    """Record a mapping edit on one feed (PLAN-2026-10-16 §8 P3, AC13).

    `changed_keys` is the *point* of the row: "somebody edited crowdstrike-edr" is not actionable, but
    "titles stopped being read from `$.display_name`" is — the question an operator asks when alert
    titles change under them. Dot-paths into the config (`fields.title.paths`, `value_maps`), never
    values: an audit row outlives the config and must not become a copy of it.
    """
    from identity.models import AuditLog

    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=source.organisation,
        action="ingestion_source.updated",
        target_type="IngestionSource",
        target_id=str(source.id),
        details={"changed": list(changed_keys)},
        ip=_get_client_ip(request),
    )


def record_ingestion_source_mapping_applied(
    request: HttpRequest,
    *,
    actor: User,
    source: IngestionSource,
    changed_keys: list[str],
    candidates: int,
    updated: int,
    unchanged: int,
    refreshed: int,
) -> None:
    """Record one re-apply of a feed's mapping to its existing alerts (PLAN-2026-10-16 §8 P7, AC22).

    A **bulk write** needs its own row, even when it rides along with a mapping edit: "the rules changed"
    and "37 alerts were rewritten" are different facts with different blast radii, and the second one is
    the one an operator asks about when a severity moves under them months later.

    `changed_keys` is the config half (dot-paths into the config, never values — MAP-7); the counts are
    the alert half. No alert ids: a rewrite of 5000 rows is an operation, not 5000 operations, and the
    alerts themselves carry the outcome (`ingestion_warnings`).

    `refreshed` is the sub-count of `unchanged` rows that were still written to — values kept, `mapping`
    record brought up to date (P7.1, MAP-12). It is recorded because "0 updated, 19 unchanged" alone
    reads as "nothing happened", and 19 rows really moved.
    """
    from identity.models import AuditLog

    AuditLog.objects.create(
        actor=actor,
        actor_org=getattr(actor, "org", None),
        target_org=source.organisation,
        action="ingestion_source.mapping_applied",
        target_type="IngestionSource",
        target_id=str(source.id),
        details={
            "changed": list(changed_keys),
            "alerts": {
                "candidates": candidates,
                "updated": updated,
                "unchanged": unchanged,
                "refreshed": refreshed,
            },
        },
        ip=_get_client_ip(request),
    )
