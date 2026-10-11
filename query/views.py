"""`POST /api/v1/query` — the TheHive Query API view (plan Phase 8 tasks 3-4, ADR-002 §D6).

Thin by design: the step/operator logic lives in `query.engine`; this module owns the wire —
parsing the envelope, choosing the error shape, rendering rows through the **T1 serializers**
(`core.serializers`), applying `includeFields`/`excludeFields`, and setting `X-Total`.

Two wire facts that are easy to get wrong and are pinned here:

* the response is a **bare JSON array**, never an envelope — thehive4py's `find()` calls return
  the parsed body directly and would iterate an envelope's keys;
* the 400 envelope is built inline (as `_not_found`/`_create_case` do in `cases/views.py`) rather
  than raised as DRF's `ValidationError`: `compat.errors` flattens every 400's ``message`` to the
  literal "Bad request", and AC8.3 requires the message to *name* the offending operator.
"""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.decorators import api_view, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request
from rest_framework.response import Response

from alerts.models import Alert
from cases.models import Case, CaseObservable
from core.serializers import alert_json, case_json, observable_json
from identity.audit import maybe_record_cross_org_read

from .engine import QueryEngine, QueryError, Row


def _bad_request(message: str) -> Response:
    return Response(
        {"type": "BadRequest", "message": message},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _render(row: Row) -> dict[str, Any]:
    """One row through the T1 serializer — the query path never invents a second rendering."""
    if isinstance(row, Case):
        return case_json(row)
    if isinstance(row, Alert):
        return alert_json(row)
    if isinstance(row, CaseObservable):
        return observable_json(row)
    raise QueryError(f"Unsupported query result of type {type(row).__name__}")


def _project(payload: dict[str, Any], engine: QueryEngine) -> dict[str, Any]:
    """Apply `includeFields` / `excludeFields` to top-level keys.

    `includeFields` present **wins** — documented 5.8.0 behaviour: a client asking for
    ``["title"]`` gets only ``title``, even if it also sent an ``excludeFields`` list.
    """
    if engine.include_fields is not None:
        return {key: value for key, value in payload.items() if key in engine.include_fields}
    if engine.exclude_fields is not None:
        return {key: value for key, value in payload.items() if key not in engine.exclude_fields}
    return payload


def _audit_query_cross_org_read(request: Request, engine: QueryEngine, result: list[Row]) -> None:
    """Audit superAdmin cross-org reads from the query API.

    For each resource type in the result that belongs to a different org than the superAdmin's,
    record a `cross_org_read` audit entry. This mirrors the detail-view audit logic but at the
    query level since the query API can return multiple resource types in one request.
    """

    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return
    if getattr(user, "role", "") != "superAdmin":
        return
    actor_org = getattr(user, "org", None)
    if not actor_org:
        return

    # Group results by resource type and target org
    from collections import defaultdict

    cross_org_by_type: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for row in result:
        if isinstance(row, Case):
            target_org = getattr(row, "owner_org", None)
            if target_org and target_org.id != actor_org.id:
                cross_org_by_type["Case"][str(target_org.id)].append(str(row.id))
        elif isinstance(row, Alert):
            target_org = getattr(row, "owner_org", None)
            if target_org and target_org.id != actor_org.id:
                cross_org_by_type["Alert"][str(target_org.id)].append(str(row.id))
        elif isinstance(row, CaseObservable):
            target_org = getattr(row.observable, "owner_org", None) if row.observable else None
            if target_org and target_org.id != actor_org.id:
                cross_org_by_type["Observable"][str(target_org.id)].append(str(row.observable_id))

    # Record one audit entry per (resource_type, target_org) combination
    for target_type, org_map in cross_org_by_type.items():
        for target_org_id, target_ids in org_map.items():
            from identity.models import AuditLog, Organisation

            try:
                target_org = Organisation.objects.get(pk=target_org_id)
            except Organisation.DoesNotExist:
                continue
            # Use the first target ID as representative (query can return many)
            representative_id = target_ids[0]
            maybe_record_cross_org_read(
                request,
                actor=user,
                target_obj=type(
                    "obj", (), {"owner_org": target_org}
                )(),  # dummy object with owner_org
                target_type=target_type,
                target_id=representative_id,
                target_org_field="owner_org",
            )
            # Update the action to indicate it was via query API
            log = (
                AuditLog.objects.filter(
                    actor=user,
                    target_org=target_org,
                    target_type=target_type,
                    action="cross_org_read",
                )
                .order_by("-created_at")
                .first()
            )
            if log:
                log.action = "cross_org_read_query"
                log.target_id = (
                    f"query:{','.join(target_ids[:5])}{'...' if len(target_ids) > 5 else ''}"
                )
                log.save(update_fields=["action", "target_id"])


@api_view(["POST"])
@renderer_classes([JSONRenderer])
def query_view(request: Request) -> Response:
    """`POST /api/v1/query` — run a chained `_name` query; returns a bare array or an int.

    The legacy ``?name=`` query parameter thehive4py sends (``?name=cases``, ``?name=cases.count``)
    is accepted and ignored: DRF never reads it, which is exactly the ADR D12 contract — it named a
    frontend-internal variant, not a selector.
    """
    payload = request.data
    if not isinstance(payload, dict):
        return _bad_request('Query body must be a JSON object of the form {"query": [...]}')

    try:
        engine = QueryEngine(payload, user=request.user)
        result = engine.run()
    except QueryError as exc:
        return _bad_request(str(exc))

    if isinstance(result, int):
        # `count` answers a bare integer, not a one-element array (thehive4py parses it as int).
        return Response(result)

    body = [_project(_render(row), engine) for row in result]
    response = Response(body)
    if engine.total is not None:
        # The count of the *pre-page* set: X-Total spans all pages, not just this slice.
        response["X-Total"] = str(engine.total)

    # Audit superAdmin cross-org reads for list/detail query results
    if isinstance(result, list):
        _audit_query_cross_org_read(request, engine, result)

    return response
