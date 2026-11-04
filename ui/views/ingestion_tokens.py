"""Settings sources page and ingestion token lifecycle views."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from identity.models import IngestionToken, Organisation
from identity.scoping import (
    org_q,
)
from ingest.models import IngestionSource
from ui.pagination import paginate

from .common import (
    _base_context,
    _require_admin_ui,
    not_viewer,
)

logger = logging.getLogger(__name__)


def _settings_sources_context(request: HttpRequest) -> dict:
    """Page context for Sources: the token list plus the create-modal lookups.

    Shared by the GET list and by `settings_token_create`'s show-once re-render, so the modal the
    operator just submitted still finds its datalist (existing feed names) and — for a superAdmin —
    its organisation options when the page comes back.
    """
    is_superadmin = request.user.role == "superAdmin"
    from identity.models import IngestionToken

    qs = (
        IngestionToken.objects.select_related("source", "organisation", "created_by")
        .defer("secret_hash")
        .order_by("-created_at")
    )
    if not is_superadmin:
        qs = qs.filter(organisation_id=request.user.org_id)

    from ingest.models import IngestionSource

    # `source_token_count` is what lets the delete modal say "this is the feed's last token" — the one
    # fact that decides whether deleting the token also reaps the feed (§10b). `source_alert_count` is
    # the blast radius the modal warns about. `distinct=True` on both: two aggregate joins over the
    # same base rows would otherwise cross-multiply.
    qs = qs.annotate(
        source_token_count=Count("source__tokens", distinct=True),
        source_alert_count=Count("source__alerts", distinct=True),
    )

    # Pagination for the tokens list (primary list P)
    page_obj = paginate(request, qs)
    filtered_from = None  # No filters on this list
    tokens = list(page_obj.object_list)

    return {
        "page_obj": page_obj,
        "filtered_from": filtered_from,
        "tokens": tokens,  # Kept for backward compatibility with settings_token_create re-render
        "show_org_column": is_superadmin,
        # ADR-009: only the org's own feeds are nameable here (a superAdmin sees all and picks the
        # org, which is then the org the source is provisioned in).
        "sources": IngestionSource.objects.filter(org_q("organisation", request.user)).order_by(
            "name"
        ),
        "organisations": Organisation.objects.order_by("name") if is_superadmin else None,
        "is_superadmin": is_superadmin,
        # The endpoint is source-less: every feed posts to the same URL and the token does the routing.
        "webhook_path": "/api/v1/alerts/webhook/",
    }


@not_viewer
@login_required
def settings_sources(request: HttpRequest) -> HttpResponse:
    """Token list for ingestion sources (admin + superAdmin)."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    return render(
        request,
        "ui/settings_sources.html",
        _base_context(request, **_settings_sources_context(request)),
    )


@not_viewer
@login_required
@require_POST
def settings_token_create(request: HttpRequest) -> HttpResponse:
    """Mint a new ingestion token (show-once).

    Success is a 200 that re-renders the Sources list with the show-once dialog open (never a
    redirect: the plaintext must not survive in a URL, a session or a cache). Validation failures
    redirect back with a message.
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    is_superadmin = request.user.role == "superAdmin"

    # Resolve organisation first: the purely-request fields are validated before anything is
    # provisioned, so a rejected submission cannot leave a stray global `IngestionSource` behind.
    if is_superadmin:
        org_id = request.POST.get("organisation")
        if not org_id:
            messages.error(request, "Organisation is required.")
            return redirect("ui-settings-sources")
        try:
            organisation = Organisation.objects.get(pk=org_id)
        except Organisation.DoesNotExist:
            messages.error(request, "Organisation not found.")
            return redirect("ui-settings-sources")
    else:
        organisation = request.user.org

    # Validate name
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("ui-settings-sources")
    if len(name) > 200:
        messages.error(request, "Name must be 200 characters or fewer.")
        return redirect("ui-settings-sources")

    # Resolve the source by *name*, within the token's organisation. The webhook URL is source-less,
    # so the source is not a routing key — it is the org-owned mapping profile this token points at
    # (ADR-009, which reverses ADR-008 §4's fleet-global profile). A name is therefore all the
    # operator supplies, and the row is provisioned on demand in **their** org: `slugify` gives it a
    # stable key, so an existing name in the same org reuses that profile, while the same name in
    # another org is that org's own feed with its own rules (AC10). Scoping the lookup by org is also
    # what keeps the pair legal: an unscoped lookup would hand back another tenant's source and the
    # token would then be refused by the ADR-009 invariant.
    from django.db import IntegrityError
    from django.utils.text import slugify

    source_name = (request.POST.get("source_name") or "").strip()
    if not source_name:
        messages.error(request, "Source name is required.")
        return redirect("ui-settings-sources")
    if len(source_name) > 200:
        messages.error(request, "Source name must be 200 characters or fewer.")
        return redirect("ui-settings-sources")

    source_slug = slugify(source_name)[:100]
    if not source_slug:
        messages.error(request, "Source name must contain at least one letter or digit.")
        return redirect("ui-settings-sources")

    try:
        source, source_created = IngestionSource.objects.get_or_create(
            organisation=organisation,
            slug=source_slug,
            defaults={"name": source_name},
        )
    except IntegrityError:
        # Lost a race with a concurrent create — the row exists now, so just bind to it.
        source, source_created = (
            IngestionSource.objects.get(organisation=organisation, slug=source_slug),
            False,
        )

    if source.organisation_id != organisation.id:
        # Unreachable while the lookup above is org-scoped (the ADR-009 invariant would raise first);
        # kept as the explicit refusal so a future refactor fails with a message, not a 500.
        messages.error(
            request,
            f"'{source.slug}' belongs to another organisation, so it cannot be used for this token.",
        )
        return redirect("ui-settings-sources")

    if source_created:
        from identity.audit import record_ingestion_source_created

        record_ingestion_source_created(request, actor=request.user, source=source)

    # Create token with bounded retry on prefix collision
    from django.db import transaction

    from identity.models import IngestionToken

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                token, plaintext = IngestionToken.create_token(
                    source=source,
                    organisation=organisation,
                    name=name,
                    created_by=request.user,
                )
                # Write audit row in the same transaction
                from identity.audit import record_ingestion_token_change

                record_ingestion_token_change(
                    request,
                    actor=request.user,
                    action="ingestion_token.created",
                    token=token,
                )
        except IntegrityError:
            # The only unique constraint on IngestionToken is `prefix` (PK is UUID).
            # A collision here can only mean a prefix collision — retry with a new random prefix.
            if attempt < max_retries - 1:
                continue
            messages.error(request, "Could not generate a unique token. Please try again.")
            return redirect("ui-settings-sources")
        else:
            break

    # Show-once render. The secret is rendered into the `token-created-modal` dialog on the Sources
    # page (a token-created conditional include in `settings_sources.html`) instead of into a bare
    # partial: a partial with no base-template extends arrived as a document with no shell and no
    # stylesheets, so the one secret we can never show again landed on an unstyled page. Re-rendering
    # the list also means its new row is already behind the modal when the modal is dismissed.
    #
    # `token_plaintext` is set on THIS path only — never a redirect, a session, the URL or a log — and
    # the body partial reads it by that exact name (`test_show_once_displays_the_plaintext`).
    context = _settings_sources_context(request)
    context.update(token_created=True, token_plaintext=plaintext)
    return render(request, "ui/settings_sources.html", _base_context(request, **context))


def _org_scoped_token(
    request: HttpRequest, token_id: str
) -> tuple[IngestionToken | None, HttpResponse | None]:
    """Resolve a token the acting admin may touch, or the uniform 404 response.

    ADR-009: an admin reaches only their own org's tokens; a superAdmin reaches all. A token outside
    the caller's scope is indistinguishable from one that does not exist — no existence oracle.
    """
    # `source__organisation` rides along for `settings_token_delete`, which audits the *feed's* org
    # when it reaps a source whose last token just went.
    qs = IngestionToken.objects.select_related("organisation", "source", "source__organisation")
    if request.user.role != "superAdmin":
        qs = qs.filter(organisation_id=request.user.org_id)
    try:
        return qs.get(pk=token_id), None
    except IngestionToken.DoesNotExist:
        return None, render(request, "ui/404.html", status=404)


@not_viewer
@login_required
@require_POST
def settings_token_revoke(request: HttpRequest, token_id: str) -> HttpResponse:
    """Revoke an ingestion token. Revocation is terminal — see `settings_token_deactivate` to pause."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    token, not_found = _org_scoped_token(request, token_id)
    if not_found:
        return not_found

    if token.revoked_at is None:
        from django.utils import timezone

        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at", "updated_at"])

        from identity.audit import record_ingestion_token_change

        record_ingestion_token_change(
            request,
            actor=request.user,
            action="ingestion_token.revoked",
            token=token,
        )
        messages.success(request, f"Token '{token.name}' revoked.")
    else:
        messages.info(request, f"Token '{token.name}' was already revoked.")

    return redirect("ui-settings-sources")


@not_viewer
@login_required
@require_POST
def settings_token_deactivate(request: HttpRequest, token_id: str) -> HttpResponse:
    """Pause an active ingestion token — the reversible counterpart of revoke (§10a).

    A deactivated token refuses webhooks (both the lookup and `IngestionToken.verify` exclude it) but
    can be activated again. A revoked token cannot be deactivated: revocation is already terminal, so
    there is nothing left to pause.
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    token, not_found = _org_scoped_token(request, token_id)
    if not_found:
        return not_found

    if token.is_revoked:
        messages.error(request, f"Token '{token.name}' is revoked and cannot be deactivated.")
        return redirect("ui-settings-sources")
    if token.is_deactivated:
        messages.info(request, f"Token '{token.name}' was already deactivated.")
        return redirect("ui-settings-sources")

    token.deactivated_at = timezone.now()
    token.save(update_fields=["deactivated_at", "updated_at"])

    from identity.audit import record_ingestion_token_change

    record_ingestion_token_change(
        request,
        actor=request.user,
        action="ingestion_token.deactivated",
        token=token,
    )
    messages.success(request, f"Token '{token.name}' deactivated — it no longer accepts webhooks.")
    return redirect("ui-settings-sources")


@not_viewer
@login_required
@require_POST
def settings_token_activate(request: HttpRequest, token_id: str) -> HttpResponse:
    """Resume a deactivated token (§10a). A revoked token stays revoked — mint a new one instead."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    token, not_found = _org_scoped_token(request, token_id)
    if not_found:
        return not_found

    if token.is_revoked:
        messages.error(
            request,
            f"Token '{token.name}' is revoked and cannot be re-activated — mint a new token.",
        )
        return redirect("ui-settings-sources")
    if not token.is_deactivated:
        messages.info(request, f"Token '{token.name}' is already active.")
        return redirect("ui-settings-sources")

    token.deactivated_at = None
    token.save(update_fields=["deactivated_at", "updated_at"])

    from identity.audit import record_ingestion_token_change

    record_ingestion_token_change(
        request,
        actor=request.user,
        action="ingestion_token.activated",
        token=token,
    )
    messages.success(request, f"Token '{token.name}' activated — it accepts webhooks again.")
    return redirect("ui-settings-sources")


@not_viewer
@login_required
@require_POST
def settings_token_delete(request: HttpRequest, token_id: str) -> HttpResponse:
    """Delete an inactive token (§10b), reaping its feed when it was the feed's last credential.

    Only an inactive token (revoked or deactivated) can be deleted: an active credential must be
    deactivated first, so a secret a live feed still presents cannot be destroyed from under it. The
    audit row is written before the delete, in the same transaction, so the trail keeps the id and
    the owning org after the row is gone (`record_ingestion_token_change` reads `token.id`).

    A feed exists only to carry credentials, so deleting its **last** token also deletes the feed and
    its mapping profile — otherwise Settings → Sources would keep a configured feed that can never
    receive anything, and the Sources list would keep advertising it. The feed's alerts are **kept**
    (`Alert.ingestion_source` is `SET_NULL`): they lose the link to which profile accepted them, but
    their `raw_payload` and verbatim wire `source` survive. Emptiness is evaluated *after* the token
    delete, so a feed that still has a credential is never reaped.
    """
    guard = _require_admin_ui(request)
    if guard:
        return guard

    token, not_found = _org_scoped_token(request, token_id)
    if not_found:
        return not_found

    if token.is_active:
        messages.error(request, f"Token '{token.name}' is active — deactivate it before deleting.")
        return redirect("ui-settings-sources")

    from identity.audit import record_ingestion_source_deleted, record_ingestion_token_change

    name = token.name
    source = token.source
    source_slug = source.slug

    with transaction.atomic():
        record_ingestion_token_change(
            request,
            actor=request.user,
            action="ingestion_token.deleted",
            token=token,
        )
        token.delete()
        # After the token is gone: a feed with no tokens left has nothing to do and goes with it.
        # Audited first, in the same transaction, so the feed's id and org survive the row.
        source_reaped = not source.tokens.exists()
        if source_reaped:
            record_ingestion_source_deleted(request, actor=request.user, source=source)
            source.delete()

    if source_reaped:
        messages.success(
            request,
            f"Token '{name}' deleted. Feed '{source_slug}' had no tokens left, so it and its "
            "mapping profile were deleted too — its existing alerts are kept, but no longer name a feed.",
        )
    else:
        messages.success(request, f"Token '{name}' deleted.")
    return redirect("ui-settings-sources")
