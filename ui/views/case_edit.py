"""Case create and edit UI views (§13.3)."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from cases.create import create_case
from cases.models import (
    Case,
    CaseStatus,
)
from cases.update import CaseUpdateError, apply_case_update
from cases.views import (
    _as_uuid,
    _resolve_login,
)
from core import taxonomy_read
from core.enums import (
    PAP_CHOICES,
    SEVERITY_CHOICES,
    TLP_CHOICES,
)
from identity.models import AuditLog, User
from identity.scoping import (
    is_admin,
    resolve_case,
)
from ui.pagination import PICKER_PAGE_SIZE

from .case_list import _case_create_form_context, _render_case_list
from .common import (
    _base_context,
    _case_detail_redirect,
    not_viewer,
)


def _render_case_create_form(
    request: HttpRequest,
    post_data: Any,
    status_options: Any,
    assignees: Any,
) -> HttpResponse:
    """Render the standalone create form page, pre-filled from the submission on an error re-render."""
    return render(
        request,
        "ui/case_create.html",
        _base_context(
            request,
            **_case_create_form_context(post_data, status_options, assignees, request=request),
        ),
    )


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def case_create(request: HttpRequest) -> HttpResponse:
    """§10.2 — Open a case from the UI with no alert behind it.

    The analyst's "phoned in by the helpdesk" path. GET renders the form; POST delegates to
    `cases.create.create_case`, the same service `POST /api/v1/case` uses, so both surfaces share
    the field set, the enum bounds, the org-scoped assignee lookup, extraction and the
    "Case created" ledger entry.

    Uniform 404 for a viewer via `@not_viewer` (ADR-006), before any parsing.
    """
    org = request.user.org

    status_options = CaseStatus.objects.all().order_by("order", "value")
    assignees = (
        User.objects.filter(org=org, is_active=True)
        .select_related("org")
        .order_by("login")[:PICKER_PAGE_SIZE]
    )

    if request.method == "GET":
        return _render_case_create_form(request, None, status_options, assignees)

    payload: dict[str, Any] = {
        "title": (request.POST.get("title") or "").strip()[:500],
        "description": (request.POST.get("description") or "").strip(),
        "summary": (request.POST.get("summary") or "").strip(),
    }
    for key in ("severity", "tlp", "pap"):
        raw = request.POST.get(key)
        if raw not in (None, ""):
            payload[key] = raw
    # The form always renders the checkbox, so an absent key means "unchecked".
    payload["flag"] = request.POST.get("flag") == "on"

    # Classification fields (PLAN-2026-10-14) — explicit allow-list, never splat request.POST
    for key in ("scope", "case_type", "phase"):
        raw = request.POST.get(key)
        if raw not in (None, ""):
            payload[key] = raw
    # data_classification is admin-only (Q-D5); only include if the actor can classify
    if is_admin(request.user):
        raw = request.POST.get("data_classification")
        if raw not in (None, ""):
            payload["data_classification"] = raw

    # Origin-aware error re-render: if the POST came from the cases list modal
    # (origin=cases), re-render the list with the modal open and values kept.
    # Otherwise (standalone page), re-render the standalone form page.
    # SEC/UX: a modal error must not dump the user onto a different page,
    # or silently close and lose their input.
    def _reject() -> HttpResponse:
        if request.POST.get("origin") == "cases":
            return _render_case_list(
                request,
                open_new_case=True,
                post_data=request.POST,
                status_options=status_options,
                assignees=assignees,
            )
        return _render_case_create_form(request, request.POST, status_options, assignees)

    # Status is bounded to the existing vocabulary so a crafted POST cannot mint one (ADR-002
    # §D11 keeps the shared service tolerant; the form must not be the thing that mints).
    status_val = (request.POST.get("status") or "").strip()
    if status_val:
        if not CaseStatus.objects.filter(value=status_val).exists():
            messages.error(request, f"Unknown case status: {status_val}")
            return _reject()
        payload["status"] = status_val

    assignee_val = (request.POST.get("assignee") or "").strip()
    if assignee_val:
        person = _resolve_login(assignee_val, org=org)
        if person is None:
            messages.error(request, "That assignee is not an active user in your organisation.")
            return _reject()
        payload["assignee"] = str(person.pk)

    tags_val = (request.POST.get("tags") or "").strip()
    if tags_val:
        payload["tags"] = [t.strip() for t in tags_val.split(",") if t.strip()]

    try:
        # The audit row shares the mutation's transaction (SEC-2026-10-11 L2): a failed audit
        # insert must roll the create back rather than leave an unlogged case behind.
        with transaction.atomic():
            case, warnings = create_case(payload, actor=request.user, org=org)
            AuditLog.objects.create(
                actor=request.user,
                actor_org=org,
                target_org=case.owner_org,
                action="case.created",
                target_type="Case",
                target_id=str(case.id),
                ip=request.META.get("REMOTE_ADDR"),
            )
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _reject()

    if warnings:
        messages.warning(request, "Created, with warnings: " + "; ".join(warnings))
    else:
        messages.success(request, f"Case {case.number} created.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def case_edit(request: HttpRequest, case_id: str) -> HttpResponse:
    """P2/C2 — Edit a case (title, description, severity, TLP/PAP, flag, summary, status, assignee, tags).

    GET: renders the edit form.
    POST: applies the update via `apply_case_update` (shared with API).
    Write share required (resolve_case with write=True); uniform 404 otherwise.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    # Context values for the form (shared by GET and error re-render)
    status_options = CaseStatus.objects.all().order_by("order", "value")
    assignees = (
        User.objects.filter(org=case.owner_org, is_active=True)
        .select_related("org")
        .order_by("login")[:PICKER_PAGE_SIZE]
    )

    if request.method == "GET":
        # Derive form_values from the case for initial render
        form_values = {
            "title": case.title,
            "description": case.description,
            "summary": case.summary,
            "severity": str(case.severity),
            "tlp": str(case.tlp),
            "pap": str(case.pap),
            "flag": "on" if case.flag else "",
            "status": case.status.value if case.status_id else "",
            # The select's option values are logins, so prefill the *login* — a prefilled UUID
            # never matches and the browser would submit the first option ("Unassigned"),
            # silently unassigning the case on a save that did not touch the field (SEC-2026-10-11
            # NEW-1).
            "assignee": case.assignee.login if case.assignee_id else "",
            "tags": ", ".join(case.tags.values_list("name", flat=True)),
            "scope": case.scope,
            "case_type": case.case_type,
            "data_classification": case.data_classification,
            "phase": case.phase,
        }
        can_classify = is_admin(request.user)
        return render(
            request,
            "ui/case_edit.html",
            _base_context(
                request,
                case=case,
                form_values=form_values,
                is_cross_org=case.owner_org_id != getattr(request.user, "org_id", None),
                severity_choices=SEVERITY_CHOICES,
                tlp_choices=TLP_CHOICES,
                pap_choices=PAP_CHOICES,
                scope_choices=taxonomy_read.choices(
                    taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False
                ),
                case_type_choices=taxonomy_read.choices(
                    taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False
                ),
                data_classification_choices=taxonomy_read.choices(
                    taxonomy_read.CATEGORY_DATA_CLASSIFICATION, include_hidden=False
                ),
                phase_choices=taxonomy_read.choices(
                    taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False
                ),
                can_classify=can_classify,
                case_status_options=status_options,
                assignees=assignees,
            ),
        )

    # POST — build payload from form
    payload: dict[str, Any] = {
        "title": (request.POST.get("title") or "").strip()[:500],
        "description": (request.POST.get("description") or "").strip(),
        "summary": (request.POST.get("summary") or "").strip(),
    }

    # Integer fields
    for key in ("severity", "tlp", "pap"):
        raw = request.POST.get(key)
        if raw is not None and raw != "":
            try:
                payload[key] = int(raw)
            except ValueError:
                messages.error(request, f"Invalid value for {key}")
                return _render_case_edit_form(
                    request, case, status_options, assignees, request.POST
                )

    # The form always renders the flag checkbox, so an absent key means "unchecked".
    payload["flag"] = request.POST.get("flag") == "on"

    # Classification fields (PLAN-2026-10-14) — explicit allow-list, never splat request.POST
    for key in ("scope", "case_type", "phase"):
        raw = request.POST.get(key)
        if raw not in (None, ""):
            payload[key] = raw
    # data_classification is admin-only (Q-D5); only include if the actor can classify
    if is_admin(request.user):
        raw = request.POST.get("data_classification")
        if raw not in (None, ""):
            payload["data_classification"] = raw

    # Status (CaseStatus.value) — bounded to the existing vocabulary so a crafted POST cannot
    # mint a new status (the shared applier is tolerant by design; ADR-002 §D11).
    status_val = (request.POST.get("status") or "").strip()
    if status_val:
        if not CaseStatus.objects.filter(value=status_val).exists():
            messages.error(request, f"Unknown case status: {status_val}")
            return _render_case_edit_form(request, case, status_options, assignees, request.POST)
        payload["status"] = status_val

    # Assignee (User UUID or login, or "" to unassign). Resolved against the case's owner org
    # (SEC-2026-10-11 M1): a cross-org login/UUID is rejected here with a clear message rather
    # than reaching the applier, which would silently unassign it. Defence in depth — the shared
    # applier is itself org-scoped, so the API cannot be used as a cross-tenant oracle either.
    assignee_val = (request.POST.get("assignee") or "").strip()
    if assignee_val:
        person = User.objects.filter(org=case.owner_org, login=assignee_val).first()
        if person is None:
            pk = _as_uuid(assignee_val)
            person = User.objects.filter(org=case.owner_org, pk=pk).first() if pk else None
        if person is None:
            messages.error(request, "Assignee is not a member of this case's organisation.")
            return _render_case_edit_form(request, case, status_options, assignees, request.POST)
        payload["assignee"] = str(person.pk)
    elif "assignee" in request.POST:
        payload["assignee"] = None

    # Tags (comma-separated string)
    tags_val = (request.POST.get("tags") or "").strip()
    if tags_val:
        payload["tags"] = [t.strip() for t in tags_val.split(",") if t.strip()]

    try:
        # The audit row shares the mutation's transaction (SEC-2026-10-11 L2): a failed audit
        # insert must roll the change back rather than leave an unlogged mutation.
        with transaction.atomic():
            apply_case_update(case, payload, actor=request.user)
            AuditLog.objects.create(
                actor=request.user,
                actor_org=getattr(request.user, "org", None),
                target_org=case.owner_org,
                action="case.updated",
                target_type="Case",
                target_id=str(case.id),
                ip=request.META.get("REMOTE_ADDR"),
            )
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _render_case_edit_form(request, case, status_options, assignees, request.POST)

    messages.success(request, f"Case {case.number} updated.")
    return _case_detail_redirect(request, case.number)


def _render_case_edit_form(
    request: HttpRequest,
    case: Case,
    status_options,
    assignees,
    post_data,
) -> HttpResponse:
    """Re-render the case edit form with submitted values and error message."""
    # `post_data` is a QueryDict; `dict(...)` would yield lists (`{"title": ["x"]}`), which render as
    # `['x']`. Read scalars with `.get()` so the form echoes what was typed (SEC-2026-10-11 N1).
    can_classify = is_admin(request.user)
    fields = (
        "title",
        "description",
        "summary",
        "severity",
        "tlp",
        "pap",
        "status",
        "assignee",
        "tags",
        "scope",
        "case_type",
        "phase",
    )
    form_values = {key: post_data.get(key, "") for key in fields}
    # data_classification is admin-only (Q-D5); only echo back if the actor may classify
    if can_classify:
        form_values["data_classification"] = post_data.get("data_classification", "")
    # For classification fields, if POST doesn't carry them (e.g. non-admin form), fall back to persisted value
    for key in ("scope", "case_type", "data_classification", "phase"):
        if form_values.get(key, "") == "":
            form_values[key] = getattr(case, key)
    # Ensure checkbox renders correctly
    form_values["flag"] = "on" if post_data.get("flag") == "on" else ""
    return render(
        request,
        "ui/case_edit.html",
        _base_context(
            request,
            case=case,
            form_values=form_values,
            is_cross_org=case.owner_org_id != getattr(request.user, "org_id", None),
            severity_choices=SEVERITY_CHOICES,
            tlp_choices=TLP_CHOICES,
            pap_choices=PAP_CHOICES,
            scope_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False
            ),
            case_type_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False
            ),
            data_classification_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_DATA_CLASSIFICATION, include_hidden=False
            ),
            phase_choices=taxonomy_read.choices(
                taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False
            ),
            can_classify=can_classify,
            case_status_options=status_options,
            assignees=assignees,
        ),
    )
