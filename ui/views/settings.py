"""Settings home, organisation, and user views."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from identity.models import Organisation, User
from identity.services import (
    resolve_organisation,
    serializes_superadmin_changes,
)
from ui.pagination import paginate

from .common import (
    USERS_PER_ORG_CAP,
    _base_context,
    _require_admin_ui,
    _require_superadmin_ui,
    not_viewer,
)

logger = logging.getLogger(__name__)


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


@not_viewer
@login_required
def settings_users(request: HttpRequest) -> HttpResponse:
    """Users list + create (admin + superAdmin)."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    from core.enums import SUPER_ADMIN_ROLE, USER_ROLE_VALUES, VIEWER_ROLE

    is_superadmin = request.user.role == SUPER_ADMIN_ROLE

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        role = request.POST.get("role") or "analyst"
        org_id = request.POST.get("org")
        is_active = request.POST.get("is_active") == "on"

        errors = {}
        if not username:
            errors["username"] = "required"
        if not email:
            errors["email"] = "required"
        if not password:
            errors["password"] = "required"  # noqa: S105
        if role not in USER_ROLE_VALUES:
            errors["role"] = f"must be one of {', '.join(USER_ROLE_VALUES)}"

        # A5: Escalation guard — admin may only assign {analyst, viewer}
        if not is_superadmin:
            allowed_roles = {VIEWER_ROLE, "analyst"}
            if role not in allowed_roles:
                errors["role"] = f"admin may only assign roles: {', '.join(sorted(allowed_roles))}"

        # A3/A6: Org resolution — admin pins to own org; superAdmin reads posted org
        if is_superadmin:
            if not org_id:
                errors["org"] = "required"
            org = resolve_organisation(org_id) if org_id else None
            if org_id and org is None:
                errors["org"] = "organisation not found"
        else:
            org = request.user.org
            # Silently ignore any posted org_id (no oracle)

        if User.objects.filter(username=username).exists():
            errors.setdefault("username", "already exists")
        if User.objects.filter(login=username).exists():
            errors.setdefault("username", "already exists")

        if errors:
            for field, msg in errors.items():
                messages.error(request, f"{field}: {msg}")
        else:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError

            user = User(username=username, email=email, role=role, org=org, is_active=is_active)
            try:
                validate_password(password, user=user)
            except ValidationError as e:
                for msg in e.messages:
                    messages.error(request, f"password: {msg}")
            else:
                user.set_password(password)
                user.save()
                from identity.audit import record_user_change

                record_user_change(
                    request,
                    actor=request.user,
                    action="user.created",
                    target_id=str(user.id),
                    target_org=user.org,  # target user's org (AC-20)
                )
                messages.success(request, f"User '{user.login}' created.")
                return redirect("ui-settings-users")

    # GET: build context
    if is_superadmin:
        # B1: superAdmin per-org context — orgs in name order, each with users (newest first, capped)
        # Avoid N+1: use prefetch_related with a bounded Prefetch
        from django.db.models import Count, Prefetch

        orgs_qs = Organisation.objects.annotate(
            member_count=Count("users", distinct=True)
        ).order_by("name")

        # Prefetch users for each org, capped at USERS_PER_ORG_CAP, newest first (by -date_joined)
        orgs_qs = orgs_qs.prefetch_related(
            Prefetch(
                "users",
                queryset=User.objects.select_related("org").order_by("-date_joined")[
                    :USERS_PER_ORG_CAP
                ],
                to_attr="capped_users",
            )
        )

        org_contexts = []
        for org in orgs_qs:
            capped_users = list(org.capped_users)
            total = org.member_count
            capped = total > USERS_PER_ORG_CAP
            org_contexts.append(
                {
                    "org": org,
                    "users": capped_users,
                    "member_count": total,
                    "capped": capped,
                    "cap_limit": USERS_PER_ORG_CAP,
                }
            )

        # For superAdmin create modal (per-org), we need all orgs
        all_orgs = Organisation.objects.order_by("name")
        allowed_admin_roles = ("analyst", VIEWER_ROLE)

        context = _base_context(
            request,
            page_obj=None,  # superAdmin uses per-org cards, not a single paginated table
            filtered_from=None,
            users=[],  # flat list not used for superAdmin
            orgs=[],  # flat list not used for superAdmin
            role_choices=USER_ROLE_VALUES,
            is_superadmin=True,
            org_contexts=org_contexts,
            users_per_org_cap=USERS_PER_ORG_CAP,
            allowed_admin_roles=allowed_admin_roles,
            all_orgs=all_orgs,
        )
    else:
        # Admin: flat paginated table, scoped to own org, no org column.
        # An admin must never see superAdmins (lower roles never see higher ones); edit/delete of a
        # superAdmin is also refused upstream with a uniform 404 as a defence-in-depth backstop.
        queryset = (
            User.objects.select_related("org")
            .filter(org_id=request.user.org_id)
            .exclude(role=SUPER_ADMIN_ROLE)
            .order_by("username")
        )
        page_obj = paginate(request, queryset)
        filtered_from = None
        users = list(page_obj.object_list)
        orgs = Organisation.objects.order_by("name")
        allowed_admin_roles = ("analyst", VIEWER_ROLE)

        context = _base_context(
            request,
            page_obj=page_obj,
            filtered_from=filtered_from,
            users=users,
            orgs=orgs,
            role_choices=USER_ROLE_VALUES,
            is_superadmin=False,
            admin_org=request.user.org,
            allowed_admin_roles=allowed_admin_roles,
        )

    return render(request, "ui/settings_users.html", context)


@not_viewer
@login_required
@serializes_superadmin_changes
def settings_users_edit(request: HttpRequest, user_id: str) -> HttpResponse:
    """Edit a user (admin + superAdmin, org-scoped)."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    from core.enums import ADMIN_ROLE, SUPER_ADMIN_ROLE, USER_ROLE_VALUES, VIEWER_ROLE

    is_superadmin = request.user.role == SUPER_ADMIN_ROLE

    # A4: Object-scoped resolve — admin filters by org_id BEFORE get_object_or_404
    if is_superadmin:
        user = get_object_or_404(User, pk=user_id)
    else:
        user = get_object_or_404(User, pk=user_id, org_id=request.user.org_id)

    # A5: Admin may not edit a target whose role is admin/superAdmin → 404
    if not is_superadmin and user.role in (ADMIN_ROLE, SUPER_ADMIN_ROLE):
        return render(request, "ui/404.html", status=404)

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        role = request.POST.get("role")
        org_id = request.POST.get("org")
        is_active = request.POST.get("is_active") == "on"
        password = request.POST.get("password") or None

        org = resolve_organisation(org_id) if org_id else None

        allowed_roles = {VIEWER_ROLE, "analyst"}
        errors = {}
        if role and role not in USER_ROLE_VALUES:
            errors["role"] = f"must be one of {', '.join(USER_ROLE_VALUES)}"

        # A5: Escalation guard — admin may only assign {analyst, viewer}.
        # If the role key is present it must be a valid, allowed value; an empty/forged role is
        # rejected (not silently ignored), matching the create path. An *absent* role key is a
        # partial post (e.g. password reset) and leaves the role unchanged.
        if not is_superadmin and "role" in request.POST and role not in allowed_roles:
            errors["role"] = f"admin may only assign roles: {', '.join(sorted(allowed_roles))}"

        # An org can be reassigned but never cleared. Absent ⇒ leave unchanged
        # (partial posts such as a password reset must keep working); present-but-empty
        # is an explicit attempt to clear it and is rejected.
        if "org" in request.POST and not org_id:
            errors["org"] = "required"
        if org_id and org is None:
            errors["org"] = "organisation not found"

        if errors:
            for field, msg in errors.items():
                messages.error(request, f"{field}: {msg}")
        else:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError

            changed = False
            if user.email != email:
                user.email = email
                changed = True
            if role and user.role != role:
                # A5: Admin may not promote to admin/superAdmin (already validated above)
                # Guard: cannot change your own role away from superAdmin (A7 - preserved)
                if (
                    user.pk == request.user.pk
                    and request.user.role == SUPER_ADMIN_ROLE
                    and role != SUPER_ADMIN_ROLE
                ):
                    messages.error(request, "Cannot change your own role away from superAdmin.")
                    return redirect("ui-settings-users-edit", user_id=user_id)
                # Guard: cannot demote the last active superAdmin (A7 - preserved)
                if user.role == SUPER_ADMIN_ROLE and role != SUPER_ADMIN_ROLE:
                    active_superadmins = User.objects.filter(
                        role=SUPER_ADMIN_ROLE, is_active=True
                    ).count()
                    if active_superadmins == 1:
                        messages.error(request, "Cannot demote the last active superAdmin.")
                        return redirect("ui-settings-users-edit", user_id=user_id)
                user.role = role
                changed = True
            # A6: Org-move restriction — admin ignores posted org change; superAdmin may move
            if is_superadmin and org and user.org_id != org.id:
                user.org = org
                changed = True
            # For admin: silently ignore any org change (org stays as request.user.org)
            if user.is_active != is_active:
                # Guard: cannot deactivate yourself as superAdmin (A7 - preserved)
                if (
                    user.pk == request.user.pk
                    and request.user.role == SUPER_ADMIN_ROLE
                    and not is_active
                ):
                    messages.error(request, "Cannot deactivate yourself as superAdmin.")
                    return redirect("ui-settings-users-edit", user_id=user_id)
                # Guard: cannot deactivate the last active superAdmin (A7 - preserved)
                if user.role == SUPER_ADMIN_ROLE and user.is_active and not is_active:
                    active_superadmins = User.objects.filter(
                        role=SUPER_ADMIN_ROLE, is_active=True
                    ).count()
                    if active_superadmins == 1:
                        messages.error(request, "Cannot deactivate the last active superAdmin.")
                        return redirect("ui-settings-users-edit", user_id=user_id)
                user.is_active = is_active
                changed = True
            if password:
                try:
                    validate_password(password, user=user)
                except ValidationError as e:
                    for msg in e.messages:
                        messages.error(request, f"password: {msg}")
                    return redirect("ui-settings-users-edit", user_id=user_id)
                user.set_password(password)
                changed = True

            if changed:
                user.save()
                from identity.audit import record_user_change

                record_user_change(
                    request,
                    actor=request.user,
                    action="user.updated",
                    target_id=str(user.id),
                    target_org=user.org,  # target user's org (AC-20)
                )
                messages.success(request, f"User '{user.login}' updated.")
            else:
                messages.info(request, "No changes made.")
            return redirect("ui-settings-users")

    # GET: render the edit form
    # Admin: omit org field (org is pinned); superAdmin: full org select
    if is_superadmin:
        orgs = Organisation.objects.order_by("name")
    else:
        orgs = None  # template will omit the field

    allowed_admin_roles = ("analyst", VIEWER_ROLE)

    return render(
        request,
        "ui/settings_user_form.html",
        _base_context(
            request,
            user_obj=user,
            orgs=orgs,
            role_choices=USER_ROLE_VALUES,
            mode="edit",
            is_superadmin=is_superadmin,
            admin_org=request.user.org if not is_superadmin else None,
            allowed_admin_roles=allowed_admin_roles,
        ),
    )


@not_viewer
@login_required
@require_POST
@serializes_superadmin_changes
def settings_users_delete(request: HttpRequest, user_id: str) -> HttpResponse:
    """Delete a user (admin + superAdmin, org-scoped)."""
    guard = _require_admin_ui(request)
    if guard:
        return guard

    from core.enums import ADMIN_ROLE, SUPER_ADMIN_ROLE

    is_superadmin = request.user.role == SUPER_ADMIN_ROLE

    # A4: Object-scoped resolve — admin filters by org_id BEFORE get_object_or_404
    if is_superadmin:
        user = get_object_or_404(User, pk=user_id)
    else:
        user = get_object_or_404(User, pk=user_id, org_id=request.user.org_id)

    # A5: Admin may not delete a target whose role is admin/superAdmin → 404
    if not is_superadmin and user.role in (ADMIN_ROLE, SUPER_ADMIN_ROLE):
        return render(request, "ui/404.html", status=404)

    # Guard: cannot delete yourself (A7 - preserved)
    if user.pk == request.user.pk:
        messages.error(request, "Cannot delete yourself.")
        return redirect("ui-settings-users")

    # Guard: cannot delete the last active superAdmin (A7 - preserved)
    if user.role == SUPER_ADMIN_ROLE and user.is_active:
        active_superadmins = User.objects.filter(role=SUPER_ADMIN_ROLE, is_active=True).count()
        if active_superadmins == 1:
            messages.error(request, "Cannot delete the last active superAdmin.")
            return redirect("ui-settings-users")

    from identity.services import delete_user

    delete_user(user, actor=request.user, request=request)
    messages.success(request, f"User '{user.login}' deleted.")
    return redirect("ui-settings-users")


@not_viewer
@login_required
def settings_home(request: HttpRequest) -> HttpResponse:
    """Settings entry: redirect to first submenu item."""
    from django.shortcuts import redirect

    guard = _require_admin_ui(request)
    if guard:
        return guard

    if getattr(request.user, "role", "") == "superAdmin":
        return redirect("ui-settings-organisations")
    return redirect("ui-settings-sources")
