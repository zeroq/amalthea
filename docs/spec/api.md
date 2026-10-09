# Amalthea — REST API (implemented)

Base path: `/api/v1/`. Every `/api/v1/` include is registered twice-spelled (slash + no-slash) where
a body is sent, because `CommonMiddleware`'s `APPEND_SLASH` would 301 a POST and drop its body
(V1/V2). Root URLconf order is load-bearing: `admin/`, `"" → ui.urls`, `"" → core.urls`,
`api/v1/ → ingest, identity, observables, alerts, cases, query, compat`. `identity` and
`observables` are mounted **before** `cases` because `cases/urls.py` registers
`observable/<str:observable_id>`, a single-segment match that would otherwise swallow
`observable/type` and resolve it to an observable whose id is the word "type".

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

### Collaboration (T2/Phase P2)

| Path | Methods | View |
|---|---|---|
| `case/<id>/comment`, `…/comment/` | GET, POST | `case_comment_list` |
| `comment/<id>`, `comment/<id>/` | PATCH, DELETE | `comment_detail` |
| `case/<id>/page`, `…/page/` | GET, POST | `case_page_list` |
| `case/<id>/page/<page_id>` (+ `/`) | GET, PATCH, DELETE | `case_page_detail` |
| `case/<id>/flow`, `…/flow/` | GET | `case_flow` |
| `case/<id>/shares`, `…/shares/` | GET, POST, PUT | `case_share_list` |
| `case/<id>/share/<share_id>` (+ `/`) | DELETE | `share_detail` |
| `alert/<id>/comment`, `…/comment/` | GET, POST | `alert_comment_list` |

- Every route runs through `_case_access(request, id, write=…)`: a case with **no** `owner_org` is
  visible to any authenticated caller; an org-owned case is visible to its org, or to an org holding
  a `Share` (`read` for reads, `write` additionally for mutating verbs). Unauthorised ⇒ the same
  **404** as "does not exist". Alerts have no share route (**P2-5**).
- `comment` create: body `{"message": str}` required; 201 `comment_json`; **also** appends a
  `comment` ledger event in the same transaction, so the case's WebSocket room receives it
  (deviation **P2-1**). `comment_detail` edits/deletes the entity only.
- `page` create: `title` required; optional `content` (Markdown), `order` (int), `category`; 201
  `page_json`; publishes a `page` event (deviation **P2-6**). PATCH accepts any of those fields.
- `case_share_list`: POST adds (`share`), PUT replaces the whole set (`set_share`); body
  `{"shares":[{"organisation": id|name, "permissions": {"write": bool}}]}`. `share_json` carries
  `organisationName` + `permissions`/`canWrite` (deviation **P2-3**).
- `case_flow`: `{"_type":"flow","case":…,"alerts":[…],"observables":[…],"tasks":[…]}` — an extension
  with no TheHive REST route; never inlines `raw_payload` (deviation **P2-4**).

### Attachments (T2/Phase P3)

| Path | Methods | View |
|---|---|---|
| `case/<id>/attachments`, `…/attachments/` | POST | `case_attachment_list` |
| `case/<id>/attachment/<attachment_id>/download` | GET | `case_attachment_download` |
| `case/<id>/attachment/<attachment_id>` (+ `/`) | DELETE | `case_attachment_detail` |

- Upload is `multipart/form-data` with one or more files under the repeated `attachments` field;
  the response is TheHive's wrapper `{"attachments":[attachment_json, …]}` (201), not a bare list.
- **Size is checked before storage**: a file over `ATTACHMENT_MAX_BYTES` ⇒ **413**, with no blob and
  no row. A declared type outside the allowlist, or bytes whose magic signature contradicts the
  declared type, ⇒ **415**. The client's filename is reduced to a single safe segment and the blob is
  stored under a server-generated opaque key (deviation **P3-1**).
- Download requires only read access and returns the original `name` (via `Content-Disposition`) and
  `content_type`; the recorded `sha256` is comparable against the body (`hashes[0]`).
- All three verbs run through `_case_access` — a foreign organisation gets the same **404** as an
  unknown id.

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

### Bulk, merge and case templates (T2/Phase P4)

| Path | Methods | View |
|---|---|---|
| `case/_bulk`, `alert/_bulk`, `task/_bulk`, `observable/_bulk` | PATCH | `case_bulk_update`, `alert_bulk_update`, `task_bulk_update`, `observable_bulk_update` |
| `case/_bulk/caseTemplate` | POST | `case_apply_template` |
| `case/_merge/<id1,id2,…>` | POST | `case_merge` |
| `case/template`, `case/template/` | GET, POST | `case_template_collection` |
| `case/template/<idOrName>` | GET, PATCH, DELETE | `case_template_detail` |
| `taxonomy`, `taxonomy/` | GET | `taxonomy` |

- The four `_bulk` PATCH endpoints take `{"ids":[…], …fields}` and report **per-item** results:
  `{"results":[{"id","status","message"?}…],"updated":n,"failed":m}`. Each item is transactionally
  isolated, so one bad id or value never rolls back the batch (AC6.1-P4-a, `compat/bulk.py`).
- `case/_merge/<ids>` merges every case after the first (the target) into it, re-parents alerts,
  tasks, comments, pages, attachments, timeline events, observables/shares/custom fields, appends a
  `case-merged` ledger event per absorbed case, and is idempotent on replay (AC6.1-P4-b). This is
  TheHive's `POST /case/_merge/{ids}`. An extension `POST /alert/{id}/merge/<case_id>` was already
  present; `POST /alert/{id}/import/<case_id>` is a synonym.
- `case/_bulk/caseTemplate` applies a template's tag/task/custom-field defaults to `{"ids":[…]}`.
- `case/template` is the template CRUD; `taxonomy` aggregates the editable vocabularies
  (`caseStatus`, `alertStatus`, `observableType`, `caseTemplate`, `tag`, TTP) for the UI. Both are
  Amalthea extensions (no thehive4py module) — deviations **P4-1**…**P4-3**.

## Identity — `user`, `organisation` (T2/Phase P1)

| Path | Methods | View |
|---|---|---|
| `user/current`, `user/current/` | GET | `user_current` |
| `user/<idOrLogin>`, `user/<idOrLogin>/` | GET | `user_detail` |
| `organisation`, `organisation/` | GET | `organisation_collection` |
| `organisation/<idOrName>`, `organisation/<idOrName>/` | GET, PATCH | `organisation_detail` |

- `user_current` ⇒ `user_json` of the authenticated caller (the cheapest way for a client to learn
  the `login` it must send as an `assignee`). `user_detail` resolves `{idOrLogin}` as a UUID first,
  then as `login`; unknown ⇒ 404.
- `organisation_collection` is an **extension**: the recorded 5.8.0 bare path carries only
  `POST /organisation` (create). It returns a list holding *at most* the caller's own organisation.
- `organisation_detail` is scoped to the caller's own tenant: a foreign id/name is the **same 404**
  as "does not exist" (no tenant-enumeration oracle). PATCH writes `name`/`description` only and is
  **204, no body**; an empty body ⇒ 400; a rename onto an existing tenant ⇒ 400 (not a 500).

## Observable types — `observable/type` (T2/Phase P1)

| Path | Methods | View |
|---|---|---|
| `observable/type`, `observable/type/` | GET, POST | `observable_type_collection` |
| `observable/type/<idOrName>`, `…/` | GET, PATCH, DELETE | `observable_type_detail` |

- `{typeId}` resolves as a UUID first, then as the unique `name`. The collection GET/POST is an
  **extension** (TheHive lists types only through `POST /query`; its single detail path is
  `GET|PATCH|DELETE /observable/type/{typeId}`).
- PATCH writes `isAttachment`/`isCaseSensitive` (the recorded `InputUpdateObservableType`) and goes
  through `ObservableType.save()` so a case-rule flip re-hashes that type's observables. Empty body
  ⇒ 400. DELETE ⇒ **204**, but an in-use type ⇒ **400** (not the FK `PROTECT`'s 500).

## Statuses and tags (T2/Phase P1)

| Path | Methods | View |
|---|---|---|
| `caseStatus`, `caseStatus/` | GET, POST | `case_status_collection` |
| `caseStatus/<idOrValue>`, `…/` | GET, PATCH, DELETE | `case_status_detail` |
| `alertStatus`, `alertStatus/` | GET, POST | `alert_status_collection` |
| `alertStatus/<idOrValue>`, `…/` | GET, PATCH, DELETE | `alert_status_detail` |
| `tag`, `tag/` | GET, POST | `tag_collection` |
| `tag/<idOrName>`, `…/` | GET, PATCH, DELETE | `tag_detail` |
| `case/<idOrNumber>/tag`, `…/` | POST, DELETE | `case_tag_link` |
| `alert/<alertId>/tag`, `…/` | POST, DELETE | `alert_tag_link` |
| `observable/<id>/tag`, `…/` | POST, DELETE | `observable_tag_link` |

- `{idOrValue}` resolves as a UUID first, then the unique `value` (caseStatus/alertStatus) or `name`
  (tag). `value`/`stage` are **immutable**: a PATCH carrying only those has nothing to write and is a
  400. Unknown `stage` ⇒ 400 naming the vocabulary (`CASE_STAGES`/`ALERT_STAGES`). DELETE refuses an
  in-use row with a **400** (`Case.status`/`Alert.status` are `PROTECT`).
- Statuses `InputCreate*` `colour` is accepted-and-ignored (no model column; ADR D11).
- `tag` collection GET/POST and the three link routes are an **extension** (TheHive has only
  `GET|PATCH|DELETE /tag/{tagId}` and attaches tags through the create/update body). PATCH accepts
  the recorded `InputUpdateTag` fields (`predicate`, `description`, `colour`) plus `name`. Tag DELETE
  refuses a tag still attached to a case/alert/observable with a **400** (TheHive cascades).
- The link routes take `{"tags": <string | string[]>}` and return the owner's current tag set
  (**200**); both directions are idempotent. `observable/<id>/tag` is **not org-scoped**, matching
  `observable_detail`.

## Describe — `GET /api/v1/describe/{model}` (T2/Phase P1)

| Path | Methods | View |
|---|---|---|
| `describe/_all`, `describe/_all/` | GET | `describe_all` |
| `describe/<model>`, `describe/<model>/` | GET | `describe_model` |

Read-only catalogue keyed by model name (`case`, `alert`, `task`, `observable`, `customEvent`,
`customField`, `user`, `organisation`, `tag`, `caseStatus`, `alertStatus`, `observableType`). An
unknown model ⇒ 404. `attributes[]` uses TheHive's `PropertyDescription` vocabulary
(`type`, `cardinality`, `aggregable`, `indexType`, with `values`/`labels` for enumerations). This is
a curated subset, not a mechanical dump of every column.

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
`custom_field_json`, `user_json`, `automation_run_json`, `case_json(case, *, detail=False)`,
`organisation_json`, `observable_type_json`, `case_status_json`, `alert_status_json`, `tag_json`,
`comment_json`, `page_json`, `share_json`, `attachment_json`.
Conventions: `_id` and `id` both present; ISO-8601 `_iso` timestamps; `raw_payload` never inlined;
`task_json` sets `_createdBy`/`_updatedBy` to `null`; `user_json` computes `hasKey`/`hasPassword`,
`hasMFA:false`, `locked:false`. The new entity renderers emit the audit pair as `null` where the
model has no creator column (`organisation_json`, the status renderers), and `tag_json` renders
`namespace="_freetags_"` / `predicate=name` / `value=""` around our single `Tag.name`.

## Evidence

Contract tests: `tests/conformance/test_t1_surface.py` (53), `test_unknown_fields.py` (33),
`test_authz.py` (131), `test_thehive_fixtures.py` (13, pinned golden fixtures from thehive4py 2.1.0),
`test_webhook_hardening.py` (37), `test_t2_p1_surface.py` (19, the T2 P1 surface: identity,
observable types, statuses, tags, describe), `test_t2_p2_surface.py` (12, collaboration),
`test_t2_p3_attachments.py` (9, attachments). TheHive shape decisions: `docs/decisions/ADR-002`.
Deviations: [`deviations.md`](./deviations.md).