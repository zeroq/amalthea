"""Case-level UI mutation views (§13.3)."""

from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from alerts.escalation import (
    merge_alert_into_case,
)
from cases.correlation import (
    record_alert_bundled,
)
from cases.create import DEFAULT_CASE_STATUS
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
)
from core.enums import (
    ADMIN_ROLE,
)
from identity.scoping import (
    case_visible_q,
    is_admin,
    resolve_alert,
    resolve_case,
)
from ui.case_custom_fields import update_case_custom_field_value

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
