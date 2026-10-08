# Amalthea — Query DSL (implemented)

Endpoint: `POST /api/v1/query` (+ no-slash twin). The view (`query/views.py`) owns the wire; the
step/operator logic is in `query/engine.py::QueryEngine`. The wire contract is pinned from thehive4py
2.1.0's request builders (`docs/planning/BRIEF-2026-10-06-query-api.md`).

## Request / response contract

```json
{"query": [step, ...], "includeFields": [...], "excludeFields": [...]}
```

- **Object body, never a bare array**; a non-object body ⇒ 400.
- Response is a **bare JSON array** of T1-serializer objects (`query/views.py::_render` dispatches
  `case_json`/`alert_json`/`observable_json`) — never an envelope, and there is exactly one renderer
  per entity so query and T1 cannot drift. A `count` answers a **bare int**.
- `includeFields` present **wins** over `excludeFields` (documented 5.8.0 behaviour); either must be
  an array of strings or it 400s.
- `X-Total` is set only when the response object's `engine.total is not None` — i.e. a `page` step
  whose `extraData` contained `"total"`, and the count is of the **pre-page** set (spans all pages).
- Unknown step/operator/field ⇒ 400 built inline by `query/views.py::_bad_request` (type
  `BadRequest`, message **naming** the token). It is inline rather than raised because
  `compat.errors` flattens the message to the literal `"Bad request"`.
- Legacy `?name=` is accepted and ignored (ADR-002 §D12).

## Steps

Start steps (`_START_STEPS`) — a start step **resets** the result set and must precede
`filter`/`sort`/`page`: `listCase`, `listAlert`, `listObservable`, `listAny`, `getCase`.

- `listCase`/`listAlert`/`listObservable` → one branch.
- `listAny` → three branches (`kind`s case/alert/observable); every later step is per-branch.
- `getCase` with `idOrName` (or `id`) = `~<uuid>`, a bare UUID, or a number, resolved via
  `link_case_from_identifier`; a **miss** returns `Case.objects.none()` → HTTP 200 `[]` (a miss is
  an empty set, not an error), and later steps answer over it.

Other steps:

- `filter` / `sort` / `page` require an anchored start (`_require_anchored`) — else 400.
- `count` must be the **final** step and returns an int.
- `filter` body is everything except `_name`; `sort` carries `terms`; `page` uses `from`/`to` ints
  (non-negative) and optional `extraData`.

## Filter operators (`_LEAF_OPS`)

| op | wire | semantics |
|---|---|---|
| `_eq` | `_value` | equality |
| `_ne` | `_value` | `~Q(...)` |
| `_gt` / `_gte` / `_lt` / `_lte` | `_value` | range (mapped in `_RANGE_OPS`) |
| `_between` | `_from`, `_to` | `_from` inclusive, `_to` **exclusive** — adjacent pages tile without overlap (AC8.2) |
| `_in` | `_values` | membership |
| `_like` | `_value` (str) | `__icontains` (case-insensitive substring) |

Plus combinators `_and` / `_or` (arrays of filters) / `_not` (one filter), and `_has` (a bare field
name) ⇒ `__isnull=False`. Every filter object must be non-empty.

**Fields absent from a branch are "cannot match", not "error":** `_leaf_q` receives `spec is None`
for a kind that lacks the field and returns `_NO_MATCH` (`Q(pk__isnull=True)`) — severity on the
observable branch of `listAny` drops out under `_eq` and re-appears under `_not`. A 400 fires on an
unknown operator key or a field on **no** active whitelist (`_resolve`), before touching the
queryset.

## Field whitelists

`_allowed()` is the union of the active kinds' `_SPEC_TABLES`; a filter may name any field on that
union, but the compiler resolves per kind (so a field valid on cases and alerts is a cannot-match on
observables). Sort fields are checked against the same whitelist.

## Deterministic paging & sorting

- ORM path: `branch.qs.order_by(*(order or ["id"]), "id")` — the `id` tie-break is appended so equal
  keys page identically every run (AC8.2).
- Cross-kind path (`listAny` after a sort): materialise via `_materialize`, then `_sort_rows` /
  `functools.cmp_to_key`; the comparator falls through on incomparable types to a stable
  `(str(left.id) > str(right.id)) - (…)` tie-break — the same `_id` order the ORM path appends.
- `page` single kind: database offset/limit (`qs[start:end]`), counting **before** the slice when
  `extraData` asked for total. Multi-kind: slice the materialised list.
- `_filter_rows` (a filter arriving after a sort) re-selects surviving pks per kind through the same
  `_build_filter`, so the operator table stays the single (ORM) source — no Python-side evaluator.

## Evidence

`tests/conformance/test_query_api.py` (start steps, operators, `listAny` branches, whitelist, bare
array, `X-Total`, thehive4py round-trips). Wire shape: `BRIEF-2026-10-06-query-api.md`; decisions:
`docs/decisions/ADR-002-thehive-api-compatibility.md` §D6.