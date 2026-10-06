# Implementation Brief — Phase 8: The `POST /api/v1/query` DSL

Ref: `PLAN-2026-10-03-thehive-compatible-mvp.md` §Phase 8 (AC8.1–AC8.4), `ADR-002` §D6,
`TODO.md` 5.5. Scope is the plan's Phase 8 tasks 1–4 only (no `_bulk`, no attachments, no
`PATCH /case/_merge/{ids}` — those are §6.1 deferred items).

## The one-sentence contract

`POST /api/v1/query` takes a JSON **object** `{"query": [...], "includeFields": [...],
"excludeFields": [...]}` (never a bare array — the reader is thehive4py and the recorded 5.8.0
docs agree), executes the chained `_name` steps in order, and returns a **bare JSON array** with
`X-Total` set when `"total"` is in the `page` step's `extraData`.

## Wire contract (pinned from thehive4py 2.1.0 source + StrangeBee 5.8.0 docs)

- Request body: `{"query": [step, ...], "includeFields": [...], "excludeFields": [...]}`.
  `includeFields` and `excludeFields` are each optional arrays of top-level field names.
  `includeFields` wins: when present, `excludeFields` is ignored (documented 5.8.0 behaviour).
- Query params: **accept and ignore** any `name` param (thehive4py `find`/`count` send
  `params={"name": "cases"}` / `"cases.count"` — legacy frontend-internal variant per ADR D12).
- A query step is `{"_name": "<op>", ...rest}`. Steps run in order, threading one result set.
- Response: bare JSON array of `alert_json`/`case_json`/`observable_json` objects (the T1
  serializers, already conformant — Phase 8 must NOT introduce a second serialization, that is
  how T1 responses and query responses would drift; the one exception is the timeline shape,
  below). `X-Total` header = total matching rows **across all pages** when requested.
- Errors: the existing `compat.errors.thehive_exception_handler` — 400 `BadRequest`, 401
  `AuthenticationError`, etc. Use the existing `_not_found`/error helpers in the T1 views.

### Operators to implement (plan task 1)

| Step `_name` | Body | Semantics |
|---|---|---|
| `listCase` | `{}` (start) | all cases (T1 `case_json`) |
| `listAlert` | `{}` (start) | all alerts (T1 `alert_json`) |
| `listObservable` | `{}` (start) | all observables (T1 `observable_json`) |
| `listAny` | `{}` (start) | cases + alerts + observables, wrapped markers: `_type` already distinguishes; per ADR §D6 the plan's AC8.1 doc-example must work |
| `getCase` | `{"idOrName": "~<uuid>" or "<case number>"}` | single case; `~` strips the TheHive id prefix, else resolve by `number` (reuse `alerts/escalation._resolve_case` logic or `cases/views._resolve_case`) |
| `filter` | body is one operator object (below) | narrow the current result set |
| `sort` | `{"_fields": [{"<field>": "asc"\|"desc"}, ...]}` | order; MUST apply before `page`; tie-break by `_id` for deterministic paging |
| `page` | `{"from": n, "to": m, "extraData": [...]}` | slice `[from, to)` of the *ordered* set; `"total"` in `extraData` → count of the pre-page set in `X-Total` |
| `count` | `{}` | return **a bare integer** (thehive4py `case.count()` → `[{"_name":"listCase"}, ..., {"_name":"count"}]` expects `int`, not array) |

TheHive 5.8.0 has operators beyond these (`_startsWith`, `_endsWith`, `_id`, `_match`, `_contains`,
`_is`, `_any`, `getAny`, related-object ops, deprecated `countCase` etc.). **They are out of
scope**: AC8.3 demands a 400 `BadRequest` for *any* unknown/unimplemented `_name` or operator key —
never a silent wrong answer, never a no-op. The 400 must be explicit with a message naming the
unsupported operator (e.g. `"Unsupported query operation '_startsWith'"` / `"Unsupported filter '_startsWith'"`).

### Filter operators (plan task 1 — `_eq/_ne/_gt/_gte/_lt/_lte/_between/_in/_like/_has`)

Filter body shapes, exactly per the recorded 5.8.0 docs:

- `_and`: `{"_and": [<filter>, ...]}` — all must match
- `_or`: `{"_or": [<filter>, ...]}` — at least one must match
- `_not`: `{"_not": <filter>}` — must not match
- `_eq`: `{"_eq": {"_field": f, "_value": v}}`
- `_ne`: `{"_ne": {"_field": f, "_value": v}}`
- `_gt` / `_gte` / `_lt` / `_lte`: `{"_gt": {"_field": f, "_value": v}}` etc.
- `_between`: `{"_between": {"_field": f, "_from": a, "_to": b}}` — **`_from` inclusive, `_to` exclusive** (documented 5.8.0 semantics; the plan's AC8.2 "disjoint, complete" depends on this)
- `_in`: `{"_in": {"_field": f, "_values": [v, ...]}}`
- `_like`: `{"_like": {"_field": f, "_value": s}}` — substring match (case-insensitive; SQL `LIKE`/`icontains`)
- `_has`: `{"_has": "<field>"}` — row has a non-null value for the field

Field/values notes:
- Timestamps (`_createdAt`, `_updatedAt`, `date`, `startDate`, ...) arrive as **Unix epoch ms
  integers** (TheHive format). Convert to `datetime` for the ORM. `_between` on timestamps is the
  common paging-carrier in Practice (ADP example in docs).
- Severity: `1..4` ints. Status: string names (`Open`, `Investigating`, `Containment`, `Closed`
  for CaseStatus; `New`/`Triaged`/`Dismissed` for AlertStatus). Observable `dataType`: the type
  name (`IP`, `Domain`, ...). `caseId`: uuid string.
- **Field whitelist**: map TheHive field names → model fields per entity. Any `_field` not on the
  whitelist → 400 `BadRequest` naming the field (AC8.3's "never a silent wrong answer" covers
  fields as well as operators). The whitelist must at least cover the fields of the four T1
  serializers (they are the response contract; a field the API can emit must be filterable).

### Entity scoping and the models involved

Reuse the existing models: `cases.Case`, `alerts.Alert`, `observables.Observable`,
`observables.CaseObservable` (links), their `objects` managers, `select_related`/`prefetch_related`
per plan G5 (no N+1: `case_json`/`alert_json`/`observable_json` already avoid it — the query path
must not reintroduce it; the conformance suite's perf harness will flag a regression).

`sorted_queryset` must apply `_sort` fields mapped to model fields, then tie-break `order_by("id")`
for deterministic paging (AC8.2). `page` slices with Python slicing or `[from:to]` on the evaluated
list for small sets; better: keyset-equivalent `offset/limit` via Django `QuerySet[from:to]` — but
only *after* the full sort, which is what makes AC8.2's "disjoint, complete, ordered" hold.

### The timeline response-shape correction (plan §7.2 deviation — required by AC8.4)

The plan's §7.2 row for `GET /api/v1/case/{caseId}/timeline` says `→ ordered TimelineEvent[]`,
but the recorded 5.8.0 OpenAPI (verified from the live `docs.yaml`) `OutputTimeline` is:

```json
{"events": [ {"date": <ms int>, "kind": <str>, "entity": <str>, "entityId": <str>, "details": {...}, "endDate": <ms int|null>}, ... ]}
```

and thehive4py `case.get_timeline()`/`client.timeline.get()` consume exactly that shape
(`OutputTimeline` type in `thehive4py/types/timeline.py`). **AC8.4 requires reading the timeline
through thehive4py unmodified**, so `cases/views.py case_timeline` must change from the bare array
to the `{"events": [...]}` envelope. Map each `TimelineEvent` to:
- `date`: `int(event.date.timestamp() * 1000)` (ms)
- `kind`: the existing `event.kind` — but TheHive kinds are `case.created`, `alert.occurred`, `task`,
  `log.created`, `custom`, ... Our kinds are `case-created`, `status-change`, `comment`,
  `alert-imported`, `alert-merged`, `automation-run`, `assigned`, ... — map known kinds to the
  nearest TheHive vocabulary (`case.created`, `custom`, ...) and fall back to `custom`.
  **Do not** change `timeline_event_json` (the WS `sync` protocol and the UI use it; that is the
  internal ledger shape). Only the API serialization changes.
- `entity`/`entityId`: derive from the event (`Case`/`Alert`/... + id). For case events:
  `entity="Case"`, `entityId="~"+uuid`; for observable/task events use their types. Details empty
  `{}` for case lifecycle; for events with metadata, put `event.metadata` under `details`.
- `endDate`: `null` (point-in-time events).

Record this in plan §13 as a deviation: **P8-1 — timeline `GET` envelope** (plan §7.2 said bare
array; 5.8.0 + thehive4py require `{"events": [...]}`; internal `timeline_event_json` unchanged).

### Files to create/modify (expected layout)

- `query/` — new Django app: `query/engine.py` (step dispatch + filter/sort/page/count
  translation to ORM), `query/views.py` (`query_view` — parse body, run engine, set `X-Total`),
  `query/urls.py` (`path("query", views.query_view, name="query-api")` and trailing-slash twin),
  `query/apps.py`, `query/__init__.py`.
- `amalthea/urls.py` — mount `path("api/v1/", include("query.urls"))` **before**
  `compat.urls` (the catch-all `re_path(r"^.*$")` would swallow it; same ordering rule the
  `alerts.urls`/`cases.urls` mounts already obey).
- `cases/views.py` — `case_timeline` envelope change (P8-1).
- `amalthea/settings/base.py` — add `"query"` to `INSTALLED_APPS`.
- `tests/conformance/test_query_api.py` — new (below).

### Acceptance criteria for the whole task set

- **AC8.1** The plan's documented example query returns a bare array with `X-Total` set correctly:
  `{"query": [{"_name": "listCase"}, {"_name": "filter", "_eq": {"_field": "severity", "_value": 3}},
  {"_name": "sort", "_fields": [{"_createdAt": "desc"}]},
  {"_name": "page", "from": 0, "to": 10, "extraData": ["total"]}], "excludeFields": ["description"]}`.
  Assert: 200, body is a JSON array (not an envelope), each item has the T1-listed fields minus
  `description`, severity-3 only, newest first, `X-Total` == the count of **all** severity-3 cases
  (not just the 10 returned).
- **AC8.2** `from`/`to` paging over a fixed seeded set (e.g. 25 cases, two pages 0–10/10–20):
  disjoint (no id appears in both pages), complete (union == the full ordered set), ordered (each
  page sorted per the `_sort`), and stable across two runs (deterministic — the `_id` tie-break).
- **AC8.3** Every implemented operator has at least one positive test (`_eq`, `_ne`, `_gt`, `_gte`,
  `_lt`, `_lte`, `_between`, `_in`, `_like`, `_has`, `_and`, `_or`, `_not`, `_sort`, `_page`,
  `_count`). **And** one parametrized test that each *unimplemented* operator/step from the 5.8.0
  catalog (`_startsWith`, `_endsWith`, `_match`, `_contains`, `_is`, `_any`, `getAny`, `listTask`,
  `label`, ...) returns **400 `BadRequest`** with a message naming the operator — never 200, never
  an empty array, never a filtered approximation.
- **AC8.4** thehive4py runs **unmodified** against a live server — this is a *live* verification
  like AC1.3/AC1.4, to be run by the planner after the suites pass (see Handoff below). The
  in-CI conformance test should exercise the same calls through `Client`: `case.find(...)`,
  `alert.find(...)`, `observable.find(...)`, `case.count`, `client.query.run` with the documented
  example, and `case.get_timeline` shape (`{"events": [...]}`). CI cannot run the live server; the
  planner does it, exactly like the Postgres gate.

### Test layout (adopt the conformance conventions)

`tests/conformance/test_query_api.py`:
- seeded fixtures (reuse the repo's existing seed/rollback helpers for the case/alert/observable
  fixtures — look at how `test_mvp_loop.py` seeds).
- per-operator positive tests, AC8.1 example test, AC8.2 paging test, AC8.3 unknown-operator 400
  parametrize, AC8.4 shape tests (thehive4py-produced bodies via the same `Client`),
  `?name=` accepted-and-ignored, `includeFields` wins over `excludeFields`, timeline envelope test.
- auth: like all T1 API tests — `ApiKeyAuthentication`/session via the conformance client helper
  (check `test_mvp_loop_automation.py` for the established pattern).

### Constraints / culture (read these in the repo before writing code)

- No signals for side effects; **explicit calls** (repo culture, `cases/ledger.py` docstring).
- `transaction.on_commit` for anything that must not publish on a rolled-back write (ledger, runs).
- The error envelope is `compat.errors` — do not raise DRF's default 400 shape.
- ruff + mypy strict are enforced by `make check`; `from __future__ import annotations`; type
  annotations on every function; no `Any` leaks in new code where avoidable.
- `serialized_rollback=True` on any new `transaction=True` test.
- New app must pass `manage.py check` and `makemigrations --check` (the `query` app adds no models,
  so no migrations expected — if one is created, something is wrong).
- Keep `cases/ledger.py` untouched except where the timeline wrapper needs it (it should not).
- Do not modify the WS protocol or `live.js` (Phase 7 owns those).

### Handoff

When `make check` is green on SQLite and the Postgres suite is green, the planner will:
1. Run the live AC8.4 verification: start the dev server against the Postgres containers with a
   real API key, run an unmodified thehive4py client script exercising exactly
   `case.find → alert.create → alert.merge_into_case → case.get_timeline` (the plan's AC8.4 wording),
   verify all four succeed.
2. Review the diff, update `TODO.md` 5.5 (DONE + gate figures), `PLAN` §13 (P8-1 and any new
   deviations), `COMPLETED.md`, and commit.