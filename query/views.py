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
        engine = QueryEngine(payload)
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
    return response
