"""`/api/v1/playbook/...` routes — the playbook authoring surface (plan §6.1, T2.3).

An Amalthea extension: TheHive 5 and `thehive4py` 2.1.0 expose no playbook route, so these are
our own shape (`deviations.md` **P12-2**), documented in `docs/spec/api.md`.

Two rules the shapes follow:

* **Write-time refusal, not run-time failure.** `triggerEvent` must be one of
  `automation.dispatcher.TRIGGER_EVENTS` and `config` must pass `automation.playbooks.validate_config`,
  so a playbook naming an unknown action or an unknown script is a 400 at save time and
  never becomes a `Failed` run nobody is watching (AC6.12-P2-b). The SSRF guard in the executor is
  still the *runtime* boundary — authoring validation is additional, never a replacement.
* **The vocabulary is read from the code.** `playbook_meta` renders `TRIGGER_EVENTS` and
  `script_names()` directly, so the pickers a UI builds cannot drift from what dispatch and the
  executor will actually accept (AC6.12-P2-c).

Permissions: mutating endpoints (POST/PATCH/DELETE on playbooks, POST on run) require
`IsAnalystOrAbove` (analyst/admin/superAdmin). Read endpoints deny `viewer` role.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from automation.dispatcher import TRIGGER_EVENTS, PlaybookOrgMismatch, run_now
from automation.models import Playbook
from automation.playbooks import ACTIONS, HTTP_METHODS, MAX_TIMEOUT_SECONDS, validate_config
from automation.scripts_loader import discover_scripts
from automation.services import PlaybookDeleteBlockedError, delete_playbook
from core.serializers import automation_run_json, playbook_json
from identity.audit import maybe_record_cross_org_read
from identity.models import User
from identity.permissions import IsAnalystOrAbove
from identity.scoping import _is_superadmin, org_q, resolve_case, resolve_observable

__all__ = ["playbook_collection", "playbook_detail", "playbook_meta", "playbook_run"]


def _bad(message: str, fields: dict[str, list[str]]) -> Response:
    """The house 400 envelope (`cases/views.py::_bad`, same body, no shared import for one function)."""
    return Response(
        {"type": "BadRequest", "message": message, "fields": fields},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _not_found(what: str) -> Response:
    return Response(
        {"type": "NotFoundError", "message": f"{what} not found"}, status=status.HTTP_404_NOT_FOUND
    )


def _denied(what: str) -> Response:
    return Response(
        {"type": "AuthorizationError", "message": f"{what} denied"},
        status=status.HTTP_403_FORBIDDEN,
    )


def _as_uuid(value: Any) -> UUID | None:
    """Parse an identifier without letting a malformed one reach the ORM (a 400, not a 500)."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _resolve(identifier: str, user: User) -> Playbook | None:
    """Resolve a playbook by UUID first, then by name, scoped to the user's organisation.

    - UUID lookup is always attempted first, within the org scope.
    - Name lookup is scoped to `org_q("owner_org", user)`. For non-superAdmins the per-org
      constraint makes the name unique. For a superAdmin, two orgs can hold the same name; if
      the name matches more than one row, return None (uniform 404) so the caller must use a UUID.
    """
    qs = Playbook.objects.select_related("owner_org").filter(org_q("owner_org", user))
    pk = _as_uuid(identifier)
    if pk is not None:
        playbook = qs.filter(pk=pk).first()
        if playbook is not None:
            return playbook
    # Name lookup scoped to org
    matches = list(qs.filter(name=str(identifier)))
    if len(matches) != 1:
        # Zero matches → 404; more than one (superAdmin with same name across orgs) → uniform 404
        return None
    return matches[0]


def _require_analyst(request: Request) -> Response | None:
    """Reuse IsAnalystOrAbove and return the existing 403 envelope, or None if authorised."""
    perm = IsAnalystOrAbove()
    if not perm.has_permission(request, None):
        return _denied(perm.message)
    return None


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def playbook_collection(request: Request) -> Response:
    """`GET /api/v1/playbook` lists; `POST` creates (201).

    GET: `viewer` → 403. Otherwise org-scoped, `select_related("owner_org")`, order by owner org/name.
    POST: requires analyst role or above (IsAnalystOrAbove).
    """
    if request.method == "POST":
        # Apply write permission for create
        perm_result = _require_analyst(request)
        if perm_result is not None:
            return perm_result
        return _create_playbook(request)

    perm_result = _require_analyst(request)
    if perm_result is not None:
        return perm_result

    user = request.user
    qs = (
        Playbook.objects.select_related("owner_org")
        .filter(org_q("owner_org", user))
        .order_by("owner_org__name", "name")
    )
    return Response([playbook_json(p) for p in qs])


def _create_playbook(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}

    name = str(payload.get("name") or "").strip()
    if not name:
        return _bad("name is required", {"name": ["required"]})

    # Duplicate name check within the caller's org only
    user_org = getattr(request.user, "org_id", None)
    if (
        user_org is not None
        and Playbook.objects.filter(owner_org_id=user_org, name=name[:200]).exists()
    ):
        return _bad("name already exists", {"name": ["already exists"]})

    trigger_error = _trigger_event_error(payload.get("triggerEvent"))
    if trigger_error is not None:
        return _bad("triggerEvent is not valid", {"triggerEvent": trigger_error})

    if "config" not in payload:
        return _bad("config is required", {"config": ["required"]})
    config_errors = validate_config(payload["config"])
    if config_errors:
        return _bad("config is not valid", config_errors)

    is_active = payload.get("isActive", True)
    if not isinstance(is_active, bool):
        return _bad("isActive is not valid", {"isActive": ["must be a boolean"]})

    playbook = Playbook.objects.create(
        name=name[:200],
        description=str(payload.get("description") or ""),
        trigger_event=str(payload.get("triggerEvent") or "").strip(),
        is_active=is_active,
        config=dict(payload["config"]),
        owner_org=request.user.org,
    )
    return Response(playbook_json(playbook), status=status.HTTP_201_CREATED)


def _trigger_event_error(value: object) -> list[str] | None:
    """Validate one `triggerEvent` against the dispatcher's vocabulary; `None` when acceptable."""
    name = str(value or "").strip()
    if not name:
        return ["required"]
    if name not in TRIGGER_EVENTS:
        return [f"must be one of {', '.join(TRIGGER_EVENTS)}"]
    return None


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def playbook_meta(request: Request) -> Response:
    """`GET /api/v1/playbook/_meta` — the authoring vocabulary, read from the code (AC6.12-P2-c)."""

    scripts = discover_scripts()
    runnable_scripts = [
        {
            "name": info.name,
            "description": info.description,
            "triggerHint": info.trigger_hint,
        }
        for info in sorted(scripts.values(), key=lambda i: i.name)
        if info.load_error is None
    ]
    return Response(
        {
            "triggerEvents": list(TRIGGER_EVENTS),
            "actions": [
                {
                    "action": "http",
                    "methods": list(HTTP_METHODS),
                    "required": ["url"],
                    "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
                },
                {
                    "action": "python",
                    "scripts": runnable_scripts,
                },
            ],
            # Echoed so a picker can render "N actions" without parsing the list above.
            "actionNames": list(ACTIONS),
        }
    )


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def playbook_detail(request: Request, playbook_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/playbook/{idOrName}`; DELETE removes the run history too.

    GET: viewer → 403; cross-org → 404 (audited for superAdmin).
    PATCH/DELETE: requires analyst role or above (IsAnalystOrAbove).
    """
    perm_result = _require_analyst(request)
    if perm_result is not None:
        return perm_result

    user = request.user
    playbook = _resolve(playbook_id, user)
    if playbook is None:
        return _not_found("Playbook")

    # Audit only reads: a cross-org PATCH/DELETE is already a write attempt, not a read.
    if request.method == "GET" and _is_superadmin(user) and playbook.owner_org_id != user.org_id:
        maybe_record_cross_org_read(
            request,
            actor=user,
            target_obj=playbook,
            target_type="Playbook",
            target_id=str(playbook.id),
        )

    if request.method == "DELETE":
        # `AutomationRun.playbook` is `PROTECT` (REVIEW M14), so the service deletes the terminal
        # run history first and refuses while a run is still live. The schema guard remains the
        # backstop for any caller that bypasses this route.
        try:
            delete_playbook(playbook)
        except PlaybookDeleteBlockedError as exc:
            detail = (
                f"{exc.active_runs} run(s) are still pending or running"
                if exc.active_runs
                else str(exc)
            )
            return _bad("playbook has runs", {"_id": [detail]})
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "PATCH":
        return _update_playbook(playbook, request)

    return Response(playbook_json(playbook))


def _update_playbook(playbook: Playbook, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []

    # Note: owner_org is immutable and never accepted in PATCH
    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            return _bad("name is required", {"name": ["required"]})
        # Duplicate name check within the playbook's org only (exclude self).
        if (
            Playbook.objects.filter(owner_org_id=playbook.owner_org_id, name=name[:200])
            .exclude(pk=playbook.pk)
            .exists()
        ):
            return _bad("name already exists", {"name": ["already exists"]})
        playbook.name = name[:200]
        fields.append("name")

    if "description" in payload:
        playbook.description = str(payload["description"] or "")
        fields.append("description")

    if "triggerEvent" in payload:
        trigger_error = _trigger_event_error(payload["triggerEvent"])
        if trigger_error is not None:
            return _bad("triggerEvent is not valid", {"triggerEvent": trigger_error})
        playbook.trigger_event = str(payload["triggerEvent"]).strip()
        fields.append("trigger_event")

    if "isActive" in payload:
        if not isinstance(payload["isActive"], bool):
            return _bad("isActive is not valid", {"isActive": ["must be a boolean"]})
        playbook.is_active = payload["isActive"]
        fields.append("is_active")

    if "config" in payload:
        config_errors = validate_config(payload["config"])
        if config_errors:
            return _bad("config is not valid", config_errors)
        playbook.config = dict(payload["config"])
        fields.append("config")

    if not fields:
        return _bad("No updatable field supplied", {})

    # owner_org is immutable — never accepted or reassigned
    playbook.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def playbook_run(request: Request, playbook_id: str) -> Response:
    """`POST /api/v1/playbook/{idOrName}/run` — enqueue exactly one manual run (202, plan §6.1).

    The body names the subject, not the config: `{"case": idOrNumber}` or
    `{"observable": id}` with at least one required. The run is queued on commit and its result
    lands in `output_log` **and** the
    case timeline exactly as a triggered run's does — `run_now` shares the worker and the feedback
    loop, only the key and `triggered_by` differ.

    Tenant isolation: the case and observable are resolved through `identity.scoping` helpers.
    Cross-tenant access returns uniform 404 (no existence oracle). Triggering automation is a
    write action, so `write=True` is used for case resolution (T4-d).

    Requires analyst role or above (IsAnalystOrAbove).
    """
    perm_result = _require_analyst(request)
    if perm_result is not None:
        return perm_result

    playbook = _resolve(playbook_id, request.user)
    if playbook is None:
        return _not_found("Playbook")

    payload = request.data if isinstance(request.data, dict) else {}
    raw_case = payload.get("case")
    raw_observable = payload.get("observable")
    if raw_case in (None, "") and raw_observable in (None, ""):
        return _bad("case or observable is required", {"case": ["required"]})

    case = None
    if raw_case not in (None, ""):
        case = resolve_case(request.user, str(raw_case), write=True)
        if case is None:
            return _not_found("Case")

    observable = None
    if raw_observable not in (None, ""):
        pk = _as_uuid(raw_observable)
        if pk is not None:
            observable = resolve_observable(request.user, str(pk))
        if observable is None:
            return _not_found("Observable")

    try:
        run = run_now(playbook, case=case, observable=observable)
    except PlaybookOrgMismatch:
        # Org mismatch → uniform 404, no run created
        return _not_found("Playbook")
    except ValueError as exc:
        return _bad(str(exc), {"case": [str(exc)]})

    return Response(automation_run_json(run), status=status.HTTP_202_ACCEPTED)
