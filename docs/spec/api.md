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

## Ingest — `POST /api/v1/alerts/webhook/` (+ no-slash twin)

ADR-008: the path names no source. The credential is the only routing fact — it resolves both the
`IngestionSource` (mapping rules, rate-limit bucket) and the `Organisation` that stamps `owner_org`.

- `@csrf_exempt`, `@require_POST`. Checks in order: `Content-Length` vs
  `settings.WEBHOOK_MAX_BODY_SIZE` (default 5 MiB) → **413** `entityTooLarge`; content-type must
  start with `application/json` / `application/cloudevents+json` / `application/problem+json` →
  **415** `unsupportedMediaType`; the presented secret (`X-Webhook-Secret` || `X-Api-Key` ||
  `Authorization: Bearer`) is looked up by its unique 12-char prefix and verified against the Argon2
  hash — missing, unknown, wrong, **or revoked** ⇒ **401** `unauthorized` (strict since 2026-10-10,
  ADR-004; uniform since 2026-10-15, ADR-008 — there is no 404 and no existence oracle left); the
  matched token's `organisation` is what stamps the alert's `owner_org`; throttle (cache keys
  `wh:src:{slug}:{window}` + `wh:ip:{ip}:{window}` + `wh:token:{prefix}:{window}`,
  per-source 60/min, per-IP 300/min defaults) → **429**
  `rateLimitExceeded`; then `json.loads` + object check + depth ≤ `WEBHOOK_MAX_DEPTH` (30) → **400**.
- Mapping (`ingest/mapping.py::apply_mapping` — pure, no ORM/HTTP): one pass over the payload after
  the guards, before persistence. `IngestionSource.mapping_config` is either **v2** —
  `{"version": 2, "fields": {<target>: {"paths": [JSONPath…], "transform": <name>, "default"?,
  "max_length"?, "bands"?}}, "value_maps"?, "on_missing"?}` — or the **legacy** flat
  `{target: "$.path"}` dict, which still works and is normalised on read (the Settings editor's
  table only ever writes v2). Targets are the closed set `title, description, summary, severity, date, type,
  source_ref` (`TARGETS`; `tlp`/`pap`/`tags` are out, and `correlation_key` keeps its own reader).
  `paths` are ordered candidates and the first non-empty answer wins; a v2 rule's paths are the
  *whole* candidate list, so the legacy literal-key fallback applies only to unconfigured targets.
  Transforms are a closed set (`str`, `str_lower`, `int`, `severity_name`, `severity_score`,
  `severity_int`, `datetime`, `list`, `json`, `tlp`, `pap`) and every one is **total**: bad input
  yields `None` plus a warning, never an exception. `severity_name` maps a case-insensitive built-in
  1–4 synonym vocabulary, merged with the operator's `value_maps.severity`; `severity_score` applies
  score bands (overridable per rule as `bands`; defaults `<20 → 1`, `20–49 → 2`, `50–79 → 3`,
  `≥80 → 4`). A rule's `default` is used when no path answers; severity then falls back to the
  source's own `default_severity`, threaded from the token's source at the webhook. `on_missing` is
  reserved — `warn` is the only shipped behaviour. Configs are validated at **write** time
  (`validate_config`, via the Settings editor): an unknown target or transform, a non-compiling path
  or an oversize list is refused before storage, never at ingest.
- Mapping warnings: nothing silently defaults. `Alert.ingestion_warnings["mapping"]` is a list of
  `{"field", "reason", "paths"?, "value_preview"?}` entries describing how the **current** fields
  were resolved — a later mapping pass replaces it — while `ingestion_warnings["warnings"]` is the
  append-only event log (a re-map appends one `remapped:` line). `reason` is a stable token:
  `not_found`, `unknown_value`, `bad_type`, `bad_transform`, `truncated`; the first four are
  `UNRESOLVED_REASONS` = "the rule produced no value". The full split is documented in
  [`data-model.md`](./data-model.md) (MAP-2b) rather than repeated here.
- Persistence: `store_ingested_alert` **requires** an `owner_org` (the token's) and dedupes on the
  per-org unique quad `(owner_org, source, type, source_ref)` (ADR-004);
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

- Every route runs through `identity.scoping.resolve_case` (the `_case_access` wrapper): a case is
  visible to its owner org, or to an org holding a `Share` (`read` for reads, `write` additionally
  for mutating verbs). `Case.owner_org` is `NOT NULL` (ADR-004), so there is **no** org-less
  "visible to any authenticated caller" mode. Unauthorised ⇒ the same **404** as "does not exist".
  Alerts have no share route (**P2-5**).
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
  (400 `{"title":["required"]}`); default status `InProgress`; accepted payload keys:
  `title` (required), `description`, `summary`, `severity` (1-4), `tlp` (0-4), `pap` (0-3),
  `status` (tolerant, existing vocabulary; **absent/empty/`null` keeps the default `InProgress**),
  `flag` (bool), `tags` (array of strings), `assignee` (login or UUID, org-scoped),
  `extract` (bool, defaults false), `observables` (array of strings added to extraction corpus),
  `scope` (enum: `unset`, `single_asset_user`, `multiple_assets_users`, `single_department`,
  `multiple_departments`, `complete_enterprise`, `third_party_supplier`),
  `caseType` (enum: `unset`, `malware`, `phishing`, `password_leak`, `ransomware`, `bec`),
  `dataClassification` (enum: `unset`, `no_data`, `public_data`, `personal_data`, `sensitive_data` —
  **admin/superAdmin write only**),
  `phase` (enum: `unset`, `initial_triage`, `containment`, `analysis`, `remediation`, `cleanup`, `close`),
  `verdict` (enum: `undefined`, `true_positive`, `false_positive`; **required non-`undefined`** when
  `status` names a `Closed`-stage status — see `case_detail` below), `comment` (closing note, **required
  non-blank** on such a create).
  Extraction is **unconditional** — `extract_into_case` always runs over title and description;
  `extract`/`observables` only add explicit strings. Stricter enum validation: non-integer or
  out-of-range `severity`/`tlp`/`pap` → 400; empty/null enum keeps the model default
  (ADR-002 §D11 tolerance). Actor with `org=None` → 403 `AuthorizationError` (two-key body).
  201 `case_json(detail=True)` with ledger `kind="case-created"`.
- `case_detail` GET: `case_json(detail=True)` (adds `observables[]`, `timeline[]`,
  `automationRuns[]`, `tasks[]`). DELETE ⇒ **204** — hard delete, children cascade, alerts `SET_NULL`
  (P10-3). PATCH: writes `status-changed`, `assigned` and `classification-changed` ledger events in
  the same transaction. Accepted payload keys include the four classification fields
  (`scope`, `caseType`, `dataClassification`, `phase`) plus `verdict`; `dataClassification` is
  **admin/superAdmin write only**; out-of-vocabulary → 400; empty/null keeps model default.
  **Closing (`status` → a `Closed`-stage status) is refused with 400** unless the payload carries a
  non-`undefined` `verdict` and a non-blank `comment` (`{"verdict":[…]}` / `{"comment":[…]}`). A
  successful close stamps `closedDate` + `endDate`, closes every linked alert (copying the verdict
  onto each, deviation **V-2**) and appends an `alerts-closed` ledger event; reopening clears
  `closedDate`. The same rule is enforced on `PATCH /api/v1/case/_bulk` per item and on create.

**Close-rule boundary (SECURITY-2026-10-18 M2):** This rule is enforced at the service layer
(`apply_case_update`, `case_bulk_update`, `create_case`) and **does not** apply to direct ORM
writes (`Case.save()`, `Case.objects.update()`), management commands, shell, or raw SQL. A direct
write that sets a `Closed`-stage status will persist `closedDate` but leave `verdict="undefined"`
with no closing comment. All user-reachable API/UI paths are guarded; the bypass exists only for
code paths that deliberately circumvent the service layer.
- `task` POST: 201 `{"_id","id","title","status"}`; default status `"Waiting"`.
- `task_detail` PATCH ⇒ **204, no body** (no plan number — see [`deviations.md`](./deviations.md));
  `status` validated against
  `TASK_STATUS_CHOICES`; accepts `dueDate/startDate/endDate` (epoch-ms or ISO). Org guard
  `_case_org_scope` → `identity.scoping.org_q` (P10-w).
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
  save). **Org-scoped** (ADR-004): resolution is `identity.scoping.resolve_observable`, so a
  cross-tenant id is the same **404** as "does not exist", and observables dedupe per org on
  `(owner_org, data_type, data_hash)` rather than globally.

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

### Procedures, TTP and export (T2/Phase P5)

| Path | Methods | View |
|---|---|---|
| `case/<id>/procedure` | POST | `case_procedure_create` |
| `case/<id>/procedures` | POST | `case_procedures_create` |
| `alert/<id>/procedure` | POST | `alert_procedure_create` |
| `alert/<id>/procedures` | POST | `alert_procedures_create` |
| `procedure/<id>` | GET, PATCH, DELETE | `procedure_detail` |
| `procedure/delete/_bulk` | POST | `procedure_bulk_delete` |
| `ttp`, `ttp/` | GET, POST | `ttp_collection` |
| `ttp/<idOrName>` | GET, PATCH, DELETE | `ttp_detail` |
| `case/<id>/export` | GET | `case_export` |

- `InputProcedure` is TheHive's shape: `{"occurDate": ms-int, "patternId", "patternName", "tactic",
  "description"}`; the bulk form wraps a list in `{"procedures":[…]}` and returns a list (201).
  Amalthea adds `ttpId` (id or name) linking the procedure to the TTP vocabulary.
- `procedure_detail` PATCH accepts those fields (204); `POST /procedure/delete/_bulk` deletes
  `{"ids":[…]}` (204). `ttp` is an Amalthea extension — TheHive exposes no TTP vocabulary route
  (deviation **P5-2**); DELETE refuses a technique referenced by any procedure (AC6.1-P5-b).
- `case/<id>/export` returns a self-contained JSON document (`_type: "CaseExport"`, `version: 1`)
  whose `alerts[].rawPayload` carries the source telemetry **verbatim** — the one deliberate
  exception to "`raw_payload` never inlined" (AC6.1-P5-a). The optional `?password=` query param is
  accepted for TheHive-client compatibility but the body is **not** encrypted (deviation **P5-3**).

### Playbook authoring (T2/Phase P2)

| Path | Methods | View |
|---|---|---|
| `playbook`, `playbook/` | GET, POST | `playbook_collection` |
| `playbook/_meta` | GET | `playbook_meta` |
| `playbook/<idOrName>` | GET, PATCH, DELETE | `playbook_detail` |
| `playbook/<idOrName>/run` | POST | `playbook_run` |

- **An Amalthea extension** — TheHive 5 / `thehive4py` 2.1.0 expose no playbook route (deviation
  **P12-2**).
- Create: required `name`, `triggerEvent` ∈ `TRIGGER_EVENTS`, `config` (validated by
  `automation.playbooks.validate_config` — mirrors `executor.execute` exactly). 201 `playbook_json`.
- `config` validation at write time (AC6.12-P2-b):
  - `http`: `url` required; `method` ∈ standard HTTP verbs; `headers` string→string map; `timeoutSeconds` positive number ≤ 30.
  - `python`: `action_path` must be a key in `automation.executor.registered_actions()`.
  - Unknown `action` ⇒ 400.
- `_meta` (AC6.12-P2-c): `{"triggerEvents":[…], "actions":[{"action":"http",…},{"action":"python","registeredPaths":[…]}], "actionNames":["http","python"]}` — rendered from the live code so the UI pickers cannot drift.
- `playbook/<idOrName>`: lookup by UUID or `name`; PATCH ⇒ **204 no body** (deviation); DELETE ⇒ **204** or **400** if the playbook has any `AutomationRun` (FK is `PROTECT`).
- `playbook/<idOrName>/run`: body `{"case"?: idOrNumber, "observable"?: id}` with at least one required; returns **202** + run JSON (`triggeredBy: "manual"`); enqueues a non-deduping run (key `manual:<uuid4>`); result lands in `output_log` **and** case timeline (AC6.12-P2-a).

## Identity — `user`, `organisation` (T2/Phase P1)

| Path | Methods | View |
|---|---|---|
| `user/current`, `user/current/` | GET | `user_current` |
| `user`, `user/` | GET, POST | `user_collection` (superAdmin only) |
| `user/<idOrLogin>`, `user/<idOrLogin>/` | GET, PATCH, DELETE | `user_detail_management` |
| `organisation`, `organisation/` | GET, POST | `organisation_collection` |
| `organisation/<idOrName>`, `organisation/<idOrName>/` | GET, PATCH, DELETE | `organisation_detail` |

- `user_current` ⇒ `user_json` of the authenticated caller (the cheapest way for a client to learn
  the `login` it must send as an `assignee`).
- `user_collection` (`GET|POST /api/v1/user`) — superAdmin only. GET returns all users with
  `user_admin_json` (login, email, role, org, isActive, lastLogin, _createdAt). POST creates a user
  with `{username, email, role?, org, password, is_active?}`; `login` derived from `username`; runs
  Django password validators; audits `user.created`.
- `user_detail_management` (`GET|PATCH|DELETE /api/v1/user/{idOrLogin}`) — resolves `{idOrLogin}` as
  a UUID first, then as `login`; unknown ⇒ 404. **GET** is available to any authenticated user (for
  assignee resolution) and returns the documented **`user_json`** contract (`hasKey`/`hasPassword`/
  `hasMFA`/`locked`) — this path is deliberately *not* swapped for `user_admin_json`, because
  TheHive clients read those fields here. **PATCH/DELETE** are superAdmin only: PATCH updates
  `email`, `role`, `org`, `is_active`, optional `password` reset. Guards: cannot demote/deactivate
  the last active superAdmin, cannot change your own role away from superAdmin, cannot delete self.
  Audits `user.updated`/`user.deleted`.
- `organisation_collection` (`GET|POST /api/v1/organisation`) — GET: non-superAdmin returns own org
  (unchanged); superAdmin returns ALL organisations annotated with counts (members, cases, alerts,
  observables) via `organisation_admin_json`. POST (superAdmin only): creates org with `{name,
  description?}`; duplicate name → 400; audits `organisation.created`.
- `organisation_detail` (`GET|PATCH|DELETE /api/v1/organisation/{idOrName}`) — non-superAdmin:
  own org only (GET/PATCH as before). SuperAdmin: any org. GET returns counts. PATCH updates
  `name`/`description`; audits `organisation.updated`. DELETE (superAdmin only): guarded deletion.
  Pre-flight checks for tenant data (cases/alerts/observables/ingestionTokens/ingestionSources) →
  **409** with blockers (`ingestionSources` added by ADR-009: the feed itself is tenant-owned, so it
  blocks too). If members exist: requires `members=reassign&target_org=<idOrName>` or `members=delete`.
  All operations atomic; audits `organisation.deleted`.

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
  (**200**); both directions are idempotent. `observable/<id>/tag` is **org-scoped** (ADR-004),
  matching `observable_detail` — a cross-tenant id is the same **404**.

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
**Org-scoped** (ADR-004): `QueryEngine` scopes every start step to the caller's organisation
(cases honour shares; `superAdmin` cross-org reads are audited as `cross_org_read_query`) —
cross-tenant rows never enter the result set.
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
`custom_field_json`, `user_json`, `user_admin_json`, `automation_run_json`,
`case_json(case, *, detail=False)`, `organisation_json`, `organisation_admin_json`,
`observable_type_json`, `case_status_json`, `alert_status_json`, `tag_json`,
`comment_json`, `page_json`, `share_json`, `attachment_json`.
Conventions: `_id` and `id` both present; ISO-8601 `_iso` timestamps; `raw_payload` never inlined;
`task_json` sets `_createdBy`/`_updatedBy` to `null`; `user_json` computes `hasKey`/`hasPassword`,
`hasMFA:false`, `locked:false`. `user_admin_json` (superAdmin directory) includes `login`, `email`,
`role`, `org` (name), `orgId`, `isActive`, `lastLogin`, `_createdAt`. `organisation_json` now
includes `_createdAt`/`_updatedAt` from `TimeStampedModel`; `organisation_admin_json` adds optional
`counts` block (members, cases, alerts, observables). The entity renderers emit the audit pair as
`null` where the model has no creator column (status renderers), and `tag_json` renders
`namespace="_freetags_"` / `predicate=name` / `value=""` around our single `Tag.name`.

## Analyst UI (server-rendered)

The Django template UI lives at `/` (no `/api/` prefix) and is session-authenticated (CSRF-protected).
Routes are named `ui-*` in `ui/urls.py`.

| Path | Methods | View | Name |
|---|---|---|---|
| `cases/new` | GET, POST | `case_create` | `ui-case-create` |
| `cases/<case_id>` | GET | `case_detail` | `ui-case-detail` |
| `sources` | GET | `sources_list` | `ui-sources-list` |
| `settings/sources/<uuid:source_id>` | GET, POST | `settings_source_detail` | `ui-settings-source-detail` |

### `GET|POST /cases/new` (`ui-case-create`)

- **GET** → 200: Renders the case creation form with fields:
  `title` (required, maxlength=500), `description` (Markdown), `summary`,
  `severity`/`tlp`/`pap` (selects, defaults 2/2/2), `status` (select over `CaseStatus` vocabulary, default `InProgress`),
  `assignee` (select, users in actor's org), `flag` (checkbox), `tags` (comma-separated).
  The extraction hint is shown; extraction runs unconditionally on create.
  Viewer (`role=viewer`) → 404 (uniform, `@not_viewer` gate). Actor with no organisation → 200
  explanatory state, no form.
- **POST** → 302 to `ui-case-detail` on success; 200 re-render with error on validation failure.
  On success: `Case` created with `owner_org = actor.org`, `Case.number` allocated, timeline event
  `kind="case-created"` ("Case created"), `AuditLog` row `action="case.created"` with
  `target_type="Case"`, `target_id=str(case.id)`, `target_org=actor.org`, all in the same
  transaction. Extraction runs unconditionally over title + description.
  CSRF protected: `Client(enforce_csrf_checks=True)` POST without token → 403.

### `GET|POST /settings/sources/<source_id>` (`ui-settings-source-detail`) — the mapping editor

- The `/sources` list (`ui-sources-list`) is org-scoped and shows each feed's alert count and a rule
  summary (`N rules · severity←<transform or first path>`); the slug links to the detail page for
  admins only, plain text otherwise.
- Admin-only and org-scoped (ADR-009): `_require_admin_ui` + `resolve_ingestion_source` — an `admin`
  reaches own-org sources, a `superAdmin` any, and a viewer/analyst or foreign-org admin gets the
  same **404** as a nonexistent id, never 403. A superAdmin's cross-org read is audited.
- The page renders Identity, the **Mapping rules** table (one row per target: newline-separated-only
  paths, transform select, default; an empty row deletes the rule), Tokens, the **Mapping health**
  card, and **Advanced — stored configuration** (the JSON box — the only place `value_maps` is
  edited). One storage object, two views of it; saving a legacy config through the table converts it
  to v2, announced in words — in v2 a rule's paths are the whole candidate list, so the conversion is
  not a no-op.
- POSTs are routed by the `intent` field (`_source_mapping_post`; absent ⇒ `save`):
  - `save` — exactly one of the two forms (`form=table|json`) is parsed and validated
    (`ingest/editor.py` → `validate_config`) before storage. A rejection is a **200 echoing the
    input** with per-row errors and writes nothing; success is PRG; an unchanged submission is a
    no-op (no write, no audit). Audited as `ingestion_source.updated` with the changed config keys
    as dot-paths, never values (MAP-7).
  - `apply` — "Apply to unlinked alerts…": validates the submitted config, runs a read-only dry pass
    (`preview_alerts`, capped at `MAX_REMAP_ALERTS`) over the candidates and re-renders with the
    confirmation dialog showing per-target would-change / produced-no-value counts. Writes nothing.
  - `apply_confirmed` — re-validates the config carried in the hidden `pending_config` field, then
    in **one transaction** saves the mapping (only when it changed) and re-maps (`remap_alerts`),
    audited as `ingestion_source.mapping_applied` with candidate/updated/unchanged/refreshed
    counts. Over `MAX_REMAP_ALERTS` (5000) the dialog's confirm control is disabled and the server
    re-counts and refuses — **nothing** is saved, the mapping included (MAP-11).
- Candidates are one predicate (`ingest/remap.py::candidate_alerts`): alerts of *this* feed
  (`ingestion_source` FK, not the wire `source`) that are unlinked (`case` null) and open
  (`status.stage` ∈ `New`/`InProgress`). A pass writes only `title`, `description`, `summary`,
  `severity`, `date` (`REMAP_FIELDS`), and only where the new mapping disagrees — never `type`/
  `source_ref` (the dedupe/replay identity, MAP-9) and never triage fields; a text field the new
  mapping cannot answer keeps its stored value (MAP-10), and a pass that changes no field but leaves
  a stale `mapping` record rewrites just that record (`refreshed`, MAP-12). The **Mapping health**
  card reads the candidates' stored `ingestion_warnings` records only — never re-runs the rules —
  bounded by `HEALTH_SCAN_LIMIT` (2000), and says when it stopped early.

## Evidence

Contract tests (collected counts, 2026-10-10; mapping suites 2026-10-17):
`tests/conformance/test_t1_surface.py` (56), `test_unknown_fields.py` (33), `test_authz.py` (263),
`test_thehive_fixtures.py` (13, pinned golden fixtures from thehive4py 2.1.0), `test_webhook_hardening.py`
(37), `test_t2_p1_surface.py` (19, the T2 P1 surface: identity, observable types, statuses, tags,
describe), `test_t2_p2_surface.py` (12,
collaboration), `test_t2_p3_attachments.py` (9, attachments), `test_t2_p4_surface.py` (10, bulk/merge/
templates/taxonomy), `test_t2_p5_surface.py` (7, export/TTP), `test_playbook_authoring.py` (15,
Amalthea playbook extension), `test_wire_boundary.py` (3, the one-way literal boundary),
`test_observable_bulk_guard.py` (4, §6.5 blast radius on `_bulk`), `test_mapping_engine.py` (184, the
v2 mapping engine), `test_mapping_editor.py` (66, the Settings mapping editor), `test_mapping_remap.py`
(32, re-applying a mapping to existing alerts), `test_settings_source_mapping.py` (53, the source page:
org gate, editor, apply). TheHive shape decisions:
`docs/decisions/ADR-002`. Deviations: [`deviations.md`](./deviations.md). Independent verification:
[`../planning/VERIFY-2026-10-10-t2-and-phase12.md`](../planning/VERIFY-2026-10-10-t2-and-phase12.md).
