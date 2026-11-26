"""Shared benchmark helpers for Amalthea Postgres performance measurement.

Both benchmarks measure the exact SQL Django emits: count queries are captured from
``queryset.count()`` and page queries from ``list(queryset[start:stop])``.  The same
captured SQL is then sent through ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`` so the
planner evidence and the timing evidence cannot drift apart.

The output shape matches the original observable COUNT benchmark: ``kind``,
``median_ms``, ``min_ms``, ``name``, ``node_types``, ``p95_ms``, ``plan``,
``runs_ms``, ``sql``, and ``explain_execution_time_ms``.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections.abc import Callable
from typing import Any

from django.db import connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext


def _timings(run: Callable[[], Any], repeat: int) -> dict[str, Any]:
    """Wall-clock timings for ``run`` executed ``repeat`` times after one warm-up."""
    run()  # warm caches/planner state; excluded from the reported samples.
    runs_ms: list[float] = []
    for _ in range(max(repeat, 1)):
        start = time.perf_counter()
        run()
        runs_ms.append((time.perf_counter() - start) * 1000.0)
    ordered = sorted(runs_ms)
    return {
        "runs_ms": runs_ms,
        "median_ms": statistics.median(ordered),
        "min_ms": ordered[0],
        "p95_ms": ordered[max(math.ceil(0.95 * len(ordered)) - 1, 0)],
    }


def _node_types(plan: Any) -> list[str]:
    """Flatten ``Node Type`` values from an EXPLAIN JSON plan."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        node_type = node.get("Node Type") or node.get("type")
        if isinstance(node_type, str):
            found.append(node_type)
        for value in node.values():
            walk(value)

    walk(plan)
    return found


def _explain(sql: str) -> dict[str, Any]:
    """Run EXPLAIN ANALYZE on already-interpolated benchmark SQL."""
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}")
        row = cursor.fetchone()
    execution_ms = (time.perf_counter() - started) * 1000.0
    raw = row[0] if row else None
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, str):
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            plan = {"Node Type": "Unparseable plan", "Raw": raw}
    elif isinstance(raw, dict):
        plan = raw
    else:
        plan = {"Node Type": "N/A"}
    return {
        "sql": sql,
        "plan": plan,
        "node_types": _node_types(plan),
        "explain_execution_time_ms": execution_ms,
    }


def _capture_sql(run: Callable[[], Any]) -> str:
    """Execute ``run`` once and return the exact SQL Django issued."""
    with CaptureQueriesContext(connection) as captured:
        run()
    if not captured.captured_queries:
        raise RuntimeError("benchmark query did not hit the database")
    return str(captured.captured_queries[0]["sql"])


def measure_count(name: str, queryset: QuerySet[Any], repeat: int) -> dict[str, Any]:
    """Measure one COUNT query shape and capture its EXPLAIN evidence."""

    def run() -> int:
        return queryset.count()

    return {
        "kind": "count",
        "name": name,
        **_timings(run, repeat),
        **_explain(_capture_sql(run)),
    }


def measure_page(
    name: str,
    queryset: QuerySet[Any],
    repeat: int,
    page_size: int,
    page_number: int,
) -> dict[str, Any]:
    """Measure one LIMIT/OFFSET page shape and capture its EXPLAIN evidence."""
    start = (page_number - 1) * page_size

    def run() -> list[Any]:
        return list(queryset[start : start + page_size])

    return {
        "kind": "page",
        "name": name,
        **_timings(run, repeat),
        **_explain(_capture_sql(run)),
    }
