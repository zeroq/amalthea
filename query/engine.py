"""`POST /api/v1/query` — the TheHive query DSL engine (plan §Phase 8, ADR-002 §D6).

The wire contract, pinned from thehive4py 2.1.0's own request builders and the recorded 5.8.0
docs (BRIEF-2026-10-06):

* a request body is ``{"query": [step, ...], "includeFields": [...], "excludeFields": [...]}``
  — an object, never a bare array;
* steps run in order, threading one result set, and the response is a **bare JSON array** of the
  T1 serializers' objects (``core.serializers``). The query path deliberately renders through
  those same functions: a second rendering of the same entity is how the T1 endpoints and the
  query endpoint would drift apart;
* anything the engine does not implement — an unknown ``_name`` step, an unknown filter
  operator, a field that is not on the entity's whitelist — answers ``400 BadRequest`` **naming
  the offending token**, never a silent empty array and never a no-op (AC8.3).

Shape decisions worth knowing before changing anything here:

* **One filter compiler, not two.** A result set is a list of *branches* (one per entity kind —
  ``listCase`` keeps one, ``listAny`` keeps three). Filters compile to ORM ``Q`` objects per
  branch. When a sort or a page forces a cross-kind materialisation, a later filter re-selects
  the surviving pks per kind instead of growing a Python-side evaluator, so the operator table
  exists exactly once and an ORM/Python semantic split cannot open up.
* **Fields absent from a branch are "cannot match", not "error".** ``severity`` is a valid
  filter field on cases and alerts but observables have no severity; in a ``listAny`` query the
  observable branch therefore drops out of ``{"_eq": {"_field": "severity", ...}}`` (and drops
  *in* under ``_not``, which is the correct reading of "this row does not have severity 3").
  The 400 for an unknown field fires when the field is on **no** active whitelist.
* **Timestamps are Unix epoch ms on the wire** and ``datetime`` in the ORM; ``_between`` is
  ``_from`` inclusive / ``_to`` exclusive, which is what makes adjacent pages disjoint (AC8.2).
* **Deterministic paging:** every sort appends an ``id`` tie-break, and the start steps order by
  ``id`` so even an unsorted ``page`` is stable across runs.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from django.db.models import Count, Q, QuerySet

from alerts.escalation import link_case_from_identifier
from alerts.models import Alert
from cases.models import Case, CaseObservable

KIND_CASE = "case"
KIND_ALERT = "alert"
KIND_OBSERVABLE = "observable"

Row = Case | Alert | CaseObservable
AnyQS = QuerySet[Case] | QuerySet[Alert] | QuerySet[CaseObservable]

# A row that lacks the field the condition is about: false under any leaf operator, and
# therefore true under `_not` — the three-valued reading a SQL NULL never gives you.
_NO_MATCH = Q(pk__isnull=True)


class QueryError(Exception):
    """A query the engine refuses to answer; the view turns this into a named 400.

    Deliberately *not* ``rest_framework.exceptions.ValidationError``: DRF's exception handler
    flattens every 400's ``message`` to the literal string "Bad request" and pushes the detail
    into ``fields``, and AC8.3 requires the response to *name* the operator. The T1 views
    already build the ``compat`` envelope inline for exactly this reason (``_not_found``,
    ``_create_case``); ``query/views.py`` mirrors them.
    """


@dataclass(frozen=True)
class _FieldSpec:
    """One wire field's path into the ORM (or its constant value, or its aggregate)."""

    # ORM path (`status__value`, `created_at`, ...). Empty when the field is a constant.
    path: str = ""
    # The value arrives as Unix epoch ms and must become an aware datetime for the ORM.
    is_datetime: bool = False
    # Related name to `Count()` into `path` when the field is referenced (alertCount, caseCount).
    counter: str | None = None
    # Constant per entity — `_type` is "case"/"alert"/"observable", not a column.
    literal: str | None = None


def _case_fields() -> dict[str, _FieldSpec]:
    """Every field `case_json` emits, plus the `_createdAt`/`_updatedAt` spellings TheHive
    filters and sorts with."""
    return {
        "_id": _FieldSpec("id"),
        "id": _FieldSpec("id"),
        "_type": _FieldSpec(literal=KIND_CASE),
        "number": _FieldSpec("number"),
        "title": _FieldSpec("title"),
        "description": _FieldSpec("description"),
        "severity": _FieldSpec("severity"),
        "status": _FieldSpec("status__value"),
        "tlp": _FieldSpec("tlp"),
        "pap": _FieldSpec("pap"),
        "flag": _FieldSpec("flag"),
        "summary": _FieldSpec("summary"),
        "assignee": _FieldSpec("assignee__login"),
        "startDate": _FieldSpec("start_date", is_datetime=True),
        "endDate": _FieldSpec("end_date", is_datetime=True),
        "closedDate": _FieldSpec("closed_date", is_datetime=True),
        "createdAt": _FieldSpec("created_at", is_datetime=True),
        "updatedAt": _FieldSpec("updated_at", is_datetime=True),
        "_createdAt": _FieldSpec("created_at", is_datetime=True),
        "_updatedAt": _FieldSpec("updated_at", is_datetime=True),
        "alertCount": _FieldSpec("alert_count", counter="alerts"),
    }


def _alert_fields() -> dict[str, _FieldSpec]:
    """Every field `alert_json` emits (its `raw_payload` has its own `/raw` endpoint, so the
    whitelist has no business letting a query scan it)."""
    return {
        "_id": _FieldSpec("id"),
        "id": _FieldSpec("id"),
        "_type": _FieldSpec(literal=KIND_ALERT),
        "type": _FieldSpec("type"),
        "source": _FieldSpec("source"),
        "sourceRef": _FieldSpec("source_ref"),
        "title": _FieldSpec("title"),
        "description": _FieldSpec("description"),
        "summary": _FieldSpec("summary"),
        "severity": _FieldSpec("severity"),
        "status": _FieldSpec("status__value"),
        "date": _FieldSpec("date", is_datetime=True),
        "tlp": _FieldSpec("tlp"),
        "pap": _FieldSpec("pap"),
        "flag": _FieldSpec("flag"),
        "follow": _FieldSpec("follow"),
        "assignee": _FieldSpec("assignee__login"),
        "caseId": _FieldSpec("case_id"),
        "correlationKey": _FieldSpec("correlation_key"),
        "ingestionSource": _FieldSpec("ingestion_source_id"),
        "ingestionWarnings": _FieldSpec("ingestion_warnings"),
        "createdAt": _FieldSpec("created_at", is_datetime=True),
        "updatedAt": _FieldSpec("updated_at", is_datetime=True),
        "_createdAt": _FieldSpec("created_at", is_datetime=True),
        "_updatedAt": _FieldSpec("updated_at", is_datetime=True),
    }


def _observable_fields() -> dict[str, _FieldSpec]:
    """Every field `observable_json` emits. The entity is the *link* row (one per case), so
    `_id` resolves to the `Observable` the serializer reports and `addedAt` to the link."""
    return {
        "_id": _FieldSpec("observable_id"),
        "id": _FieldSpec("observable_id"),
        "_type": _FieldSpec(literal=KIND_OBSERVABLE),
        "dataType": _FieldSpec("observable__data_type__name"),
        "data": _FieldSpec("observable__data"),
        "normalizedData": _FieldSpec("observable__normalized_data"),
        "tlp": _FieldSpec("observable__tlp"),
        "pap": _FieldSpec("observable__pap"),
        "ioc": _FieldSpec("observable__ioc"),
        "sighted": _FieldSpec("observable__sighted"),
        "enrichmentData": _FieldSpec("observable__enrichment_data"),
        "caseCount": _FieldSpec("case_link_count", counter="observable__case_observables"),
        "caseId": _FieldSpec("case_id"),
        "addedAt": _FieldSpec("created_at", is_datetime=True),
        "_createdAt": _FieldSpec("created_at", is_datetime=True),
    }


_SPEC_TABLES: dict[str, dict[str, _FieldSpec]] = {
    KIND_CASE: _case_fields(),
    KIND_ALERT: _alert_fields(),
    KIND_OBSERVABLE: _observable_fields(),
}

_LEAF_OPS = frozenset({"_eq", "_ne", "_gt", "_gte", "_lt", "_lte", "_between", "_in", "_like"})
_RANGE_OPS = {"_gt": "__gt", "_gte": "__gte", "_lt": "__lt", "_lte": "__lte"}
_START_STEPS = frozenset({"listCase", "listAlert", "listObservable", "listAny", "getCase"})


@dataclass
class _Branch:
    """One entity kind's not-yet-materialised result set."""

    kind: str
    qs: AnyQS
    # Aggregate aliases already applied, so a filter and a later sort can both ask for
    # `alert_count` without Django rejecting the duplicate annotation.
    annotations: set[str] = field(default_factory=set)

    def annotate(self, specs: Sequence[_FieldSpec]) -> None:
        pending = [s for s in specs if s.counter and s.path not in self.annotations]
        if not pending:
            return
        self.qs = _annotate_qs(self.qs, pending)
        self.annotations.update(s.path for s in pending)


def _annotate_qs(qs: AnyQS, specs: Sequence[_FieldSpec]) -> AnyQS:
    """Apply each `Count` alias at most once (see `_Branch.annotations`)."""
    seen: set[str] = set()
    for spec in specs:
        if spec.counter and spec.path not in seen:
            qs = qs.annotate(**{spec.path: Count(spec.counter, distinct=True)})
            seen.add(spec.path)
    return qs


def _is_int(value: Any) -> bool:
    # `isinstance(True, int)` is True; a boolean is not a page offset or an epoch-ms date.
    return isinstance(value, int) and not isinstance(value, bool)


def _coerce(value: Any, spec: _FieldSpec | None) -> Any:
    """Wire value → ORM value: epoch-ms ints become aware datetimes on datetime fields."""
    if (
        spec is not None
        and spec.is_datetime
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    return value


def _kind_of(row: Row) -> str:
    if isinstance(row, Case):
        return KIND_CASE
    if isinstance(row, Alert):
        return KIND_ALERT
    return KIND_OBSERVABLE


def _base_qs(kind: str) -> AnyQS:
    """An unfiltered queryset for the kind — used only to re-check pks after materialisation."""
    if kind == KIND_CASE:
        return Case.objects.all()
    if kind == KIND_ALERT:
        return Alert.objects.all()
    return CaseObservable.objects.all()


def _resolve(kind: str, field: str, allowed: frozenset[str], context: str) -> _FieldSpec | None:
    """Look a wire field up in the entity's whitelist.

    Returns `None` when *this* kind has no such field (a `listAny` branch dropping out, see the
    module docstring); raises when *no* active kind has it — that is the AC8.3 "name the field"
    rejection, never a silently empty answer.
    """
    if field not in allowed:
        raise QueryError(f"Unsupported {context} field '{field}'")
    return _SPEC_TABLES[kind].get(field)


def _require(body: Mapping[str, Any], key: str, op: str) -> Any:
    if key not in body:
        raise QueryError(f"Malformed filter '{op}': '{key}' is required")
    return body[key]


def _literal_q(op: str, spec: _FieldSpec, body: Mapping[str, Any]) -> Q:
    """Evaluate a constant field (`_type`) against a literal, in Python.

    There is no column to ask, so the predicate is decided here and folds to `Q()` (everyone
    matches) or `_NO_MATCH` (nobody does) — still a real answer, not a skipped condition.
    """
    value = spec.literal or ""
    if op == "_eq":
        return Q() if value == _require(body, "_value", op) else _NO_MATCH
    if op == "_ne":
        return _NO_MATCH if value == _require(body, "_value", op) else Q()
    if op == "_in":
        values = _require(body, "_values", op)
        if not isinstance(values, list):
            raise QueryError(f"Malformed filter '{op}': '_values' must be an array")
        return Q() if value in values else _NO_MATCH
    if op == "_like":
        needle = _require(body, "_value", op)
        if not isinstance(needle, str):
            raise QueryError(f"Malformed filter '{op}': '_value' must be a string")
        return Q() if needle.lower() in value.lower() else _NO_MATCH
    if op == "_between":
        lo = body.get("_from")
        hi = body.get("_to")
        if lo is None and hi is None:
            raise QueryError(f"Malformed filter '{op}': '_from' or '_to' is required")
        try:
            above = lo is None or value >= lo
            below = hi is None or value < hi  # _to is exclusive, as documented in 5.8.0
        except TypeError:
            raise QueryError(
                f"Malformed filter '{op}': cannot compare '{value}' with the given bounds"
            ) from None
        return Q() if above and below else _NO_MATCH
    # The ordering operators compare two strings or not at all.
    try:
        matches = {
            "_gt": value > _require(body, "_value", op),
            "_gte": value >= _require(body, "_value", op),
            "_lt": value < _require(body, "_value", op),
            "_lte": value <= _require(body, "_value", op),
        }[op]
    except TypeError:
        raise QueryError(
            f"Malformed filter '{op}': cannot compare '{value}' with the given '_value'"
        ) from None
    return Q() if matches else _NO_MATCH


def _leaf_q(
    op: str,
    body: Mapping[str, Any],
    spec: _FieldSpec | None,
    out: list[_FieldSpec],
) -> Q:
    """One comparison operator. `spec is None` means this kind lacks the field → cannot match."""
    if op == "_between":
        lo = body.get("_from")
        hi = body.get("_to")
        if lo is None and hi is None:
            raise QueryError(f"Malformed filter '{op}': '_from' or '_to' is required")
    elif op == "_in":
        if not isinstance(_require(body, "_values", op), list):
            raise QueryError(f"Malformed filter '{op}': '_values' must be an array")
    else:
        _require(body, "_value", op)

    if spec is None:
        return _NO_MATCH
    if spec.counter:
        out.append(spec)
    if spec.literal is not None:
        return _literal_q(op, spec, body)

    if op == "_eq":
        return Q(**{spec.path: _coerce(body["_value"], spec)})
    if op == "_ne":
        return ~Q(**{spec.path: _coerce(body["_value"], spec)})
    if op in _RANGE_OPS:
        return Q(**{spec.path + _RANGE_OPS[op]: _coerce(body["_value"], spec)})
    if op == "_like":
        needle = body["_value"]
        if not isinstance(needle, str):
            raise QueryError(f"Malformed filter '{op}': '_value' must be a string")
        return Q(**{spec.path + "__icontains": needle})
    if op == "_in":
        values = body["_values"]
        assert isinstance(values, list)  # checked above
        return Q(**{spec.path + "__in": [_coerce(v, spec) for v in values]})

    # _between: `_from` inclusive, `_to` exclusive — adjacent ranges tile without overlap,
    # which is what AC8.2's disjoint paging depends on.
    query = Q()
    if body.get("_from") is not None:
        query &= Q(**{spec.path + "__gte": _coerce(body["_from"], spec)})
    if body.get("_to") is not None:
        query &= Q(**{spec.path + "__lt": _coerce(body["_to"], spec)})
    return query


def _build_filter(
    body: Mapping[str, Any],
    *,
    kind: str,
    allowed: frozenset[str],
    out: list[_FieldSpec],
    context: str = "filter",
) -> Q:
    """Compile one filter object (possibly nested through `_and`/`_or`/`_not`) to a `Q`.

    `out` collects the aggregate specs the condition references so the caller can annotate
    before filtering. An unknown operator key raises *before* touching the queryset: a
    half-applied filter is a wrong answer with a 200 on it.
    """
    if not body:
        raise QueryError("Unsupported filter: the filter object is empty")
    query = Q()
    for key, value in body.items():
        if key == "_and" or key == "_or":
            if not isinstance(value, list):
                raise QueryError(f"Malformed filter '{key}': expected an array of filters")
            combined = Q()
            for child in value:
                if not isinstance(child, Mapping):
                    raise QueryError(f"Malformed filter '{key}': expected an array of filters")
                sub = _build_filter(child, kind=kind, allowed=allowed, out=out, context=context)
                combined = combined & sub if key == "_and" else combined | sub
            query &= combined
        elif key == "_not":
            if not isinstance(value, Mapping):
                raise QueryError("Malformed filter '_not': expected a filter object")
            query &= ~_build_filter(value, kind=kind, allowed=allowed, out=out, context=context)
        elif key == "_has":
            field = value
            if not isinstance(field, str) or not field:
                raise QueryError("Malformed filter '_has': expected a bare field name")
            spec = _resolve(kind, field, allowed, context)
            if spec is None:
                query &= _NO_MATCH
            elif spec.counter:
                out.append(spec)
                query &= Q(**{spec.path + "__isnull": False})
            elif spec.literal is not None:
                query &= Q()
            else:
                query &= Q(**{spec.path + "__isnull": False})
        elif key in _LEAF_OPS:
            if not isinstance(value, Mapping):
                raise QueryError(f"Malformed filter '{key}': expected an object with '_field'")
            field = value.get("_field")
            if not isinstance(field, str) or not field:
                raise QueryError(f"Malformed filter '{key}': '_field' is required")
            spec = _resolve(kind, field, allowed, context)
            query &= _leaf_q(key, value, spec, out)
        else:
            raise QueryError(f"Unsupported filter '{key}'")
    return query


def _getattr_path(row: Row, path: str) -> Any:
    current: Any = row
    for part in path.split("__"):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


def _row_value(row: Row, field: str) -> Any:
    """Sort key for one materialised row. Missing field on this kind → None (sorts as a value).

    Annotated aggregates cannot be read back off the instance, so a counter field is counted
    from the related manager — which the start steps prefetch, so this stays off the N+1 path.
    """
    spec = _SPEC_TABLES[_kind_of(row)].get(field)
    if spec is None:
        return None
    if spec.literal is not None:
        return spec.literal
    if spec.counter is not None:
        manager: Any = row
        for part in spec.counter.split("__"):
            manager = getattr(manager, part, None)
            if manager is None:
                return 0
        return len(manager.all())
    return _getattr_path(row, spec.path)


def _sort_rows(rows: list[Row], terms: Sequence[tuple[str, str]]) -> list[Row]:
    """Cross-kind sort with the same `_id` tie-break the ORM path appends (AC8.2).

    NULL sorts first ascending, matching SQLite — PostgreSQL would say NULLS LAST, so the
    suite's paging assertions use non-nullable fields where the two disagree.
    """

    def compare(left: Row, right: Row) -> int:
        for field_name, direction in terms:
            first = _row_value(left, field_name)
            second = _row_value(right, field_name)
            if first == second:
                continue
            if first is None:
                return -1 if direction == "asc" else 1
            if second is None:
                return 1 if direction == "asc" else -1
            try:
                ordered = first < second
            except TypeError:
                continue  # incomparable values across kinds: fall through to the id tie-break
            if ordered:
                return -1 if direction == "asc" else 1
            return 1 if direction == "asc" else -1
        # Stable tie-break, by `_id`, exactly as the ORM path appends `order_by("id")`.
        return (str(left.id) > str(right.id)) - (str(left.id) < str(right.id))

    return sorted(rows, key=functools.cmp_to_key(compare))


class QueryEngine:
    """Execute one `{"query": [...], "includeFields": ..., "excludeFields": ...}` request."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._payload = payload
        self._steps: list[Any] = []
        self._branches: list[_Branch] = []
        self._rows: list[Row] | None = None
        self._kinds: frozenset[str] = frozenset()
        self._count_result: int | None = None
        # Set only by a `page` step whose `extraData` asked for "total"; becomes `X-Total`.
        self.total: int | None = None
        self.include_fields: list[str] | None = None
        self.exclude_fields: list[str] | None = None

    # -- entry point -----------------------------------------------------

    def run(self) -> int | list[Row]:
        steps = self._payload.get("query")
        if not isinstance(steps, list) or not steps:
            raise QueryError('Query body must contain a non-empty "query" array of steps')
        self._steps = steps
        self.include_fields = self._string_list("includeFields")
        self.exclude_fields = self._string_list("excludeFields")

        for index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                raise QueryError("Invalid query step: every step must be an object")
            name = step.get("_name")
            if not isinstance(name, str) or not name:
                raise QueryError("Invalid query step: missing '_name'")
            if name == "count" and index != len(steps) - 1:
                raise QueryError("Unsupported query operation 'count': it must be the final step")
            if name in _START_STEPS:
                self._start(name, step)
            elif name == "filter":
                self._require_anchored(name)
                self._filter(step)
            elif name == "sort":
                self._require_anchored(name)
                self._sort(step)
            elif name == "page":
                self._require_anchored(name)
                self._page(step)
            elif name == "count":
                self._require_anchored(name)
                self._count_result = self._count_rows()
            else:
                raise QueryError(f"Unsupported query operation '{name}'")

        if self._count_result is not None:
            return self._count_result
        # A bare list/get step (no filter/sort/page) never set `_rows`; materialize
        # it now so we return every row instead of an empty result set.
        return self._rows if self._rows is not None else self._materialize()

    # -- steps -----------------------------------------------------------

    def _string_list(self, key: str) -> list[str] | None:
        value = self._payload.get(key)
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise QueryError(f"'{key}' must be an array of field names")
        return value

    def _require_anchored(self, name: str) -> None:
        if not self._kinds:
            raise QueryError(f"Unsupported query operation '{name}': no list step precedes it")

    def _start(self, name: str, step: Mapping[str, Any]) -> None:
        """A start step resets the result set: whatever ran before it is replaced, not merged."""
        self._rows = None
        self._count_result = None
        self.total = None
        if name == "listCase":
            self._set([_Branch(KIND_CASE, _case_qs())])
        elif name == "listAlert":
            self._set([_Branch(KIND_ALERT, _alert_qs())])
        elif name == "listObservable":
            self._set([_Branch(KIND_OBSERVABLE, _observable_qs())])
        elif name == "listAny":
            # Three branches, not one UNION: the kinds share no columns, and every later step
            # already works per branch.
            self._set(
                [
                    _Branch(KIND_CASE, _case_qs()),
                    _Branch(KIND_ALERT, _alert_qs()),
                    _Branch(KIND_OBSERVABLE, _observable_qs()),
                ]
            )
        else:
            self._set([self._get_case(step)])

    def _set(self, branches: list[_Branch]) -> None:
        self._branches = branches
        self._kinds = frozenset(branch.kind for branch in branches)

    def _get_case(self, step: Mapping[str, Any]) -> _Branch:
        """`getCase` with `idOrName` = `~<uuid>` (TheHive prefix), a bare UUID, or a number."""
        raw = step.get("idOrName", step.get("id"))
        if raw is None:
            raise QueryError("Unsupported query operation 'getCase': 'idOrName' is required")
        identifier = str(raw).lstrip("~")
        try:
            case = link_case_from_identifier(identifier)
        except Case.DoesNotExist:
            # A miss is an empty result set, not an error: the step threads "zero cases", and
            # every later step (count included) then answers honestly.
            return _Branch(KIND_CASE, Case.objects.none())
        return _Branch(KIND_CASE, _case_qs().filter(pk=case.pk))

    def _allowed(self) -> frozenset[str]:
        """Union of the active kinds' whitelists — the AC8.3 field boundary."""
        allowed: set[str] = set()
        for kind in self._kinds:
            allowed.update(_SPEC_TABLES[kind])
        return frozenset(allowed)

    def _filter(self, step: Mapping[str, Any]) -> None:
        body = {key: value for key, value in step.items() if key != "_name"}
        allowed = self._allowed()
        if self._rows is not None:
            self._filter_rows(body, allowed)
            return
        for branch in self._branches:
            specs: list[_FieldSpec] = []
            query = _build_filter(body, kind=branch.kind, allowed=allowed, out=specs)
            branch.annotate(specs)
            branch.qs = branch.qs.filter(query)

    def _filter_rows(self, body: Mapping[str, Any], allowed: frozenset[str]) -> None:
        """Filter a materialised result set by re-selecting pks per kind.

        The rows that survive keep their original objects and their positions, so a filter
        arriving *after* a sort narrows the ordered set instead of reordering it — and the
        operator table stays the ORM one.
        """
        rows = self._rows or []
        keep: set[int] = set()
        by_kind: dict[str, list[tuple[int, Row]]] = {}
        for index, row in enumerate(rows):
            by_kind.setdefault(_kind_of(row), []).append((index, row))
        for kind, items in by_kind.items():
            specs: list[_FieldSpec] = []
            query = _build_filter(body, kind=kind, allowed=allowed, out=specs)
            candidates = _annotate_qs(_base_qs(kind), specs).filter(
                pk__in=[row.pk for _, row in items]
            )
            matched = set(candidates.filter(query).values_list("pk", flat=True))
            keep.update(index for index, row in items if row.pk in matched)
        self._rows = [row for index, row in enumerate(rows) if index in keep]

    def _sort(self, step: Mapping[str, Any]) -> None:
        raw = step.get("_fields")
        if not isinstance(raw, list) or not raw:
            raise QueryError(
                "Unsupported query operation 'sort': '_fields' must be a non-empty array"
            )
        allowed = self._allowed()
        terms: list[tuple[str, str]] = []
        for entry in raw:
            if not isinstance(entry, Mapping) or not entry:
                raise QueryError("Malformed sort step: each '_fields' entry must be an object")
            for field_name, direction in entry.items():
                if direction != "asc" and direction != "desc":
                    raise QueryError(
                        f"Unsupported sort direction {direction!r} for field '{field_name}'"
                    )
                if field_name not in allowed:
                    raise QueryError(f"Unsupported sort field '{field_name}'")
                terms.append((field_name, direction))

        if self._rows is not None:
            self._rows = _sort_rows(self._rows, terms)
            return
        if len(self._branches) > 1:
            # Cross-kind order cannot be one ORDER BY across three tables: materialise, then
            # sort with the same tie-break. Later steps keep working (see `_filter_rows`).
            self._rows = _sort_rows(self._materialize(), terms)
            return

        branch = self._branches[0]
        specs = [_resolve(branch.kind, field_name, allowed, "sort") for field_name, _ in terms]
        branch.annotate([spec for spec in specs if spec is not None])
        order: list[str] = []
        for (_field_name, direction), spec in zip(terms, specs, strict=True):
            # A constant (`_type`) orders nothing within one kind; a kind without the field
            # likewise — both are handled by the materialised path above, this is belt-and-braces.
            if spec is None or spec.literal is not None:
                continue
            order.append(("-" if direction == "desc" else "") + spec.path)
        # The `_id` tie-break: equal keys must page identically on every run (AC8.2).
        branch.qs = branch.qs.order_by(*(order or ["id"]), "id")

    def _page(self, step: Mapping[str, Any]) -> None:
        start = step.get("from", 0)
        end = step.get("to")
        if not _is_int(start) or int(start) < 0:
            raise QueryError("Malformed page step: 'from' must be a non-negative integer")
        if end is not None and (not _is_int(end) or int(end) < 0):
            raise QueryError("Malformed page step: 'to' must be a non-negative integer")
        extra = step.get("extraData")
        if extra is None:
            extra = []
        if not isinstance(extra, list):
            raise QueryError("Malformed page step: 'extraData' must be an array")
        want_total = "total" in extra

        total: int | None = None
        if self._rows is None and len(self._branches) == 1:
            # Single kind: let the database do the offset/limit (plan G5 — no full materialise
            # on the common path), counting *before* the slice so X-Total spans all pages.
            branch = self._branches[0]
            if want_total:
                total = branch.qs.count()
            page_rows: list[Row] = []
            page_rows.extend(branch.qs[int(start) : None if end is None else int(end)])
            self._rows = page_rows
            self._branches = []
        else:
            rows = self._rows if self._rows is not None else self._materialize()
            if want_total:
                total = len(rows)
            self._rows = rows[int(start) : None if end is None else int(end)]
        if want_total:
            self.total = total

    def _count_rows(self) -> int:
        if self._rows is not None:
            return len(self._rows)
        return sum(branch.qs.count() for branch in self._branches)

    def _materialize(self) -> list[Row]:
        """Evaluate every branch into one row list and clear the branches.

        Returns the rows rather than mutating-only so call sites never need an `assert` after
        the call — mypy cannot see through a method that narrows an attribute it already
        pinned to `None`.
        """
        if self._rows is not None:
            return self._rows
        rows: list[Row] = []
        for branch in self._branches:
            # Start steps order by `id`, so an unmaterialised multi-kind set has a stable
            # order to materialise into.
            rows.extend(branch.qs)
        self._rows = rows
        self._branches = []
        return rows


def _case_qs() -> QuerySet[Case]:
    # select_related: case_json reads status.value and assignee.login per row.
    # prefetch_related("alerts"): case_json counts alerts per row — the prefetch is what turns
    # that N+1 into one extra query (plan G5, measured in tests/conformance).
    return (
        Case.objects.select_related("status", "assignee").prefetch_related("alerts").order_by("id")
    )


def _alert_qs() -> QuerySet[Alert]:
    return Alert.objects.select_related("status", "assignee").order_by("id")


def _observable_qs() -> QuerySet[CaseObservable]:
    # caseCount counts the observable's links — prefetched for the same reason as alertCount.
    return (
        CaseObservable.objects.select_related("observable__data_type")
        .prefetch_related("observable__case_observables")
        .order_by("id")
    )
