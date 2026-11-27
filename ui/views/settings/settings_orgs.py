"""Organisation settings views."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from identity.models import Organisation
from identity.services import resolve_organisation
from ui.pagination import paginate

from ..common import _base_context, _require_superadmin_ui, not_viewer


def _org_blockers_text(org: object) -> str:
    """Human summary of the tenant data that PROTECTs an org from deletion.

    Rendered server-side into the delete modal so the warning never depends on JS
    building HTML from server data (F3/F10).
    """
    parts: list[str] = []
    for count, singular in (
        (getattr(org, "cases_count", 0), "case"),
        (getattr(org, "alerts_count", 0), "alert"),
        (getattr(org, "observables_count", 0), "observable"),
        (getattr(org, "tokens_count", 0), "ingestion token"),
        (getattr(org, "sources_count", 0), "ingestion source"),
    ):
        if count:
            parts.append(f"{count} {singular}{'' if count == 1 else 's'}")
    return ", ".join(parts)


@not_viewer
@login_required
def settings_organisations(request: HttpRequest) -> HttpResponse:
    """Organisations list + create (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:200]
        description = (request.POST.get("description") or "").strip()
        if not name:
            messages.error(request, "Name is required.")
        elif Organisation.objects.filter(name=name).exists():
            messages.error(request, "An organisation with that name already exists.")
        else:
            from django.db import IntegrityError

            try:
                org = Organisation.objects.create(name=name, description=description)
            except IntegrityError:
                # Truncation can make two input names collide after the pre-check (§F7).
                messages.error(request, "An organisation with that name already exists.")
            else:
                from identity.audit import record_organisation_change

                record_organisation_change(
                    request,
                    actor=request.user,
                    action="organisation.created",
                    target_org=org,
                    target_id=str(org.id),
                )
                messages.success(request, f"Organisation '{org.name}' created.")
                return redirect("ui-settings-organisations")

    queryset = Organisation.objects.annotate(
        members_count=Count("users", distinct=True),
        cases_count=Count("cases", distinct=True),
        alerts_count=Count("alerts", distinct=True),
        observables_count=Count("observables", distinct=True),
        tokens_count=Count("ingestion_tokens", distinct=True),
        # ADR-009: an owned IngestionSource PROTECTs its org.
        sources_count=Count("ingestion_sources", distinct=True),
    ).order_by("name")

    page_obj = paginate(request, queryset)
    filtered_from = None  # No filters on this list

    orgs = list(page_obj.object_list)
    for org in orgs:
        # Dynamic, template-only display attributes (not model fields).
        org.blockers_text = _org_blockers_text(org)  # type: ignore[attr-defined]
        org.is_blocked = bool(org.blockers_text)  # type: ignore[attr-defined]

    return render(
        request,
        "ui/settings_organisations.html",
        _base_context(request, page_obj=page_obj, filtered_from=filtered_from, organisations=orgs),
    )


@not_viewer
@login_required
def settings_organisations_edit(request: HttpRequest, org_id: str) -> HttpResponse:
    """Edit an organisation (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    org = get_object_or_404(Organisation, pk=org_id)

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()[:200]
        description = (request.POST.get("description") or "").strip()
        if not name:
            messages.error(request, "Name is required.")
        elif Organisation.objects.exclude(pk=org.pk).filter(name=name).exists():
            messages.error(request, "An organisation with that name already exists.")
        else:
            from django.db import IntegrityError

            org.name = name
            org.description = description
            try:
                org.save(update_fields=["name", "description", "updated_at"])
            except IntegrityError:
                messages.error(request, "An organisation with that name already exists.")
            else:
                from identity.audit import record_organisation_change

                record_organisation_change(
                    request,
                    actor=request.user,
                    action="organisation.updated",
                    target_org=org,
                    target_id=str(org.id),
                )
                messages.success(request, f"Organisation '{org.name}' updated.")
                return redirect("ui-settings-organisations")

    return render(
        request,
        "ui/settings_organisation_form.html",
        _base_context(request, org=org, mode="edit"),
    )


@not_viewer
@login_required
@require_POST
def settings_organisations_delete(request: HttpRequest, org_id: str) -> HttpResponse:
    """Delete an organisation (superAdmin only)."""
    guard = _require_superadmin_ui(request)
    if guard:
        return guard

    org = get_object_or_404(Organisation, pk=org_id)
    members_action = request.POST.get("members")
    target_org_id = request.POST.get("target_org")
    # Malformed ids resolve to None instead of raising out of the query compiler (F7).
    target_org = resolve_organisation(target_org_id)

    # Mirror the API's up-front validation: a reassign must name a real, different
    # organisation, otherwise members would be orphaned (or reassigned to itself).
    if members_action == "reassign" and (target_org is None or target_org.pk == org.pk):
        messages.error(request, "Select a valid target organisation to reassign members to.")
        return redirect("ui-settings-organisations")

    from identity.services import OrganisationDeleteBlockedError, delete_organisation

    try:
        delete_organisation(
            org,
            members=members_action,
            target=target_org,
            actor=request.user,
            request=request,
        )
        messages.success(request, "Organisation deleted.")
    except OrganisationDeleteBlockedError as e:
        if e.reason == "self_lockout":
            messages.error(
                request,
                "Cannot delete your own account by deleting an organisation. "
                "Reassign members instead.",
            )
        elif e.reason == "last_superadmin":
            messages.error(
                request,
                "Cannot delete the last active superAdmin. Reassign members instead.",
            )
        elif e.reason == "invalid_target":
            messages.error(request, "Select a valid target organisation to reassign members to.")
        elif e.members is not None and not e.blockers.get("race"):
            messages.error(request, "Organisation has members. Choose reassign or delete.")
        else:
            blockers_text = ", ".join(f"{k}: {v}" for k, v in e.blockers.items() if v)
            messages.error(
                request, f"Cannot delete: organisation owns tenant data ({blockers_text})."
            )
        return redirect("ui-settings-organisations")

    return redirect("ui-settings-organisations")
