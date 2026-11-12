"""Automation ledger and playbook views."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from automation.dispatcher import TRIGGER_EVENTS, PlaybookOrgMismatch
from automation.models import ACTIVE_RUN_STATUSES, AutomationRun, Playbook
from automation.playbooks import ACTIONS, HTTP_METHODS
from automation.scripts_loader import discover_scripts, script_names
from automation.services import PlaybookDeleteBlockedError, delete_playbook
from cases.models import (
    Case,
)
from identity.audit import maybe_record_cross_org_read
from identity.models import User
from identity.scoping import (
    _is_superadmin,
    case_visible_q,
    org_q,
    resolve_case,
    resolve_observable,
)
from ui.pagination import paginate

from .common import (
    MAX_HEADERS,
    _base_context,
    not_viewer,
)

logger = logging.getLogger(__name__)


def _parse_playbook_config(post: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and validate playbook action config from POST data.

    Returns (config_dict, error_message). If error_message is not None, config_dict is None.
    Mirrors the validation logic from playbook_create/playbook_detail so a GET→POST round-trip
    produces an identical config (including header order).
    """
    from automation.playbooks import MAX_TIMEOUT_SECONDS

    action = (post.get("action") or "").lower()
    if action not in ("http", "python"):
        return None, "Action must be 'http' or 'python'."

    config: dict[str, Any] = {"action": action}

    if action == "http":
        url = (post.get("url") or "").strip()
        if not url:
            return None, "URL is required for HTTP actions."
        config["url"] = url

        method = (post.get("http_method") or "GET").upper()
        if method not in HTTP_METHODS:
            return None, "Invalid HTTP method."
        config["method"] = method

        timeout = post.get("timeout_seconds")
        if timeout:
            try:
                timeout_val = float(timeout)
                if timeout_val <= 0 or timeout_val > MAX_TIMEOUT_SECONDS:
                    raise ValueError
                config["timeoutSeconds"] = timeout_val
            except ValueError:
                return None, f"Timeout must be a positive number <= {MAX_TIMEOUT_SECONDS}."

        # Headers: preserve order by iterating header_key_<i> in index order
        headers = {}
        header_count = post.get("header_count")
        try:
            max_idx = int(header_count) if header_count else 4
        except (TypeError, ValueError):
            max_idx = 4
        # Bound the loop: header_count is attacker-controlled and drives a CPU-bound range(),
        # so an unbounded value is an authenticated DoS. Reject rather than silently clamp.
        if not 0 <= max_idx <= MAX_HEADERS:
            return None, f"Too many headers (max {MAX_HEADERS})."
        for i in range(max_idx):
            key_field = f"header_key_{i}"
            val_field = f"header_val_{i}"
            if post.get(key_field):
                headers[post[key_field]] = post.get(val_field, "")
        if headers:
            config["headers"] = headers

        body = post.get("http_body")
        if body:
            config["body"] = body

    else:  # python
        action_path = (post.get("action_path") or "").strip()
        if not action_path:
            return None, "Action path is required for Python actions."
        known_scripts = script_names()
        if action_path not in known_scripts:
            return (
                None,
                f"Unknown script. Discovered: {', '.join(sorted(known_scripts))}",
            )
        config["action_path"] = action_path

    return config, None


def _get_script_source(script_name: str) -> str | None:
    """Safely retrieve the source code of a discovered script for read-only display.

    Returns the source as a string, or None if the script is not found or has a load error.
    Never executes the script; uses inspect.getsource on the loaded module.
    """
    import inspect

    scripts = discover_scripts()
    info = scripts.get(script_name)
    if info is None or info.load_error is not None or info.run is None:
        return None
    try:
        return inspect.getsource(info.run)
    except (OSError, TypeError):
        # Could be a built-in function or source not available
        return None


@login_required
@require_GET
def automation_list(request: HttpRequest) -> HttpResponse:
    """Every automation run, newest first: the Module D ledger across all cases (org-scoped)."""
    from identity.scoping import case_visible_q

    visible_cases = Case.objects.filter(case_visible_q(request.user))
    queryset = (
        AutomationRun.objects.select_related("case", "playbook", "triggered_by_observable")
        .filter(case__in=visible_cases)
        .order_by("-created_at")
    )

    # No filters on this list currently, so filtered_from is None
    page_obj = paginate(request, queryset)
    filtered_from = None

    return render(
        request,
        "ui/automation_list.html",
        _base_context(
            request,
            page_obj=page_obj,
            filtered_from=filtered_from,
            playbooks=AutomationRun.objects.filter(case__in=visible_cases)
            .values("playbook_name")
            .annotate(n=Count("id"))
            .order_by("playbook_name"),
        ),
    )


def _resolve_playbook_ui(identifier: str, user: User) -> Playbook | None:
    """Resolve a playbook by UUID then name, always inside the caller's organisation scope.

    Returns `None` for a cross-org row, a missing row, or (for a superAdmin) an ambiguous name
    shared by two organisations. Callers turn `None` into the uniform 404.
    """
    qs = Playbook.objects.select_related("owner_org").filter(org_q("owner_org", user))
    try:
        pk: uuid.UUID | None = uuid.UUID(identifier)
    except (TypeError, ValueError):
        pk = None
    if pk is not None:
        playbook = qs.filter(pk=pk).first()
        if playbook is not None:
            return playbook
    matches = list(qs.filter(name=identifier)[:2])
    return matches[0] if len(matches) == 1 else None


@login_required
@require_GET
def playbook_list(request: HttpRequest) -> HttpResponse:
    """List the caller's playbooks with trigger, action, and last run status.

    `run_count`/`active_run_count` are annotated once per row: the delete modal needs both to
    tell "past runs" from "still running", and deriving them in the template would be an N+1.
    Viewers keep read access to this inventory; the template omits authoring links and the full
    config map. superAdmin sees every organisation and gets an Organisation column.
    """
    active_runs = Q(runs__status__in=ACTIVE_RUN_STATUSES)
    queryset = (
        Playbook.objects.select_related("owner_org")
        .filter(org_q("owner_org", request.user))
        .annotate(
            run_count=Count("runs"),
            active_run_count=Count("runs", filter=active_runs),
        )
        .prefetch_related("runs")
        .order_by("owner_org__name", "name")
    )
    page_obj = paginate(request, queryset)
    filtered_from = None  # No filters on this list
    return render(
        request,
        "ui/playbook_list.html",
        _base_context(
            request,
            page_obj=page_obj,
            playbooks=queryset,
            filtered_from=filtered_from,
            show_owner_org=_is_superadmin(request.user),
        ),
    )


@login_required
@require_GET
def playbook_scripts(request: HttpRequest) -> JsonResponse:
    """Return the playbook script catalogue for the form's script picker.

    The folder is deploy-only and has no watcher, so re-scan on every call
    (`refresh=True`): a script deployed after the process started appears as soon as the
    author selects PYTHON. Plain form GETs stay cached
    (PLAN-2026-11-12-playbook-script-fetch-on-change).
    """
    if request.user.role not in ("admin", "superAdmin"):
        return JsonResponse({"detail": "Not found"}, status=404)
    scripts = discover_scripts(refresh=True)
    data = [
        {
            "name": info.name,
            "description": info.description,
            "trigger_hint": info.trigger_hint,
        }
        for info in sorted(scripts.values(), key=lambda info: info.name)
        if info.load_error is None
    ]
    return JsonResponse({"scripts": data})


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def playbook_create(request: HttpRequest) -> HttpResponse:
    """Create a new playbook. GET shows the form, POST creates it.

    Both GET and POST require admin or superAdmin role (P5 authz tightening).
    """
    if request.user.role not in ("admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    from automation.playbooks import HTTP_METHODS
    from automation.scripts_loader import discover_scripts

    if request.method == "GET":
        # Cached baseline for the no-JS render; the picker refreshes via
        # ui-playbook-scripts when PYTHON is selected (PLAN-2026-11-12-playbook-script-fetch-on-change).
        scripts = discover_scripts()
        # Only include loadable scripts (load_error is None) for the picker
        script_list = [
            {
                "name": info.name,
                "description": info.description,
                "trigger_hint": info.trigger_hint,
            }
            for info in scripts.values()
            if info.load_error is None
        ]
        script_list_sorted = sorted(script_list, key=lambda s: s["name"])
        return render(
            request,
            "ui/playbook_form.html",
            _base_context(
                request,
                trigger_events=TRIGGER_EVENTS,
                actions=ACTIONS,
                http_methods=HTTP_METHODS,
                scripts=script_list_sorted,
            ),
        )

    # POST - create the playbook
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Name is required.")
        return redirect("ui-playbook-create")

    if Playbook.objects.filter(owner_org=request.user.org, name=name[:200]).exists():
        messages.error(request, "A playbook with that name already exists in your organisation.")
        return redirect("ui-playbook-create")

    trigger_event = (request.POST.get("trigger_event") or "").strip()
    if trigger_event not in TRIGGER_EVENTS:
        messages.error(
            request, f"Invalid trigger event. Must be one of: {', '.join(TRIGGER_EVENTS)}"
        )
        return redirect("ui-playbook-create")

    # Parse and validate config using shared helper
    config, error = _parse_playbook_config(request.POST)
    if error:
        messages.error(request, error)
        return redirect("ui-playbook-create")

    description = (request.POST.get("description") or "").strip()
    is_active = request.POST.get("is_active") == "on"

    Playbook.objects.create(
        owner_org=request.user.org,
        name=name[:200],
        description=description,
        trigger_event=trigger_event,
        is_active=is_active,
        config=config,
    )
    messages.success(request, f"Playbook '{name}' created.")
    return redirect("ui-playbook-list")


@not_viewer
@login_required
def playbook_detail(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """View or edit a playbook.

    GET: any authenticated non-viewer org member; superAdmin may cross orgs (audited on GET).
    POST (update): requires admin or superAdmin role (P5 authz tightening).
    """
    from automation.playbooks import HTTP_METHODS
    from automation.scripts_loader import discover_scripts

    playbook = _resolve_playbook_ui(playbook_id, request.user)
    if playbook is None:
        raise Http404()

    if (
        request.method == "GET"
        and _is_superadmin(request.user)
        and playbook.owner_org_id != request.user.org_id
    ):
        maybe_record_cross_org_read(
            request,
            actor=request.user,
            target_obj=playbook,
            target_type="Playbook",
            target_id=str(playbook.id),
        )

    if request.method == "GET":
        visible_cases = Case.objects.filter(case_visible_q(request.user))
        runs_qs = (
            AutomationRun.objects.filter(playbook=playbook, case__in=visible_cases)
            .select_related("case", "triggered_by_observable")
            .order_by("-created_at")
        )
        from ui.pagination import RAIL_CAP

        runs_total = runs_qs.count()
        runs = list(runs_qs[:RAIL_CAP])
        # The delete guard is global, so the modal warning must be global too: a playbook can be
        # referenced by runs on cases this user cannot see.
        run_count = playbook.runs.count()
        active_run_count = playbook.runs.filter(status__in=ACTIVE_RUN_STATUSES).count()

        # Build script list for the detail view (includes all discovered, even with errors, for diagnostics)
        scripts = discover_scripts()
        script_list = []
        script_dict = {}
        for info in scripts.values():
            script_data = {
                "name": info.name,
                "description": info.description,
                "trigger_hint": info.trigger_hint,
                "path": str(info.path),
                "load_error": info.load_error,
            }
            # Add source for loadable scripts (for read-only display in detail view)
            if info.load_error is None and info.run is not None:
                script_data["source"] = _get_script_source(info.name)
            script_list.append(script_data)
            script_dict[info.name] = script_data

        return render(
            request,
            "ui/playbook_detail.html",
            _base_context(
                request,
                playbook=playbook,
                runs=runs,
                runs_total=runs_total,
                run_count=run_count,
                active_run_count=active_run_count,
                trigger_events=sorted(TRIGGER_EVENTS),
                actions=ACTIONS,
                http_methods=HTTP_METHODS,
                scripts=sorted(script_list, key=lambda s: s["name"]),
                script_dict=script_dict,
            ),
        )

    # POST - update the playbook: requires admin or superAdmin (P5 authz tightening)
    if request.user.role not in ("admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    description = (request.POST.get("description") or "").strip()
    trigger_event = (request.POST.get("trigger_event") or "").strip()
    if trigger_event and trigger_event not in TRIGGER_EVENTS:
        messages.error(request, "Invalid trigger event.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    is_active = request.POST.get("is_active") == "on"

    action = (request.POST.get("action") or "").lower()
    if action and action not in ("http", "python"):
        messages.error(request, "Action must be 'http' or 'python'.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    config = dict(playbook.config) if playbook.config else {}
    if action:
        config["action"] = action
    elif "action" in config:
        action = config["action"]

    if action == "http":
        url = request.POST.get("url")
        if url is not None:
            url = url.strip()
            if not url:
                messages.error(request, "URL is required for HTTP actions.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            config["url"] = url

        method = request.POST.get("http_method")
        if method:
            method = method.upper()
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                messages.error(request, "Invalid HTTP method.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            config["method"] = method

        timeout = request.POST.get("timeout_seconds")
        if timeout is not None:
            timeout = timeout.strip()
            if timeout:
                try:
                    timeout_val = float(timeout)
                    if timeout_val <= 0 or timeout_val > 30:
                        raise ValueError
                    config["timeoutSeconds"] = timeout_val
                except ValueError:
                    messages.error(request, "Timeout must be a positive number <= 30.")
                    return redirect("ui-playbook-detail", playbook_id=playbook_id)
            else:
                config.pop("timeoutSeconds", None)

        # Handle headers - this is a simplified version, full form would be more complex
        # For now, we just allow clearing
        if "clear_headers" in request.POST:
            config.pop("headers", None)

        body = request.POST.get("http_body")
        if body is not None:
            config["body"] = body.strip() if body else None

    elif action == "python":
        action_path = request.POST.get("action_path")
        if action_path is not None:
            action_path = action_path.strip()
            if not action_path:
                messages.error(request, "Action path is required for Python actions.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            known_scripts = script_names()
            if action_path not in known_scripts:
                messages.error(request, "Unknown script.")
                return redirect("ui-playbook-detail", playbook_id=playbook_id)
            config["action_path"] = action_path

    playbook.description = description
    if trigger_event:
        playbook.trigger_event = trigger_event
    playbook.is_active = is_active
    playbook.config = config
    playbook.save(
        update_fields=["description", "trigger_event", "is_active", "config", "updated_at"]
    )

    messages.success(request, f"Playbook '{playbook.name}' updated.")
    return redirect("ui-playbook-detail", playbook_id=playbook_id)


@not_viewer
@login_required
@require_http_methods(["GET", "POST"])
def playbook_edit(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """Edit a playbook.

    GET: any authenticated non-viewer org member; superAdmin may cross orgs.
    POST: requires admin or superAdmin role (P5 authz tightening).
    """
    from automation.playbooks import HTTP_METHODS
    from automation.scripts_loader import discover_scripts

    playbook = _resolve_playbook_ui(playbook_id, request.user)
    if playbook is None:
        raise Http404()

    if request.method == "GET":
        config = playbook.config or {}
        # Cached baseline for the no-JS render; the picker refreshes via
        # ui-playbook-scripts when PYTHON is selected (PLAN-2026-11-12-playbook-script-fetch-on-change).
        scripts = discover_scripts()
        script_list = [
            {
                "name": info.name,
                "description": info.description,
                "trigger_hint": info.trigger_hint,
            }
            for info in scripts.values()
            if info.load_error is None
        ]
        script_list_sorted = sorted(script_list, key=lambda s: s["name"])

        # Find the currently selected script for description display
        selected_script = None
        action_path = config.get("action_path", "")
        if action_path:
            for s in script_list_sorted:
                if s["name"] == action_path:
                    selected_script = s
                    break

        return render(
            request,
            "ui/playbook_form.html",
            _base_context(
                request,
                playbook=playbook,
                config=config,
                trigger_events=sorted(TRIGGER_EVENTS),
                actions=ACTIONS,
                http_methods=HTTP_METHODS,
                scripts=script_list_sorted,
                selected_script=selected_script,
            ),
        )

    # POST - update the playbook: requires admin or superAdmin (P5 authz tightening)
    if request.user.role not in ("admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    # Parse and validate config using shared helper
    config, error = _parse_playbook_config(request.POST)
    if error:
        messages.error(request, error)
        return redirect("ui-playbook-edit", playbook_id=playbook_id)

    description = (request.POST.get("description") or "").strip()
    trigger_event = (request.POST.get("trigger_event") or "").strip()
    if trigger_event and trigger_event not in TRIGGER_EVENTS:
        messages.error(request, "Invalid trigger event.")
        return redirect("ui-playbook-edit", playbook_id=playbook_id)

    is_active = request.POST.get("is_active") == "on"

    playbook.description = description
    if trigger_event:
        playbook.trigger_event = trigger_event
    playbook.is_active = is_active
    playbook.config = config
    playbook.save(
        update_fields=["description", "trigger_event", "is_active", "config", "updated_at"]
    )

    messages.success(request, f"Playbook '{playbook.name}' updated.")
    return redirect("ui-playbook-detail", playbook_id=playbook_id)


@not_viewer
@login_required
@require_POST
def playbook_delete(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """Delete a playbook and its run history. Requires analyst role or above.

    Refused while any run is `Pending`/`Running`; terminal runs are deleted with the playbook.
    Cross-org playbooks are a uniform 404 for non-superAdmins.
    """
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    playbook = _resolve_playbook_ui(playbook_id, request.user)
    if playbook is None:
        raise Http404()

    name = playbook.name
    try:
        run_count = delete_playbook(playbook)
    except PlaybookDeleteBlockedError as exc:
        if exc.active_runs:
            run_word = "run is" if exc.active_runs == 1 else "runs are"
            pronoun = "it" if exc.active_runs == 1 else "them"
            messages.error(
                request,
                f"Cannot delete playbook: {exc.active_runs} {run_word} still pending or running. "
                f"Wait for {pronoun} to finish or deactivate the playbook.",
            )
        else:
            messages.error(request, f"Cannot delete playbook: {exc}.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    if run_count:
        run_word = "run" if run_count == 1 else "runs"
        verb = "was" if run_count == 1 else "were"
        messages.success(
            request,
            f"Playbook '{name}' deleted. {run_count} {run_word} of history {verb} also removed.",
        )
    else:
        messages.success(request, f"Playbook '{name}' deleted.")
    return redirect("ui-playbook-list")


@not_viewer
@login_required
@require_POST
def playbook_run(request: HttpRequest, playbook_id: str) -> HttpResponse:
    """Manually trigger a playbook run. Requires analyst role or above.

    The playbook and the subject case must belong to the same organisation. Cross-org playbooks
    are a uniform 404; a cross-org case is rejected before a run is created.
    """
    if request.user.role not in ("analyst", "admin", "superAdmin"):
        return render(request, "ui/404.html", status=404)

    from automation.dispatcher import run_now

    playbook = _resolve_playbook_ui(playbook_id, request.user)
    if playbook is None:
        raise Http404()

    case_id = (request.POST.get("case") or "").strip()
    observable_id = (request.POST.get("observable") or "").strip()

    if not case_id and not observable_id:
        messages.error(request, "Either a case or an observable is required.")
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    case = None
    if case_id:
        case = resolve_case(request.user, case_id, write=True)
        if case is None:
            return render(request, "ui/404.html", status=404)

    observable = None
    if observable_id:
        observable = resolve_observable(request.user, observable_id)
        if observable is None:
            return render(request, "ui/404.html", status=404)

    # Ensure observable is linked to case if case not provided
    if case is None and observable is not None:
        link = observable.case_observables.select_related("case").first()
        if link is None:
            messages.error(request, "Observable is not linked to any case.")
            return redirect("ui-playbook-detail", playbook_id=playbook_id)
        case = link.case
        # Verify the resolved case is also accessible (write)
        case = resolve_case(request.user, str(case.id), write=True)
        if case is None:
            return render(request, "ui/404.html", status=404)

    try:
        run = run_now(playbook, case=case, observable=observable)
    except PlaybookOrgMismatch:
        # Do not reveal which side belongs to which organisation.
        return render(request, "ui/404.html", status=404)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ui-playbook-detail", playbook_id=playbook_id)

    messages.success(request, f"Playbook triggered (run {run.id}).")
    return redirect("ui-playbook-detail", playbook_id=playbook_id)
