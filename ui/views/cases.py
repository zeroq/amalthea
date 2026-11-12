"""Case list, create, edit, and case-level mutation views."""

from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from alerts.escalation import (
    merge_alert_into_case,
)
from cases.correlation import (
    record_alert_bundled,
)
from cases.create import DEFAULT_CASE_STATUS, create_case
from cases.importing import (
    MAX_BYTES as CASE_IMPORT_MAX_BYTES,
)
from cases.importing import (
    CaseImportError,
    import_case_document,
    parse_case_export,
)
from cases.ledger import append_timeline_event
from cases.models import (
    Case,
    CaseStatus,
    Comment,
)
from cases.update import CaseUpdateError, apply_case_update
from cases.views import (
    _as_uuid,
    _resolve_login,
)
from core import taxonomy_read
from core.enums import (
    ADMIN_ROLE,
    PAP_CHOICES,
    SEVERITY_CHOICES,
    SUPER_ADMIN_ROLE,
    TLP_CHOICES,
)
from identity.models import AuditLog, Organisation, User
from identity.scoping import (
    case_visible_q,
    is_admin,
    is_case_owner,
    is_viewer,
    resolve_alert,
    resolve_case,
)
from identity.services import (
    resolve_organisation,
)
from ui.case_custom_fields import update_case_custom_field_value
from ui.pagination import PICKER_PAGE_SIZE, paginate
from ui.severity_filter import build_severity_filter_links, parse_severity_filter

from .common import (
    CASE_INLINE_EDIT_FIELDS,
    CASE_STAGE_VALUES,
    _base_context,
    _case_detail_redirect,
    not_viewer,
)

logger = logging.getLogger(__name__)


@not_viewer
@login_required
@require_POST
def case_alert_link(request: HttpRequest, case_id: str) -> HttpResponse:
    """Link an unlinked alert to this case from the Similar tab."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    alert = resolve_alert(request.user, request.POST.get("alert_id") or "", write=True)
    if alert is None:
        raise Http404()
    if alert.case_id is not None:
        messages.info(request, "Alert is already linked to a case.")
        return _case_detail_redirect(request, case.number)
    try:
        with transaction.atomic():
            merge_alert_into_case(alert, case, actor=request.user)
            record_alert_bundled(
                case,
                alert,
                actor=request.user,
                reason="Alert linked from Similar tab",
                source="merge",
            )
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return _case_detail_redirect(request, case.number)
    messages.success(request, f"Alert linked to case {case.number}.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_delete_ui(request: HttpRequest, case_id: str) -> HttpResponse:
    """Delete a case — admin and superAdmin only (UI endpoint).

    Admins may only delete cases belonging to their own organisation.
    SuperAdmins may delete any case. Non-admin/non-superAdmin users receive a uniform 404.
    """
    user = request.user
    if not is_admin(user):
        return render(request, "ui/404.html", status=404)

    case = resolve_case(user, case_id, write=True)
    if case is None:
        return render(request, "ui/404.html", status=404)

    # superAdmin may delete any case; admin only a case its own org owns. A shared-org case
    # grants access, not ownership, so it stays undeletable for an admin.
    if user.role == ADMIN_ROLE and case.owner_org_id != user.org_id:
        return render(request, "ui/404.html", status=404)

    case.delete()
    messages.success(request, "Case deleted.")
    return redirect("ui-case-list")


def _case_create_form_context(
    post_data: Any,
    status_options: Any,
    assignees: Any,
    *,
    request: HttpRequest | None = None,
) -> dict[str, Any]:
    """Build the context extras for the case create form (page or modal).

    `post_data` is a `QueryDict` (`None` on a GET): `dict(...)` would yield lists and render as
    `['x']`, so scalars are read with `.get()` (SEC-2026-10-11 N1). The numeric selects fall back
    to the model defaults, which is what the service applies when the wire omits them.
    """
    can_classify = is_admin(request.user) if request is not None else False
    form_values = {
        "title": "",
        "description": "",
        "summary": "",
        "severity": "2",
        "tlp": "2",
        "pap": "2",
        "status": DEFAULT_CASE_STATUS,
        "assignee": "",
        "tags": "",
        "flag": "",
        "scope": "unset",
        "case_type": "unset",
        "data_classification": "unset",
        "phase": "initial_triage",
    }
    if post_data is not None:
        for key in (
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
        ):
            form_values[key] = post_data.get(key, "")
        # data_classification is admin-only (Q-D5); only echo back if the actor may classify
        if can_classify:
            form_values["data_classification"] = post_data.get("data_classification", "")
        form_values["flag"] = "on" if post_data.get("flag") == "on" else ""
    return {
        "form_values": form_values,
        "case_status_options": status_options,
        "assignees": assignees,
        "severity_choices": SEVERITY_CHOICES,
        "tlp_choices": TLP_CHOICES,
        "pap_choices": PAP_CHOICES,
        "scope_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_CASE_SCOPE, include_hidden=False
        ),
        "case_type_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_CASE_TYPE, include_hidden=False
        ),
        "data_classification_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_DATA_CLASSIFICATION, include_hidden=False
        ),
        "phase_choices": taxonomy_read.choices(
            taxonomy_read.CATEGORY_CASE_PHASE, include_hidden=False
        ),
        "can_classify": can_classify,
    }


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


def _render_case_list(
    request: HttpRequest,
    *,
    open_new_case: bool = False,
    post_data: Any = None,
    status_options: Any = None,
    assignees: Any = None,
) -> HttpResponse:
    """Render the case list, optionally with the New-case modal open and pre-filled.

    Supports independent `scope` (open/all/closed) and `mine` (cases assigned to the
    signed-in user) filters. Only when the user can mutate (not a viewer) do we
    include the form context; viewers must not trigger those user/status queries
    at all.
    """
    from identity.scoping import case_visible_q

    # Scope is a tri-state: "open" (default), "all", or "closed". An unrecognised
    # value falls back to "open" so a malformed query never 500s.
    scope = request.GET.get("scope", "open")
    if scope not in ("open", "all", "closed"):
        scope = "open"

    mine = request.GET.get("mine") == "1"
    selected_severities = parse_severity_filter(request.GET)

    base_queryset = Case.objects.select_related("status", "assignee", "owner_org").filter(
        case_visible_q(request.user)
    )
    unfiltered_count = base_queryset.count()

    if mine:
        base_queryset = base_queryset.filter(assignee=request.user)

    if scope == "open":
        queryset = base_queryset.filter(closed_date__isnull=True)
    elif scope == "closed":
        queryset = base_queryset.filter(closed_date__isnull=False)
    else:  # scope == "all"
        queryset = base_queryset

    # Apply severity filter after org/scope/mine, before pagination.
    # Multiple selected severities combine with OR (Django ``severity__in``).
    if selected_severities:
        queryset = queryset.filter(severity__in=selected_severities)

    # Pagination for the cases list (primary list P). The assignees picker
    # remains capped at PICKER_PAGE_SIZE (class K, not paged).
    # "all" is not a filter; "open"/"closed" narrow the set.
    has_filter = scope != "all" or mine or bool(selected_severities)
    page_obj = paginate(request, queryset.order_by("-severity", "-start_date"))
    filtered_from = (
        unfiltered_count if has_filter and unfiltered_count > page_obj.paginator.count else None
    )

    heading = {
        ("open", False): "Open cases",
        ("all", False): "All cases",
        ("closed", False): "Closed cases",
        ("open", True): "My open cases",
        ("all", True): "My cases",
        ("closed", True): "My closed cases",
    }[(scope, mine)]

    context = {
        "page_obj": page_obj,
        "filtered_from": filtered_from,
        "scope": scope,
        "mine": mine,
        "heading": heading,
        "open_new_case": open_new_case,
        "selected_severities": selected_severities,
        "severity_filter_links": build_severity_filter_links(request, selected_severities),
        "can_delete_cases": is_admin(request.user),
        "can_delete_any_case": request.user.role == SUPER_ADMIN_ROLE,
    }

    # Only include form context for users who can actually create cases.
    if not is_viewer(request.user):
        if status_options is None:
            status_options = CaseStatus.objects.all().order_by("order", "value")
        if assignees is None:
            assignees = (
                User.objects.filter(org=request.user.org, is_active=True)
                .select_related("org")
                .order_by("login")[:PICKER_PAGE_SIZE]
            )
        context.update(
            _case_create_form_context(post_data, status_options, assignees, request=request)
        )

    return render(request, "ui/case_list.html", _base_context(request, **context))


@login_required
@require_GET
def case_list(request: HttpRequest) -> HttpResponse:
    """Open cases by default; closed ones on request.

    The `new=1` query parameter opens the New-case modal (for the `n` shortcut
    and for deep-linking into the modal state).
    """
    return _render_case_list(request, open_new_case=request.GET.get("new") == "1")


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


def _case_status_for_stage(case: Case, stage: str) -> CaseStatus | None:
    """The `CaseStatus` a posted `stage` resolves to, or `None` if the vocabulary lacks one.

    Prefers the case's *current* status when its stage already matches, so re-posting the same
    stage is an idempotent no-op rather than a silent jump to a different row that shares the
    stage. Otherwise the lowest-order non-hidden status with that stage wins. A hidden-only
    vocabulary yields `None` and the caller reports it — the old path auto-created statuses via
    `resolve_case_status`, which is exactly what a form field must not do.
    """
    if case.status_id and case.status.stage == stage:
        return case.status
    return CaseStatus.objects.filter(stage=stage, hidden=False).order_by("order", "value").first()


@not_viewer
@login_required
@require_POST
def case_set_status(request: HttpRequest, case_id: str) -> HttpResponse:
    """Move a case between stages by delegating to the shared applier (PLAN-2026-10-17 §12.8, D1).

    Form field: `status` = a concrete `CaseStatus.value`. The older `stage` field is still
    accepted for compatibility and resolves to the lowest-order non-hidden status in that stage.
    Nothing is persisted here: `apply_case_update` owns `status`, `closed_date`, `end_date` and the
    close rule, so the UI can no longer close a case around it. A Closed-stage status closes the
    case only when a verdict and a closing comment ride along; without them the applier refuses,
    the case stays open and the analyst gets the applier's message. The plain status control omits
    Closed-stage statuses (the dedicated close dialog supplies the two fields), so an accidental
    close is not reachable from the form at all.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    status_val = (request.POST.get("status") or "").strip()
    if status_val:
        target = CaseStatus.objects.filter(value=status_val, hidden=False).first()
        if target is None:
            messages.error(request, f"Unknown case status: {status_val}.")
            return _case_detail_redirect(request, case.number)
    else:
        # Backwards-compatible path for callers/tests that still post a stage.
        stage = (request.POST.get("stage") or DEFAULT_CASE_STATUS).strip()
        if stage not in CASE_STAGE_VALUES:
            messages.error(request, f"Unknown case stage: {stage}.")
            return _case_detail_redirect(request, case.number)
        target = _case_status_for_stage(case, stage)
        if target is None:
            messages.error(request, f"No case status is defined for the {stage} stage.")
            return _case_detail_redirect(request, case.number)
    payload: dict[str, Any] = {"status": target.value}
    comment = (request.POST.get("comment") or "").strip()
    if comment:
        payload["comment"] = comment
    verdict = (request.POST.get("verdict") or "").strip()
    if verdict:
        payload["verdict"] = verdict
    try:
        apply_case_update(case, payload, actor=request.user)
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _case_detail_redirect(request, case.number)
    case.refresh_from_db()
    messages.success(request, f"Case {case.number} is now {case.status.value}.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_close(request: HttpRequest, case_id: str) -> HttpResponse:
    """Close a case through the one close rule (PLAN-2026-10-17 §12.8).

    The entry point behind the "Close case" dialog: it resolves the Closed-stage status and posts
    it through `apply_case_update` together with the verdict and the closing comment the rule
    requires. A missing verdict or an empty comment is the applier's refusal surfaced as a flash
    error — the case stays open and nothing is written.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    target = _case_status_for_stage(case, "Closed")
    if target is None:
        messages.error(request, "No case status is defined for the Closed stage.")
        return _case_detail_redirect(request, case.number)
    payload: dict[str, Any] = {
        "status": target.value,
        "verdict": (request.POST.get("verdict") or "").strip(),
        "comment": (request.POST.get("comment") or "").strip(),
    }
    try:
        apply_case_update(case, payload, actor=request.user)
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _case_detail_redirect(request, case.number)
    case.refresh_from_db()
    messages.success(request, f"Case {case.number} closed.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_field_edit(request: HttpRequest, case_id: str) -> HttpResponse:
    """Set one inline-editable General-tab field via the shared applier (PLAN-2026-10-17 §12.8).

    `field` is whitelisted against `CASE_INLINE_EDIT_FIELDS`; an unknown key is a 404 and never
    reaches the applier's `setattr`. The value is validated by the applier against the field's own
    vocabulary / range, so an out-of-range severity or an unknown verdict is a clean form error,
    not a 500. Editing the verdict here is how a case can be marked TP/FP before it is closed
    (plan §14, resolved in favour of editable).
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    field = (request.POST.get("field") or "").strip()
    if field not in CASE_INLINE_EDIT_FIELDS:
        raise Http404()

    # Special handling for containment phase entry — actions are required.
    if field == "phase" and (request.POST.get("value") or "") == "containment":
        from cases.containment import apply_containment_actions

        action_codes = request.POST.getlist("containment_actions")
        other_text = (request.POST.get("other_text") or "").strip()

        if not action_codes:
            messages.error(
                request,
                "At least one containment action is required to enter the Containment phase.",
            )
            return _case_detail_redirect(request, case.number)

        try:
            result = apply_containment_actions(
                case,
                actor=request.user,
                action_codes=action_codes,
                other_text=other_text,
            )
        except CaseUpdateError as exc:
            messages.error(request, exc.message)
            return _case_detail_redirect(request, case.number)
        except Exception:
            logger.exception("Failed to enter containment for case %s", case.number)
            messages.error(
                request,
                "Could not enter containment. No changes were saved.",
            )
            return _case_detail_redirect(request, case.number)

        if result.error:
            messages.error(request, result.error)
            return _case_detail_redirect(request, case.number)

        # Success: the containment service already applied phase, status, actions, page, audit.
        case.refresh_from_db()
        messages.success(request, "Case entered containment phase with actions.")
        return _case_detail_redirect(request, case.number)

    # Normal path: single field update via the shared applier (unchanged behaviour).
    try:
        apply_case_update(case, {field: request.POST.get("value", "")}, actor=request.user)
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _case_detail_redirect(request, case.number)
    case.refresh_from_db()
    messages.success(request, f"Updated {field.replace('_', ' ')}.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_tag_add(request: HttpRequest, case_id: str) -> HttpResponse:
    """Attach existing tags and/or create-on-assign new ones (PLAN-2026-10-17 §12.8(5b)).

    Existing tags arrive as repeated `tags` values; `new_tags` is a comma-separated list typed into
    the dialog. Both merge into one `addTags` payload written through the applier, so the attach
    and the `tags-changed` ledger event cannot drift from the API's tag path.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    names = [n.strip() for n in request.POST.getlist("tags") if n.strip()]
    names += [n.strip() for n in (request.POST.get("new_tags") or "").split(",") if n.strip()]
    if not names:
        messages.error(request, "Select or type at least one tag.")
        return _case_detail_redirect(request, case.number)
    try:
        apply_case_update(case, {"addTags": names}, actor=request.user)
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _case_detail_redirect(request, case.number)
    messages.success(request, "Tags added.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_tag_remove(request: HttpRequest, case_id: str) -> HttpResponse:
    """Detach one tag via the shared applier (PLAN-2026-10-17 §12.8)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    name = (request.POST.get("tag") or "").strip()
    if not name:
        messages.error(request, "No tag specified.")
        return _case_detail_redirect(request, case.number)
    try:
        apply_case_update(case, {"removeTags": [name]}, actor=request.user)
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _case_detail_redirect(request, case.number)
    messages.success(request, f"Removed tag '{name}'.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_comment(request: HttpRequest, case_id: str) -> HttpResponse:
    """Append a note to the case ledger and record it as a first-class `Comment`.

    Two response shapes, one write. With `HX-Request` the browser already has the timeline on
    screen, so the rendered `<li>` comes back for HTMX to append — no reload, and the WebSocket
    clients see the same entry through the publish the write performs. Without HTMX the POST
    redirects exactly as it did before: a non-JS client has no swap to feed, and a bare 200 would
    strand it on a blank page (brief 6).

    Since T2 P2 the note is *also* a `Comment` row: the ledger `TimelineEvent` is what the timeline
    and the WebSocket render, while the `Comment` is the TheHive-parity entity the API's
    `GET /case/{id}/comment` returns. Both land in one transaction so a note can never be visible in
    one view of the case but not the other.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    body = (request.POST.get("body") or "").strip()
    if not body:
        # A 400, not a redirect: HTMX only swaps 2xx, so the flash error below would have nowhere
        # to land and the analyst would see a silent no-op.
        if request.headers.get("HX-Request"):
            return HttpResponse("A note needs a body.", status=400)
        messages.error(request, "A note needs a body.")
        return _case_detail_redirect(request, case.number)
    with transaction.atomic():
        Comment.objects.create(case=case, message=body, created_by=request.user)
        event = append_timeline_event(
            case, title="Note added", description=body[:10000], kind="comment", actor=request.user
        )
    if request.headers.get("HX-Request"):
        # No flash on this path: it would render on the *next* full page load, announcing a note
        # that is already on screen.
        return render(request, "ui/_timeline_entry.html", {"event": event})
    messages.success(request, "Note added to the case timeline.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_custom_field_edit(request: HttpRequest, case_id: str) -> HttpResponse:
    """Edit a case custom field value.

    POST fields:
      - value_id  : CaseCustomFieldValue UUID
      - value     : raw form value
      - tab       : "custom" (validated against CASE_TAB_SLUGS)
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    value_uuid = _as_uuid(request.POST.get("value_id"))
    if value_uuid is None:
        raise Http404()

    # Scope the lookup to this case. A value id from another case is indistinguishable from an
    # unknown one, so the response cannot confirm that it exists.
    value = case.custom_field_values.select_related("custom_field").filter(pk=value_uuid).first()
    if value is None:
        raise Http404()

    raw = request.POST.get("value", "")

    try:
        with transaction.atomic():
            update_case_custom_field_value(case, value, raw, actor=request.user)
    except CaseUpdateError as exc:
        messages.error(request, exc.message)
        return _case_detail_redirect(request, case.number)

    messages.success(request, "Custom field updated.")
    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def case_export(request: HttpRequest, case_id: str) -> HttpResponse:
    """Download case export (JSON)."""
    case = resolve_case(request.user, case_id, write=False)
    if case is None:
        raise Http404()
    from cases.views import _case_export_document

    doc = _case_export_document(case)

    response = HttpResponse(
        json.dumps(doc, indent=2, default=str),
        content_type="application/json",
    )
    response["Content-Disposition"] = f'attachment; filename="case-{case.number}-export.json"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def case_import_ui(request: HttpRequest) -> HttpResponse:
    """§18 — import a case from a ``CaseExport`` JSON file.

    Admin/superAdmin only; everyone else gets the same 404 as any other forbidden page (ADR-006).
    GET renders the standalone page; POST validates the upload and either redirects to the new case
    or re-renders the page with the service's message and a 400/409. Any failure leaves no case.
    """
    if not is_admin(request.user):
        raise Http404("Import is admin-only.")

    if request.method == "GET":
        return render(request, "ui/import_case.html", _base_context(request))

    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return render(
            request,
            "ui/import_case.html",
            {"error": "Choose a JSON export file to import.", **_base_context(request)},
            status=400,
        )
    if uploaded_file.size > CASE_IMPORT_MAX_BYTES:
        return render(
            request,
            "ui/import_case.html",
            {"error": "File exceeds the 5 MiB limit.", **_base_context(request)},
            status=400,
        )

    raw = uploaded_file.read(CASE_IMPORT_MAX_BYTES + 1)
    if len(raw) > CASE_IMPORT_MAX_BYTES:
        return render(
            request,
            "ui/import_case.html",
            {"error": "File exceeds the 5 MiB limit.", **_base_context(request)},
            status=400,
        )

    try:
        document = parse_case_export(raw)
        case = import_case_document(
            document,
            actor=request.user,
            source_filename=uploaded_file.name,
        )
    except CaseImportError as exc:
        status_code = 409 if exc.code in ("alert_duplicate", "alert_import_failed") else 400
        return render(
            request,
            "ui/import_case.html",
            {"error": exc.message, "error_code": exc.code, **_base_context(request)},
            status=status_code,
        )

    messages.success(request, f"Case #{case.number} imported from JSON.")
    return redirect("ui-case-detail", case_id=str(case.id))


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


@not_viewer
@login_required
def case_share_list(request: HttpRequest, case_id: str) -> HttpResponse:
    """List and manage case shares (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if not is_case_owner(request.user, case):
        return render(request, "ui/404.html", status=404)

    from cases.models import Share

    shares_qs = case.shares.select_related("organisation").order_by("created_at")
    available_orgs = Organisation.objects.exclude(
        id__in=shares_qs.values_list("organisation_id", flat=True)
    ).exclude(id=case.owner_org_id)

    if request.method == "POST":
        org_id = request.POST.get("organisation")
        permissions = request.POST.get("permissions", "read")
        if org_id:
            # A tampered/blank id must not raise out of the query compiler (F7 class).
            org = resolve_organisation(org_id)
            if org is None:
                messages.error(request, "Select a valid organisation.")
                return redirect("ui-case-share-list", case_id=case.number)
            share, created = Share.objects.update_or_create(
                case=case,
                organisation=org,
                defaults={"permissions": {"read": True, "write": permissions == "readwrite"}},
            )
            messages.success(
                request, f"Share {'created' if created else 'updated'} for {org.name}."
            )
        return redirect("ui-case-share-list", case_id=case.number)

    # Build full list of share dicts (for template compatibility)
    all_shares = []
    for share in shares_qs:
        all_shares.append(
            {
                "share": share,
                "revoke_url": reverse(
                    "ui-case-share-revoke", kwargs={"case_id": case.number, "share_id": share.id}
                ),
            }
        )

    # Pagination for the shares list (primary list P) — use the frozen helper.
    # Paginator accepts a sequence, so the list-of-dicts shape the template expects is preserved.
    page_obj = paginate(request, all_shares)
    filtered_from = None  # No filters on this list
    shares = page_obj.object_list

    return render(
        request,
        "ui/case_share_list.html",
        _base_context(
            request,
            case=case,
            page_obj=page_obj,
            filtered_from=filtered_from,
            shares=shares,
            available_orgs=available_orgs,
        ),
    )


@not_viewer
@login_required
@require_POST
def case_share_revoke(request: HttpRequest, case_id: str, share_id: str) -> HttpResponse:
    """Revoke a case share (owner org only)."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if not is_case_owner(request.user, case):
        return render(request, "ui/404.html", status=404)

    from cases.models import Share

    share = get_object_or_404(Share, pk=share_id, case=case)
    org_name = share.organisation.name
    share.delete()
    messages.success(request, f"Revoked share for {org_name}.")
    return redirect("ui-case-share-list", case_id=case.number)


@not_viewer
@login_required
def case_merge(request: HttpRequest, case_id: str) -> HttpResponse:
    """Merge cases via UI."""
    from cases.merge import merge_cases

    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    if request.method == "GET":
        # Show merge form with candidate cases
        candidates = (
            Case.objects.filter(case_visible_q(request.user))
            .filter(closed_date__isnull=True)
            .exclude(pk=case.pk)
            .order_by("-start_date")
        )
        return render(
            request, "ui/case_merge.html", _base_context(request, case=case, candidates=candidates)
        )
    # POST
    target_id = request.POST.get("target_case")
    if not target_id:
        messages.error(request, "Target case is required.")
        return redirect("ui-case-merge", case_id=case.number)
    target = resolve_case(request.user, target_id, write=True)
    if target is None:
        return render(request, "ui/404.html", status=404)
    try:
        with transaction.atomic():
            merged = merge_cases([target, case], actor=request.user)
        messages.success(request, f"Merged case {case.number} into {merged.number}.")
        return _case_detail_redirect(request, merged.number)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-case-merge", case_id=case.number)
