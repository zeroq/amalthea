from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

import identity.scoping
from cases.models import (
    Task,
)
from compat.time import parse_timestamp
from core.serializers import (
    task_json,
)
from identity.audit import maybe_record_cross_org_read

from ._common import (
    _TASK_STATUSES,
    _as_uuid,
    _bad,
    _case_org_scope,
    _not_found,
    _resolve_login,
    _resolve_task,
)


def case_task_create(request: Request, case_id: str) -> Response:
    """`POST /api/v1/case/{idOrNumber}/task` — add a task to the case.

    Deliberately *not* `@api_view`-decorated: this is an inner helper that `case_task_list`
    calls with the request it already holds. Wrapping it would make it a fresh
    `WrappedAPIView.as_view()`, and calling that with a DRF `Request` runs
    `initialize_request()` on it — `Request.__init__` then asserts `isinstance(request,
    HttpRequest)` and the endpoint answers 500 for every POST. The route is registered once,
    against `case_task_list`, which dispatches GET/POST itself.
    """
    case = identity.scoping.resolve_case(request.user, case_id, write=True)
    if case is None:
        return _not_found("Case")
    payload = request.data if isinstance(request.data, dict) else {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return Response(
            {
                "type": "BadRequest",
                "message": "title is required",
                "fields": {"title": ["required"]},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    task = Task.objects.create(
        case=case,
        title=title[:500],
        # Was `"Todo"`, which is not in `TASK_STATUS_CHOICES` and violates the
        # `task_status_valid` CHECK on Postgres (plan §13 bug fix, A3).
        status=str(payload.get("status") or "Waiting"),
    )
    return Response(
        {"_id": str(task.id), "id": str(task.id), "title": task.title, "status": task.status},
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "POST"])
@renderer_classes([JSONRenderer])
def case_task_list(request: Request, case_id: str) -> Response:
    """`GET` lists a case's tasks; `POST` adds one.

    Both verbs share the route *and* the view, so `?status=` filtering on the list and task creation
    cannot drift apart, and so a single `path()` entry serves the collection.
    """
    if request.method == "POST":
        return case_task_create(request, case_id)
    case = identity.scoping.resolve_case(request.user, case_id, write=False)
    if case is None:
        return _not_found("Case")

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=case,
        target_type="Case",
        target_id=str(case.id),
        target_org_field="owner_org",
    )

    return Response(
        [
            {
                "_id": str(t.id),
                "id": str(t.id),
                "title": t.title,
                "status": t.status,
                "assignee": getattr(t.assignee, "login", None),
            }
            for t in case.tasks.all()
        ]
    )


@api_view(["GET", "PATCH", "DELETE"])
@renderer_classes([JSONRenderer])
def task_detail(request: Request, task_id: str) -> Response:
    """`GET|PATCH|DELETE /api/v1/task/{taskId}` — one task, T1 detail (plan §7.2).

    Org-scoped through the owning case using `identity.scoping.resolve_case` with the
    appropriate `write` flag: a task's UUID is guessable-shaped like any other id, and
    the id must not become a cross-organisation read/write path.

    PATCH/DELETE require write access; GET requires read access.
    """
    write = request.method in ("PATCH", "DELETE")
    task = _resolve_task(request, task_id, write=write)
    if task is None:
        return _not_found("Task")
    if request.method == "DELETE":
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    if request.method == "PATCH":
        return _update_task(request, task)

    # Audit superAdmin cross-org read
    maybe_record_cross_org_read(
        request,
        actor=request.user,
        target_obj=task.case,
        target_type="Task",
        target_id=str(task.id),
        target_org_field="owner_org",
    )

    return Response(task_json(task))


def _lookup_task(request: Request, task_id: str) -> Task | None:
    pk = _as_uuid(task_id)
    if pk is None:
        return None
    return (
        Task.objects.select_related("case", "assignee")
        .filter(pk=pk)
        .filter(_case_org_scope(request))
        .first()
    )


def _set_task_fields(task: Task, payload: Mapping[str, Any]) -> Response | list[str]:
    """Apply `InputUpdateTask`'s fields to `task`; shared by `task_detail` and `task/_bulk`."""
    fields: list[str] = []
    if "title" in payload:
        title = str(payload["title"] or "").strip()
        if not title:
            return _bad("title is required", {"title": ["required"]})
        task.title = title[:500]
        fields.append("title")
    for key in ("description", "group"):
        if key in payload:
            setattr(task, key, str(payload[key] or ""))
            fields.append(key)
    if "flag" in payload:
        task.flag = bool(payload["flag"])
        fields.append("flag")
    if "mandatory" in payload:
        task.mandatory = bool(payload["mandatory"])
        fields.append("mandatory")
    if "order" in payload:
        try:
            task.order = int(payload["order"])
        except (TypeError, ValueError):
            return _bad("order must be an integer", {"order": ["must be an integer"]})
        fields.append("order")
    if "status" in payload:
        value = str(payload["status"])
        if value not in _TASK_STATUSES:
            return _bad(
                f"unknown status {payload['status']!r}",
                {"status": [f"must be one of {', '.join(_TASK_STATUSES)}"]},
            )
        task.status = value
        fields.append("status")
    for key, attr in (
        ("dueDate", "due_date"),
        ("startDate", "started_at"),
        ("endDate", "ended_at"),
    ):
        if key not in payload:
            continue
        moment = parse_timestamp(payload[key])
        if moment is None and payload[key] is not None:
            return _bad(f"{key} is not a valid timestamp", {key: ["invalid timestamp"]})
        setattr(task, attr, moment)
        fields.append(attr)
    if "assignee" in payload:
        task.assignee = _resolve_login(payload["assignee"], org=task.case.owner_org)
        fields.append("assignee")
    return fields


@transaction.atomic
def _update_task(request: Request, task: Task) -> Response:
    payload = request.data if isinstance(request.data, dict) else {}
    fields = _set_task_fields(task, payload)
    if isinstance(fields, Response):
        return fields
    if not fields:
        return _bad("No updatable field supplied", {})
    task.save(update_fields=[*fields, "updated_at"])
    return Response(status=status.HTTP_204_NO_CONTENT)
