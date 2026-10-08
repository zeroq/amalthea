# Amalthea — REST API (implemented)

Base path: `/api/v1/`. Every `/api/v1/` include is registered twice-spelled (slash + no-slash) where
a body is sent, because `CommonMiddleware`'s `APPEND_SLASH` would 301 a POST and drop its body
(V1/V2). Root URLconf order is load-bearing: `admin/`, `"" → ui.urls`, `"" → core.urls`,
`api/v1/ → ingest, alerts, cases, query, compat`.

## Error envelope (`compat/errors.py::thehive_exception_handler`)

| HTTP | `type` | `message` |
|---|---|---|
| 400 | `BadRequest` | `"Bad request"` (+ `fields` from DRF detail) |
| 401 | `AuthenticationError` | `"Unauthorized"` |
| 403 | `AuthorizationError` | `"Forbidden"` |
| 404 | `NotFoundError` | `"Not found"` |
| 405 | `BadRequest` | `"Method not allowed"` |
| 500 | `GenericError` | `"Internal error"` |

API-key challenges return `WWW-Authenticate: Bearer`. Login failure is a **400, never 401**
(no account-existence oracle, P10-6). The 400 `message` is the literal `"Bad request"`; the
`fields` object carries the per-field detail — views that need a custom `message` build the 400
inline themselves.

## Timestamps on the wire

- **Serializer output is ISO-8601 with timezone** (`core/serializers.py::_iso`), never naive.
- **Inputs `customEvent.date` and alert `date` are epoch-ms** (int/float > 1e12 ⇒ ms, else s; ISO
  strings accepted too — `compat/time.py::parse_timestamp`; `inf`/`nan`/negative/overflow ⇒ 400).
- **Timeline wire renders epoch-ms** (`realtime.md`; deviation P8-1). This is the recorded split:
  entity JSON = ISO-8601, timeline envelope = ms.

## Ingest — `POST /api/v1/alerts/webhook/{source_id}` (+ no-slash twin)

- `@csrf_exempt`, `@require_POST`. Checks in order: `Content-Length` vs
  `settings.WEBHOOK_MAX_BODY_SIZE` (default 5 MiB) → **413** `entityTooLarge`; content-type must
  start with `application/json` / `application/cloudevents+json` / `application/problem+json` →
  **415** `unsupportedMediaType`; unknown source → **404**; auth (`X-Webhook-Secret` ||
  `X-Api-Key` || `Authorization: Bearer`) → **401/403**; throttle (cache keys `wh:src:{slug}:{window}`
  + `wh:ip:{ip}:{window}`, per-source 60/min, per-IP 300/min defaults) → **429**
  `rateLimitExceeded`; then `json.loads` + object check + depth ≤ `WEBHOOK_MAX_DEPTH` (30) → **400**.
- Mapping: `_mapped(payload, mapping, *keys)` — JSONPath first (`ingest/mapping.py`), literal
  fallback; severity coerced with the source's `default_severity` as fallback.
- Persistence: `store_ingested_alert` dedupes on the unique triple `(source, type, source_ref)`;
  missing `sourceRef` ⇒ synthesised `sha256:` of the canonical payload (content-based replay dedupe).
- Response JSON: `_id,id,_type,title,severity,source,type,status,correlation_key,source_ref,
  sourceRef,date,created_at,ingestion_source,created,warnings` — **201 on first store, 200 on
  replay**.

## Alerts

| Path | Methods | View |
|---|---|---|
| `alert`, `alert/` | GET, POST | `alert_list` |
| `alert/<id>/raw` | GET | `alert_raw` |
| `alert/<id>/import` | POST | `alert_import` |
| `alert/<id>/observable` | POST | `alert_observable_add` |
| `alert/<id>/merge/<case_id>` | POST | `alert_merge` |
| `alert/<id>/import/<case_id>` | POST | `alert_merge` (same view) |
| `alert/<id>` | GET, PATCH, PUT, DELETE | `alert_detail` |

- Identifier: UUID or single unambiguous `source_ref`; ambiguity ⇒ 400 `fields {"alertId":[...]}`;
  missing ⇒ 404 `NotFoundError`.
- `alert_list` GET: `?case=` (identifier), `?status=`; `queryset[:200]`, ordered `-date`.
- `alert_list` POST: required `title,type,source,sourceRef` ⇒ 400 `Missing required field(s): …`;
  `date` read as epoch-ms; unmapped payload keys → `ingestion_warnings.unmapped_fields`; 201
  `alert_json`.
- `alert_detail` PATCH/PUT: updatable fields only from `title,description,summary,severity,tlp,pap,
  flag,follow,status` (only-present); none supplied ⇒ 400; DELETE ⇒ **204**.
- `alert/raw` returns `raw_payload` verbatim (never inlined elsewhere).
- `alert/{id}/observable` POST: `data` string-or-array; unknown `dataType` ⇒ 400; create-time-only
  `tlp/pap/ioc/sighted/message`; response 201 = full `alert.alert_observables` list; **no automation
  dispatch** (P10-4).
- `alert/{id}/import` (case-less import): 201 `case_json`; already attached ⇒ 400 "…merge instead".
- `alert/{id}/merge/{case_id}` (also `/import/{case_id}` spelling): 200 `case_json(case,
  detail=True)`; re-merges idempotently (extract + ledger, metadata `already_linked`/`warnings`).
  (`alerts/escalation.py`)

## Cases, tasks, observables, custom fields

| Path | Methods | View |
|---|---|---|
| `case`, `case/` | GET, POST | `case_collection` |
| `case/<id>`, `case/<id>/` | GET, PATCH, PUT, DELETE | `case_detail` |
| `case/<id>/task`, `…/task/` | GET, POST | `case_task_list` |
| `case/<id>/observable`, `…/observable/` | GET, POST | `case_observable_list` |
| `case/<id>/timeline` | GET | `case_timeline` |
| `case/<id>/alert/<alert_id>` | DELETE | `case_alert_remove` |
| `case/<id>/customEvent`, `…/customEvent/` | POST | `case_custom_event_create` |
| `observable/<id>` | GET, PATCH, DELETE | `observable_detail` |
| `task/<id>` | GET, PATCH, DELETE | `task_detail` |
| `customEvent/<id>` | PATCH, DELETE | `custom_event_detail` |
| `customField`, `customField/` | GET | `custom_field_list` |

- Case identifier: UUID **first**, then numeric `number`, resolved in
  `cases/views.py::_resolve_case` → `alerts/escalation.py::link_case_from_identifier`. A
  UUID-shaped string that is not a real UUID is caught by `_as_uuid` (`cases/views.py:75`), never a
  500.
- `case_collection` GET: `?status=`, `?severity=`; `[:200]`, `-start_date`. POST: `title` required
  (400 `{"title":["required"]}`); default status `InProgress`; optional `extract`/`observables`
  text → `extract_into_case`; ledger `kind="case-created"`; 201 `case_json(detail=True)`.
- `case_detail` GET: `case_json(detail=True)` (adds `observables[]`, `timeline[]`,
  `automationRuns[]`, `tasks[]`). DELETE ⇒ **204** — hard delete, children cascade, alerts `SET_NULL`
  (P10-3). PATCH: writes `status-changed` and `assigned` ledger events in the same transaction.
- `task` POST: 201 `{"_id","id","title","status"}`; default status `"Waiting"`.
- `task_detail` PATCH ⇒ **204, no body** (no plan number — see [`deviations.md`](./deviations.md));
  `status` validated against
  `TASK_STATUS_CHOICES`; accepts `dueDate/startDate/endDate` (epoch-ms or ISO). Org guard
  `_case_org_scope` (P10-w).
- `case_observable_list` POST with no `data`/`extract` re-runs extraction over `case.title` +
  `case.description`.
- `case_alert_remove`: unlinks only (`alert.case = None`), ledger `kind="alert-removed"`, 204.
- `customEvent` POST: requires `date` (epoch-ms or ISO) and `title`; `append_timeline_event(...,
  kind="custom", date, end_date)`; 201 `custom_event_json`.
- `case_timeline` GET: `{"events":[...]}` — ms-epoch `date`; kind map `case-created→case.created`,
  `comment→log.created`, `alert-imported|alert-merged→alert.occurred`, else `custom`; `entity`/
  `entityId` as `~<uuid>` (deviation P8-1).
- `custom_event_detail`: only `kind="custom"` rows mutable; others ⇒ 400 "Only custom events can be
  updated or deleted"; PATCH/DELETE ⇒ 204.
- `observable_detail` GET: payload incl. `cases[]` (cross-case linkage). PATCH ⇒ **204** (deliberate:
  alert/case PATCH echo, observable does not — recorded). `dataType` re-type allowed (re-hash on
  save). **Not org-scoped** (global mutation deferred — see F2 in [`deviations.md`](./deviations.md)).

## Query — `POST /api/v1/query` (+ no-slash twin)

`{"query": [step, ...], "includeFields": [...], "excludeFields": [...]}` → **bare JSON array** (or
bare `int` for `count`). `X-Total` header when a `page` step used `extraData:["total"]`.
`includeFields` wins over `excludeFields`. Legacy `?name=` accepted and ignored (ADR D12).
Full DSL in [`query-dsl.md`](./query-dsl.md).

## Compat (login/logout + catch-all)

| Path | Methods | View |
|---|---|---|
| `login`, `login/` | POST | `api_login` |
| `logout`, `logout/` | GET, POST | `api_logout` |
| `re_path(r"^.*$")` | any | `not_found` → 404 `NotFoundError` |

- `api_login`: body `{"user","password"[,"organisation"|"org"]}`; bad creds ⇒ **400** `"Invalid
  credentials"` (never 401); org mismatch ⇒ 400 `{"organisation":["does not belong to this user"]}`;
  success ⇒ `django_login(...)` + `user_json`. Both views `AllowAny` + empty `authentication_classes`
  + `@csrf_exempt` (session-attached CSRF cookie would otherwise reject login/logout POSTs — P10-6).
- `api_logout`: GET|POST ⇒ 200 empty; session flushed.

## Non-`/api/v1/` — `core/urls.py`

- `GET /healthz` → `{"status":"ok"}`.
- `GET /readyz` → DB `SELECT 1` + Redis ping (`settings.REDIS_URL`) + pending-migrations check;
  failure 503 with `detail ∈ db|redis|migrations_pending|migration_check_failed`. (AC1.3/AC1.4 —
  needs a running worker + Redis; not in CI.)

## Wire renderers (`core/serializers.py`)

`alert_json`, `observable_json`, `timeline_event_json`, `task_json`, `custom_event_json`,
`custom_field_json`, `user_json`, `automation_run_json`, `case_json(case, *, detail=False)`.
Conventions: `_id` and `id` both present; ISO-8601 `_iso` timestamps; `raw_payload` never inlined;
`task_json` sets `_createdBy`/`_updatedBy` to `null`; `user_json` computes `hasKey`/`hasPassword`,
`hasMFA:false`, `locked:false`.

## Evidence

Contract tests: `tests/conformance/test_t1_surface.py` (53), `test_unknown_fields.py` (33),
`test_authz.py` (131), `test_thehive_fixtures.py` (13, pinned golden fixtures from thehive4py 2.1.0),
`test_webhook_hardening.py` (37). TheHive shape decisions: `docs/decisions/ADR-002`.
Deviations: [`deviations.md`](./deviations.md).