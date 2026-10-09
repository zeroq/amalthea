"""Shared per-item bulk PATCH helper behind the T2 `_bulk` endpoints (plan §6-P4).

TheHive's `_bulk` patches are "one field set applied to many ids" (thehive4py
`InputBulkUpdateCase` = the update body plus `ids`). The one wire property that matters is the
**failure boundary** (AC6.1-P4-a): each item commits or fails on its own, so a bad id cannot roll
back the batch, and the response reports a per-item status.

`apply(obj, body)` mutates `obj` and returns one of:

* a DRF `Response` -> this item failed; its `message` is copied into the item's report;
* `None` -> nothing updatable arrived for this row (a 400 for that item);
* a list of touched column names -> success. The list may be empty when the write was a side
  effect (a tag link); the caller saves `update_fields=["updated_at"]` in that case.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from django.db import DataError, IntegrityError, transaction
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

Resolver = Callable[[Request, str], Any | None]
Applier = Callable[[Any, Mapping[str, Any]], Response | list[str] | None]


def bulk_patch(
    request: Request,
    *,
    ids_key: str,
    resolve: Resolver,
    apply: Applier,
) -> Response:
    """Apply one field set to every id in `payload[ids_key]`, reporting per-item status."""
    payload = request.data if isinstance(request.data, dict) else {}
    ids = payload.get(ids_key)
    if not isinstance(ids, list) or not ids:
        return Response(
            {
                "type": "BadRequest",
                "message": f"{ids_key} is required",
                "fields": {ids_key: ["required"]},
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    body = {key: value for key, value in payload.items() if key != ids_key}

    results: list[dict[str, Any]] = []
    updated = 0
    for raw in ids:
        obj = resolve(request, str(raw))
        if obj is None:
            results.append({"id": str(raw), "status": 404, "message": "not found"})
            continue
        try:
            with transaction.atomic():
                outcome = apply(obj, body)
                if isinstance(outcome, Response):
                    message = str(getattr(outcome, "data", {}).get("message", "invalid"))
                    results.append({"id": str(obj.id), "status": 400, "message": message})
                    continue
                if outcome is None:
                    results.append(
                        {
                            "id": str(obj.id),
                            "status": 400,
                            "message": "No updatable field supplied",
                        }
                    )
                    continue
                if outcome:
                    obj.save(update_fields=[*outcome, "updated_at"])
                else:
                    obj.save(update_fields=["updated_at"])
        except (IntegrityError, DataError, ValueError, TypeError) as exc:
            results.append({"id": str(raw), "status": 400, "message": str(exc)[:200]})
            continue
        results.append({"id": str(obj.id), "status": 200})
        updated += 1

    return Response(
        {"results": results, "updated": updated, "failed": len(results) - updated},
        status=status.HTTP_200_OK,
    )


__all__ = ["bulk_patch"]
