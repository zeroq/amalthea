"""`/api/v1/playbook/...` routes — the playbook authoring surface (plan §6.1, T2.3).

An Amalthea extension: TheHive 5 and `thehive4py` 2.1.0 expose no playbook route, so these are
our own shape (`deviations.md` **P12-2**), documented in `docs/spec/api.md`.

Two rules the shapes follow:

* **Write-time refusal, not run-time failure.** `triggerEvent` must be one of
  `automation.dispatcher.TRIGGER_EVENTS` and `config` must pass `automation.playbooks.validate_config`,
  so a playbook naming an unknown action or an unregistered `action_path` is a 400 at save time and
  never becomes a `Failed` run nobody is watching (AC6.12-P2-b). The SSRF guard in the executor is
  still the *runtime* boundary — authoring validation is additional, never a replacement.
* **The vocabulary is read from the code.** `playbook_meta` renders `TRIGGER_EVENTS` and
  `registered_actions()` directly, so the pickers a UI builds cannot drift from what dispatch and the
  executor will actually accept (AC6.12-P2-c).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.escalation import link_case_from_identifier
from automation.dispatcher import TRIGGER_EVENTS, run_now
from automation.executor import registered_actions
from automation.models import Playbook
from automation.playbooks import ACTIONS, HTTP_METHODS, MAX_TIMEOUT_SECONDS, validate_config
from cases.models import Case
from core.serializers import automation_run_json, playbook_json
from observables.models import Observable

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


def _as_uuid(value: Any) -> UUID | None:
    """Parse an identifier without letting a malformed one reach the ORM (a 400, not a 500)."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _resolve(identifier: str) -> Playbook | None:
    """A playbook by UUID first, then by name — the `idOrName` rule the rest of the surface uses."""
    pk = _as_uuid(identifier)
    if pk is not None:
        playbook = Playbook.objects.filter(pk=pk).first()
        if playbook is not None:
            return playbook
    return Playbook.objects.filter(name=str(identifier)).first()


def _trigger_event_error(value: object) -> list[str] | None:
    """Validate one `triggerEvent` against the dispatcher's vocabulary; `None` when acceptable."""
    name = str(value or "").strip()
    if not name:
        return ["required"]
    if name not in TRIGGER_EVENTS:
        return [f"must be one of {', '.join(TRIGGER_EVENTS)}"]
    return None


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def playbook_collection(request: Request) -> Response:
    """`GET /api/v1/playbook` lists; `POST` creates (201)."""
    if request.method == "POST":
        return _create_playbook(request)
    return Response([playbook_json(p) for p in Playbook.objects.order_by("name")])


def _create_playbook(request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}

    name = str(payload.get("name") or "").strip()
    if not name:
        return _bad("name is required", {"name": ["required"]})
    if Playbook.objects.filter(name=name[:200]).exists():
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
    )
    return Response(playbook_json(playbook), status=status.HTTP_201_CREATED)


@api_view(["GET"])
@renderer_classes([JSONRenderer])
def playbook_meta(request: Request) -> Response:
    """`GET /api/v1/playbook/_meta` — the authoring vocabulary, read from the code (AC6.12-P2-c).

    A hand-maintained list here would be a second source of truth for what a playbook may say, and
    the two would drift the first time an action was registered. Both halves are rendered from the
    same objects dispatch and the executor read.
    """
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
                    "registeredPaths": sorted(registered_actions()),
                },
            ],
            # Echoed so a picker can render "N actions" without parsing the list above.
            "actionNames": list(ACTIONS),
        }
    )


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def playbook_detail(request: Request, playbook_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/playbook/{idOrName}`; DELETE refuses a playbook that has runs."""
    playbook = _resolve(playbook_id)
    if playbook is None:
        return _not_found("Playbook")

    if request.method == "DELETE":
        # `AutomationRun.playbook` is `PROTECT` (REVIEW M14), so letting this fall through would
        # raise `ProtectedError` and surface as a 500. The plan asks for a clear message instead.
        if playbook.runs.exists():
            return _bad(
                "playbook has runs",
                {"_id": ["at least one AutomationRun still references this playbook"]},
            )
        playbook.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "PATCH":
        return _update_playbook(playbook, request)

    return Response(playbook_json(playbook))


def _update_playbook(playbook: Playbook, request: Request) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields: list[str] = []

    if "name" in payload:
        name = str(payload["name"] or "").strip()
        if not name:
            return _bad("name is required", {"name": ["required"]})
        if Playbook.objects.exclude(pk=playbook.pk).filter(name=name[:200]).exists():
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
    playbook.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def playbook_run(request: Request, playbook_id: str) -> Response:
    """`POST /api/v1/playbook/{idOrName}/run` — enqueue exactly one manual run (202, plan §6.1).

    The body names the subject, not the config: `{"case"?: idOrNumber, "observable"?: id}` with at
    least one required. The run is queued on commit and its result lands in `output_log` **and** the
    case timeline exactly as a triggered run's does — `run_now` shares the worker and the feedback
    loop, only the key and `triggered_by` differ.
    """
    playbook = _resolve(playbook_id)
    if playbook is None:
        return _not_found("Playbook")

    payload = request.data if isinstance(request.data, dict) else {}
    raw_case = payload.get("case")
    raw_observable = payload.get("observable")
    if raw_case in (None, "") and raw_observable in (None, ""):
        return _bad("case or observable is required", {"case": ["required"]})

    case = None
    if raw_case not in (None, ""):
        try:
            case = link_case_from_identifier(str(raw_case))
        except Case.DoesNotExist:
            case = None
        if case is None:
            return _not_found("Case")

    observable = None
    if raw_observable not in (None, ""):
        pk = _as_uuid(raw_observable)
        observable = Observable.objects.filter(pk=pk).first() if pk is not None else None
        if observable is None:
            return _not_found("Observable")

    try:
        run = run_now(playbook, case=case, observable=observable)
    except ValueError as exc:
        return _bad(str(exc), {"case": [str(exc)]})
    return Response(automation_run_json(run), status=status.HTTP_202_ACCEPTED)
