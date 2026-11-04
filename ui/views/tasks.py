"""Case task and task-note views."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from cases.ledger import append_timeline_event
from cases.models import (
    Case,
    Task,
    TaskNote,
)
from cases.template_defaults import (
    earliest_alert_for_case,
    format_full_payload,
    has_source_payload_sentinel,
)
from core.enums import (
    TASK_STATUS_CHOICES,
)
from core.serializers import task_json
from identity.audit import maybe_record_cross_org_read
from identity.models import User
from identity.scoping import (
    is_viewer,
    org_q,
    resolve_alert,
    resolve_case,
    resolve_task,
)
from realtime.publisher import publish_case_event
from ui.pagination import PICKER_PAGE_SIZE

from .common import (
    _base_context,
    _case_detail_redirect,
    _htmx_redirect,
    _parse_due_date,
    not_viewer,
)

logger = logging.getLogger(__name__)


@not_viewer
@login_required
@require_POST
def case_task_create(request: HttpRequest, case_id: str) -> HttpResponse:
    """Add a task. An unrecognised assignee is left unassigned rather than auto-created (ADR-002 §D11).

    Extended to accept `description`, `due_date`, `flag`, and a validated `status` (D-1 fix:
    reject any status not in `TASK_STATUS_CHOICES`). On HTMX error, re-renders the modal body
    with inline errors; otherwise flashes and redirects.
    """
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()

    title = (request.POST.get("title") or "").strip()
    if not title:
        errors = {"title": "A task needs a title."}
        return _render_task_form_error(request, case, errors, request.POST)

    # Validate status against the enum (D-1 fix)
    status_val = (request.POST.get("status") or "Waiting").strip()
    valid_statuses = {s for s, _ in TASK_STATUS_CHOICES}
    if status_val not in valid_statuses:
        errors = {"status": f"Invalid status: {status_val}"}
        return _render_task_form_error(request, case, errors, request.POST)

    # Parse due_date (YYYY-MM-DD -> end-of-day aware datetime)
    due_date_raw = (request.POST.get("due_date") or "").strip()
    due_date = _parse_due_date(due_date_raw) if due_date_raw else None
    if due_date_raw and due_date is None:
        errors = {"due_date": "Invalid date format. Use YYYY-MM-DD."}
        return _render_task_form_error(request, case, errors, request.POST)

    # Resolve assignee by login (org-scoped; unknown -> unassigned)
    login = (request.POST.get("assignee") or "").strip()
    assignee = (
        User.objects.filter(org_q("org", request.user), login=login).first() if login else None
    )

    # Flag checkbox: "on" when checked, absent when unchecked
    flag = request.POST.get("flag") == "on"

    description = (request.POST.get("description") or "").strip()

    task = Task.objects.create(
        case=case,
        title=title[:500],
        description=description,
        status=status_val,
        assignee=assignee,
        due_date=due_date,
        flag=flag,
    )

    # Publish `task` frame for live list refresh (mirrors `case_page_create`)
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "task", {"event": task_json(task)})
    )

    if request.headers.get("HX-Request"):
        # A successful modal POST: navigate back to the case (tab preserved) so the dialog closes
        # and the flash shows. A bare `204` would strand the open modal on screen.
        return _htmx_redirect(_case_detail_redirect(request, case.number).url)

    messages.success(request, "Task added.")
    return _case_detail_redirect(request, case.number)


def _render_task_form_error(
    request: HttpRequest,
    case: Case,
    errors: dict[str, str],
    post_data: Any,
    *,
    task: Task | None = None,
) -> HttpResponse:
    """Re-render the task form modal body with errors and submitted values (for HTMX).

    On non-HTMX POST, falls back to flash + redirect (preserves legacy behaviour).
    If `task` is provided, the template is in edit mode and receives the task for prefill.
    """
    if request.headers.get("HX-Request"):
        # Build form_values dict mirroring what the template expects
        form_values = {
            "title": post_data.get("title", ""),
            "description": post_data.get("description", ""),
            "due_date": post_data.get("due_date", ""),
            "assignee": post_data.get("assignee", ""),
            "status": post_data.get("status", "Waiting"),
            "flag": "on" if post_data.get("flag") == "on" else "",
        }
        # The error body replaces the *existing* modal body (`hx-target="#<modal_id>-body"`), so it
        # must reproduce the ids the full-page render used: every control references the footer form
        # via `form="{{ form_id }}"`, and the labels key off `modal_id`. Both are derived from the
        # path (create vs. edit) and never from the request, so a hostile POST cannot inject an id.
        if task is not None:
            modal_id = f"task-edit-{task.id}"
            form_id = f"task-edit-form-{task.id}"
        else:
            modal_id = "task-create-modal"
            form_id = "task-create-form"
        return render(
            request,
            "ui/_task_form_modal_body.html",
            {
                "errors": errors,
                "form_values": form_values,
                "task_status_options": TASK_STATUS_CHOICES,
                # Same uncapped, org-scoped list the tab and detail page pass; without it the
                # re-rendered assignee <select> would drop every option but "Unassigned".
                "task_assignees": User.objects.filter(
                    org_q("org", request.user), is_active=True
                ).order_by("login"),
                "case": case,
                "is_edit": task is not None,
                "task": task,
                "modal_id": modal_id,
                "form_id": form_id,
            },
            status=200,
        )
    # Non-HTMX fallback
    for msg in errors.values():
        messages.error(request, msg)
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_task_edit(request: HttpRequest, task_id: str) -> HttpResponse:
    """Edit a task (title, description, status, assignee, due_date, flag).

    Resolves the task through its case (org-scoped via `resolve_task`). Same validation
    and error handling as `case_task_create`. Publishes a `task` frame on success.
    """
    task = resolve_task(request.user, task_id, write=True)
    if task is None:
        raise Http404()
    case = task.case

    title = (request.POST.get("title") or "").strip()
    if not title:
        errors = {"title": "A task needs a title."}
        return _render_task_form_error(request, case, errors, request.POST, task=task)

    status_val = (request.POST.get("status") or "Waiting").strip()
    valid_statuses = {s for s, _ in TASK_STATUS_CHOICES}
    if status_val not in valid_statuses:
        errors = {"status": f"Invalid status: {status_val}"}
        return _render_task_form_error(request, case, errors, request.POST, task=task)

    due_date_raw = (request.POST.get("due_date") or "").strip()
    due_date = _parse_due_date(due_date_raw) if due_date_raw else None
    if due_date_raw and due_date is None:
        errors = {"due_date": "Invalid date format. Use YYYY-MM-DD."}
        return _render_task_form_error(request, case, errors, request.POST, task=task)

    login = (request.POST.get("assignee") or "").strip()
    assignee = (
        User.objects.filter(org_q("org", request.user), login=login).first() if login else None
    )

    flag = request.POST.get("flag") == "on"
    description = (request.POST.get("description") or "").strip()

    task.title = title[:500]
    task.description = description
    task.status = status_val
    task.assignee = assignee
    task.due_date = due_date
    task.flag = flag
    task.save(
        update_fields=[
            "title",
            "description",
            "status",
            "assignee",
            "due_date",
            "flag",
            "updated_at",
        ]
    )

    transaction.on_commit(
        partial(publish_case_event, str(case.id), "task", {"event": task_json(task)})
    )

    if request.headers.get("HX-Request"):
        return _htmx_redirect(_case_detail_redirect(request, case.number).url)

    messages.success(request, "Task updated.")
    return _case_detail_redirect(request, case.number)


@not_viewer
@login_required
@require_POST
def case_task_delete(request: HttpRequest, task_id: str) -> HttpResponse:
    """Delete a task (scoped to the user's org via `resolve_task`).

    Resolves and records the case ID *before* deletion (since `task.case` becomes
    inaccessible after). Publishes a `task` frame, then redirects to the case detail
    preserving `?tab=tasks`.
    """
    task = resolve_task(request.user, task_id, write=True)
    if task is None:
        raise Http404()
    case = task.case
    case_number = case.number
    case_id_str = str(case.id)

    task.delete()

    transaction.on_commit(
        partial(
            publish_case_event,
            case_id_str,
            "task",
            {"event": {"id": task_id, "deleted": True}},
        )
    )

    messages.success(request, "Task deleted.")
    return _case_detail_redirect(request, case_number)


@not_viewer
@login_required
@require_POST
def case_task_toggle(request: HttpRequest, case_id: str, task_id: str) -> HttpResponse:
    """Flip a task between `Waiting` and `Completed`."""
    case = resolve_case(request.user, case_id, write=True)
    if case is None:
        raise Http404()
    task = get_object_or_404(Task, pk=task_id, case=case)
    task.status = "Waiting" if task.status == "Completed" else "Completed"
    task.save(update_fields=["status", "updated_at"])

    # Publish `task` frame for live list refresh
    transaction.on_commit(
        partial(publish_case_event, str(case.id), "task", {"event": task_json(task)})
    )

    return _case_detail_redirect(request, case.number)


@login_required
@require_GET
def task_detail(request: HttpRequest, task_id: str) -> HttpResponse:
    """Task detail page: all fields + notes log + add-note composer.

    Viewer-readable (no `@not_viewer`). Resolves the task via `resolve_task` (read
    access through case visibility). Renders `ui/task_detail.html`.
    """
    task = resolve_task(request.user, task_id, write=False)
    if task is None:
        raise Http404()
    case = task.case

    # Audit superAdmin cross-org read (same target/type as the API task-detail view).
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=case,
        target_type="Task",
        target_id=str(task.id),
        target_org_field="owner_org",
    )

    can_mutate = not is_viewer(request.user)

    notes = task.notes.select_related("created_by").order_by("created_at")

    # The source-payload modal is opt-in: the sentinel is written by `_apply_template`, the
    # alert must still exist, and the caller must be able to read that alert directly. A case
    # sharee can read the task (and therefore the 64 KiB excerpt in its description) but alerts
    # have no shares, so they must not receive the raw payload through this side door.
    source_payload_alert = earliest_alert_for_case(case)
    show_source_payload = bool(
        source_payload_alert is not None
        and has_source_payload_sentinel(task.description)
        and resolve_alert(request.user, str(source_payload_alert.id)) is not None
    )

    return render(
        request,
        "ui/task_detail.html",
        _base_context(
            request,
            task=task,
            case=case,
            notes=notes,
            can_mutate=can_mutate,
            task_status_options=TASK_STATUS_CHOICES,
            show_source_payload=show_source_payload,
            source_payload_alert=source_payload_alert,
            source_payload_url=(
                reverse("ui-task-source-payload", kwargs={"task_id": task.id})
                if show_source_payload
                else None
            ),
            # The edit modal's assignee picker needs the same org-scoped list the tab
            # passes (capped at PICKER_PAGE_SIZE); without it the <select> would render empty
            # and a save would clear the assignee. Same query as `case_detail`'s `task_assignees`.
            task_assignees=User.objects.filter(org_q("org", request.user), is_active=True).order_by(
                "login"
            )[:PICKER_PAGE_SIZE],
        ),
    )


@never_cache
@login_required
@require_GET
def task_source_payload(request: HttpRequest, task_id: str) -> HttpResponse:
    """Lazy-loaded body for the task's full alert-payload modal.

    The 64 KiB excerpt lives in the task description; this fragment is the complete
    (pretty-printed, 8 MiB display-capped) payload from the earliest linked alert. It is
    deliberately not cached: the response contains raw alert data.
    """
    task = resolve_task(request.user, task_id, write=False)
    if task is None:
        raise Http404()
    alert = earliest_alert_for_case(task.case)
    if alert is None:
        return render(request, "ui/_task_source_payload.html", {"alert": None})
    # Alerts are org-owned and carry no shares; do not let a case sharee read them here.
    if resolve_alert(request.user, str(alert.id)) is None:
        raise Http404()

    # Audit superAdmin cross-org read of the task's full source payload.
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=task.case,
        target_type="Task",
        target_id=str(task.id),
        target_org_field="owner_org",
    )

    return render(
        request,
        "ui/_task_source_payload.html",
        {
            "alert": alert,
            "payload_json": format_full_payload(alert),
            "payload_url": reverse("ui-alert-detail", kwargs={"alert_id": alert.id}),
        },
    )


@not_viewer
@login_required
@require_POST
def task_note(request: HttpRequest, task_id: str) -> HttpResponse:
    """Append a note to a task.

    Validates non-empty body. In one transaction: creates `TaskNote` (+ `created_by`)
    and mirrors into the case ledger via `append_timeline_event(kind="task-note")`.
    On HTMX: returns 204 with `HX-Redirect` to the task detail URL (avoids depending
    on a template the frontend owns). On non-HTMX: redirects with flash.
    """
    task = resolve_task(request.user, task_id, write=True)
    if task is None:
        raise Http404()
    case = task.case

    body = (request.POST.get("body") or "").strip()
    if not body:
        if request.headers.get("HX-Request"):
            return HttpResponse("A note needs a body.", status=400)
        messages.error(request, "A note needs a body.")
        return redirect("ui-task-detail", task_id=task.id)

    with transaction.atomic():
        note = TaskNote.objects.create(task=task, message=body, created_by=request.user)
        append_timeline_event(
            case,
            title="Task note",
            description=body[:10000],
            kind="task-note",
            actor=request.user,
            metadata={"task_id": str(task.id), "note_id": str(note.id)},
        )

    task_detail_url = reverse("ui-task-detail", kwargs={"task_id": task.id})
    if request.headers.get("HX-Request"):
        # Reuse the shared 204+HX-Redirect helper so the note composer behaves like the task modals.
        return _htmx_redirect(task_detail_url)

    messages.success(request, "Note added.")
    return redirect("ui-task-detail", task_id=task.id)
