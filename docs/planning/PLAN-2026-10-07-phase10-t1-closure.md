# Phase 10a+10 — T1 Surface Closure, Conformance, Security & Performance

**Date:** 2026-10-07
**Status:** Approved (Option 1 — implement the missing T1 endpoints)
**Plan ref:** `PLAN-2026-10-03-thehive-compatible-mvp.md` §7.2, §9 Phase 10 (AC10.1–AC10.6), §13
**Brief ref:** `BRIEF-2026-10-03-phases-1-6.md` (Phases 4/5, T1 scope), `BRIEF-2026-10-06-query-api.md`

---

## Summary

Phase 10's original tasks (contract tests per T1 endpoint, security pass, EXPLAIN review, verifier)
presuppose the plan §7.2 T1 surface *exists*. A route audit on 2026-10-07 shows it does not: of the
~24 T1 endpoint groups in §7.2, **twelve are missing** at the route level (task detail, customEvent,
customField, login/logout API, and every `DELETE`/`PATCH` variant on entities that only ship
GET/PATCH/PUT today). The `compat/mappers/` package that deviation §13-13 reserved for "the real
logic" is still a set of empty stub classes — the actual wire rendering lives in
`core/serializers.py`, so the mappers are dead weight with a 0% coverage line.

This wave therefore becomes **Phase 10a (close the T1 surface) + Phase 10 (conformance/security/perf
verification)**, with the coverage-lifting gap (TODO §2.1, 77% → ≥80%) folded in: the new endpoints
are the natural corpus for the contract tests AC10.1 demands, and their tests are the missing
coverage.

## Goals

1. **Close the §7.2 T1 surface**: every T1 endpoint group in the plan exists, speaks the 5.8.0 wire
   shape, and fails closed (plan G2, §7.1).
2. **AC10.1**: every T1 endpoint has ≥1 conformance test keyed to a recorded TheHive example —
   replacing the single `case_example.json` fixture with a structural golden corpus derived from the
   recorded 5.8.0 OpenAPI (`/tmp/thehive-openapi.yaml`) and thehive4py 2.1.0 request builders.
3. **AC10.2**: unknown fields / unknown options are ignored or cleanly rejected — never a bare
   `400 "Bad request"` with no detail, and never silent mis-parses.
4. **AC10.3**: unauthenticated and under-scoped access to *every* T1 endpoint is proven fail-closed
   (parametrized authz matrix).
5. **AC10.4** (already implemented, verify + pin tests): webhook rate-limited per source+IP,
   oversized rejected pre-parse.
6. **AC10.5**: EXPLAIN review of the five hot paths on Postgres; no unbounded scans.
7. **AC10.6**: `verifier` report lists every AC met, deviations recorded in plan §13.
8. **Coverage** 77% → ≥80% on the existing `fail_under=80` gate (fixes TODO §2.1).

## Non-Goals

- **T2 endpoints** (plan §7.3: `page`, `comment`, `tag`, `shares`, `observable/type` CRUD,
  `user` CRUD, `POST /case/_merge/{ids}`, attachments download, `_bulk` patches, query-only
  `listTask`/`listCustomEvent`/`listCustomField` DSL steps). Out of scope; thehive4py has methods
  for some of these but they were never T1. The new task/customEvent/customField detail routes are
  **plain REST, not query DSL steps** — that matches §7.2's listing.
- **Query DSL extension**: `listTask`/`listCustomEvent` etc. are not added to the Phase 8 engine.
- **Playwright E2E**: UI is Phase 9 complete; not re-opened unless the verifier flags it.
- **Cellar/`_bulk`/file-attachment multipart** for observable uploads: attachments are T2 (§7.3).
- **Delete/cascade policy changes**: `DELETE case` cascades via FK; `DELETE alert` records nothing
  special (no recycle bin — 5.8.0's delete is "permanently delete", plan already treats it so).

## Architecture & Stack Rationale

No new dependencies. Everything lands in the existing Django 5.2 + DRF structure:

- **`alerts/views.py`, `cases/views.py`** gain the missing handlers, following the established
  `@api_view` + `JSONRenderer` + envelope pattern (`_not_found`, `_resolve_case`,
  `link_alert_from_identifier`, `case_json(detail=True)`).
- **`core/serializers.py`** gains `task_json`, `custom_event_json`, `custom_field_json`,
  `user_json` and (for login) an `OutputUser`-shaped view. `observable_json` already exists.
- **`compat/time.py`** (`parse_timestamp`/`to_epoch_ms`) is the single timestamp translator —
  no inline arithmetic (R6).
- **`core/enums.py`** `TASK_STATUS_CHOICES` already matches TheHive (`Waiting/InProgress/Completed/Cancel`);
  the `"Todo"` default in `case_task_create` is a **latent bug** (violates the `task_status_valid`
  CHECK constraint) and is fixed to `"Waiting"` (model default).
- **`compat/mappers/`** — decision below (§3.1).
- **Auth**: `POST /api/v1/login` is the only public endpoint; uses Django's
  `authenticate()` + `login(request, user)` under `SessionAuthentication`, `AllowAny`.
  Logout GET/POST calls `logout(request)`. Everything else stays on the existing
  `ApiKeyAuthentication | Basic | Session` chain (plan §7.1).

### 3.1 `compat/mappers/` fate (deviation §13-13 supersedes itself)

§13-13 said the stubs would be filled in Phases 4–5. They were not; `core/serializers.py` became the
single wire-rendering module (its module docstring says exactly that: "One module so the same entity
cannot be rendered two ways"). Three realistic options:

| Option | Effect | Verdict |
|---|---|---|
| A. Fill the mappers | Duplicates `core/serializers` logic; two modules can drift | ✗ Against the module's own docstring |
| B. Delete the package | Removes 0%-coverage dead code; deviations must be updated | ✓ Recommended |
| C. Keep empty | Coverage never reaches 80% (`fail_under` fights every run) | ✗ |

**Decision: delete `compat/mappers/`** and record the deviation in plan §13 (P10-x), updating
§13-13's wording. All wire rendering stays in `core/serializers.py` + the per-view timeline wire
function (`_timeline_event_wire` stays — it is the P8-1 API shape, distinct from the internal
`timeline_event_json`).

### 3.2 Timestamp convention reconciliation (conformance gap)

Plan §7.1 + AC2.4: **output is always epoch-ms integer**; recorded fixture (`case_example.json`)
uses `1745539200000`-style ints. `core/serializers.py` emits ISO-8601 via `_iso()`. The P8-1
timeline wire already emits ms ints and AC8 tests assert `isinstance(event["date"], int)`.

This is a real AC10.1/AC10.3-visible drift, but **changing `alert_json`/`case_json` timestamps to
ms-int now would break the Phase 9 UI templates?** — verify: UI templates read ORM objects
(`alert.date|date:...`), *not* API JSON, so the serializers are safe to conform. **Decision: keep
ISO-8601 output in `core/serializers.py` and record the deviation** (P10-y), because:

1. The `case_json`/`alert_json` shapes are consumed by the Phase 8 query surface and Phase 9 UI
   (which uses the *native* API, plan §7.4) interchangeably; ISO-8601 is the native rendering.
2. thehive4py types are loose `TypedDict`s; not one of the 50 query tests or the live AC8.4 gate
   depended on int timestamps outside the timeline route.
3. §7.1's ms rule is already broken by P8-1's own envelope wording ("ms-epoch `date`") being the
   *exception*; recording the boundary explicitly (API entity JSON = ISO; timeline wire = ms) is
   more honest than a bucket rewrite.

This is a **suggested deviation** — flagged in the Implementation Brief for the `verifier` to
confirm or reject during AC10.6. If rejected, the fallback is a one-line change in `_iso()` call
sites to `to_epoch_ms`, with UI re-check.

---

## 4. Data Model

**No migrations.** All entities exist:
`Task`, `TimelineEvent` (= customEvent), `CustomField` +
`CaseCustomFieldValue`/`AlertCustomFieldValue`, `Case`, `Alert`, `Observable`, `User`, `ApiKey`,
`CaseStatus`, `AlertStatus`, `ObservableType`.

No new fields. Notable semantics for the new routes:

- `DELETE /api/v1/case/{idOrName}` → cascade (FK `on_delete=CASCADE`); 5.8.0 returns `204` empty.
- `DELETE /api/v1/alert/{alertId}` → `204`; the alert-vs-case link rows die via FK.
- `DELETE /api/v1/case/{caseId}/alert/{alertId}` (unlink) → `204`, **only clears the alert's
  `case` FK** and writes a removed-timeline entry — it does NOT delete the alert or the case.
- `PATCH/GET/DELETE /api/v1/task/{taskId}` → statuses validated against
  `TASK_STATUS_CHOICES`; PATCH accepts `title/description/group/status/flag/order/dueDate/
  assignee/start/endDate`, applying only present fields (ADR D11, "no field, no write").
- `POST /api/v1/case/{caseId}/customEvent` → `TimelineEvent(kind="custom")` + WS publish via
  `append_timeline_event`; `204`-style success is `201` per §7.2 with `OutputCustomEvent` body.
- `PATCH/DELETE /api/v1/customEvent/{eventId}` → only events with `kind="custom"` are patchable/
  deletable; auto/system ledger rows (Phase 7/8 kinds) are protected → 400 `BadRequest` naming
  the rule, not 403 (they are not "forbidden", the operation is invalid for that row).
- `POST /api/v1/alert/{alertId}/observable` → reuses the case-observable `add_observable` +
  extraction path (escalation module); returns `OutputObservable`-shaped 201 list (single or array
  data), matching `InputCreateObservable` (data = string or array).
- `PATCH /api/v1/observable/{observableId}` → `tlp/pap/ignoreSimilarity/sighted/ioc/message`
  per `InputUpdateObservable` (dataType change re-hashes via existing `Observable.save()` hook);
  returns `204` per the recorded OpenAPI (thehive4py `update()` expects None).
- `GET /api/v1/customField` → list of `OutputCustomField`.
- `POST /api/v1/login` + `GET/POST /api/v1/logout` → `OutputUser` (login, name, org,
  `_created*`), session cookie lifecycle.

## 5. API Contract (delta vs current)

All new/modified routes below `observation: base path /api/v1/`.

| Method & Path | Current | Target (5.8.0) |
|---|---|---|
| `POST /api/v1/login` | **missing** | 200 `OutputUser` + session cookie; public |
| `GET`/`POST /api/v1/logout` | **missing** | 200 (empty body per OpenAPI) |
| `DELETE /api/v1/alert/{alertId}` | 405 (view is GET/PATCH/PUT) | 204 |
| `POST /api/v1/alert/{alertId}/observable` | **missing** | 201 `OutputObservable[]` |
| `DELETE /api/v1/case/{idOrName}` | 405 | 204 |
| `DELETE /api/v1/case/{caseId}/alert/{alertId}` | **missing** | 204 |
| `POST /api/v1/case/{caseId}/customEvent` | **missing** | 201 `OutputCustomEvent` |
| `GET`/`PATCH`/`DELETE /api/v1/task/{taskId}` | **missing** | 200 `OutputTask` / 204 / 204 |
| `PATCH`/`DELETE /api/v1/customEvent/{eventId}` | **missing** | 204 / 204 |
| `GET /api/v1/customField` | **missing** | 200 `OutputCustomField[]` |
| `PATCH /api/v1/observable/{observableId}` | 405 (GET only) | 204 |
| `DELETE /api/v1/observable/{observableId}` | 405 | 204 |
| `POST /api/v1/case/{caseId}/task` | exists | **fix default: `"Todo"` → `"Waiting"`** |

Errors stay `{"type","message"}` + 400/401/403/404/500 envelopes
(`compat.errors.thehive_exception_handler` + per-view `_not_found`).

### 5.1 Wire shapes (new serializers)

- **`task_json(task)`**: `_id`, `_type="Task"`, `_createdBy`, `_createdAt`, `_updatedBy`,
  `_updatedAt`, `title`, `group`, `description`, `status`, `flag`, `startDate`, `endDate`, `order`,
  `dueDate`, `assignee`, `mandatory`, `extraData={}` (mirror 5.8.0 `OutputTask`; field names from
  the recorded OpenAPI at lines 51883+).
- **`custom_event_json(event)`**: `_id`, `_type="CustomEvent"`, `date`, `endDate`, `title`,
  `description`, `_createdBy`/`_createdAt`/`_updatedBy`/`_updatedAt`, `caseId`.
- **`custom_field_json(field)`**: `_id`, `_type="customField"`, `name`, `displayName`, `group`,
  `description`, `type`, `options`, `order`, `mandatory`, `_created*`.
- **`user_json(user)`**: `_id`, `_type="user"`, `login`, `name`, `org`, `hasKey`, `hasPassword`,
  `hasMFA=false`, `locked=false`, `_createdBy`, `_createdAt`.
- Timestamps: these **entity** serializers follow §3.2 (ISO-8601, matching `core/serializers`
  today — deviation P10-y); timeline-wire keeps ms ints (P8-1).

## 6. UI/UX

No new pages. The Phase 9 UI pages already surface tasks (toggle), observables, timeline, alerts,
cases via ORM objects; the API additions do not change page renderers. Only benefit: the
`/api/v1/...` surface now equals what the UI/js could call later. No template changes expected;
flag in the brief for `django-frontend` to re-run a smoke check (`test_ui_loop.py`, 25 tests) after
the backend lands.

## 7. Phases / Task list (this wave)

### Wave A — Backend implementation (Phase 10a)

| # | Task | Files | AC |
|---|---|---|---|
| A1 | `DELETE /api/v1/alert/{id}` → 204; `POST /api/v1/alert/{id}/observable` | `alerts/views.py`, `alerts/urls.py` | AC10.1/10.3 |
| A2 | `DELETE /api/v1/case/{idOrName}` → 204; `DELETE /api/v1/case/{caseId}/alert/{alertId}`; `POST /api/v1/case/{caseId}/customEvent` | `cases/views.py`, `cases/urls.py` | AC10.1/10.3 |
| A3 | `GET/PATCH/DELETE /api/v1/task/{taskId}`; fix `"Todo"`→`"Waiting"` default | `cases/views.py`, `cases/urls.py` | AC10.1/10.3 |
| A4 | `PATCH/DELETE /api/v1/customEvent/{eventId}`; `GET /api/v1/customField` | `cases/views.py`, `cases/urls.py` | AC10.1/10.3 |
| A5 | `PATCH/DELETE /api/v1/observable/{id}` | `cases/views.py`, `cases/urls.py` | AC10.1/10.3 |
| A6 | `POST /api/v1/login`, `GET/POST /api/v1/logout` (public), session cookie | `compat/views.py` or new `auth` views + urls before catch-all | AC10.1/10.3 |
| A7 | Serializers: `task_json`, `custom_event_json`, `custom_field_json`, `user_json` | `core/serializers.py` | AC10.1 |
| A8 | Delete `compat/mappers/` package (dead stubs) | delete 9 files | §13-13 update |
| A9 | Route order guard: new `task/`, `customEvent/`, `customField/`, `login/`, `logout/` registered **before** `compat.urls` catch-all; sub-resources before bare `case/<id>` (keep existing pattern) | `amalthea/urls.py`-ish wiring | AC10.3 |

### Wave B — Conformance corpus + contract tests (Phase 10)

| # | Task | Files | AC |
|---|---|---|---|
| B1 | Golden corpus: replace single fixture with derived structural fixtures (one per new endpoint) from `/tmp/thehive-openapi.yaml` examples + thehive4py builders; keep `case_example.json` as-is (tests already pin it) | `tests/fixtures/thehive/*.json` | AC10.1 |
| B2 | Parametrized **authz matrix**: every T1 endpoint × {anonymous, bad key, read-only key} → 401/403 fail-closed | `tests/conformance/test_authz.py` | AC10.3 |
| B3 | Contract tests per new endpoint (parametrized over fixtures) + regression tests for A1-A7; assert `_id` = plain UUID (repo convention, no `~`); assert 204-empty bodies | `tests/conformance/` | AC10.1 |
| B4 | Unknown-field conformance sweep: POST/PATCH each entity with extra unknown fields → ignored (or clean 400 w/ `fields`), never generic 400; adds `test_unknown_fields` matcher | `tests/conformance/` | AC10.2 |
| B5 | Coverage: re-run `make coverage`; close remaining gaps (query/engine.py 66% → >80% via new contract tests; task/user serializers 0% → covered). Target: **overall ≥80%, keep `fail_under=80`** | `tests/` | TODO §2.1 |

### Wave C — Security-auditor pass

| # | Task | AC |
|---|---|---|
| C1 | `security-auditor` reviews: authn/authz (incl. new login/logout + read-only key scope `read` vs `readwrite` refusals), webhook trust boundary (already: 413 pre-parse, per-source+IP throttle — pin with tests), injection (raw_payload/markdown rendering), rate limits on login (DRF `AnonRateThrottle` — verify applied), secrets (webhook_secret_hash, api_key_hash), dependency audit (`pip-audit`) | AC10.4 |
| C2 | Fix anything surfaced by C1 (budget: small; the 413/throttle logic predates Phase 10) | AC10.4/10.3 |

### Wave D — Postgres EXPLAIN review

| # | Task | AC |
|---|---|---|
| D1 | Run `EXPLAIN (ANALYZE, BUFFERS)` on the five hot paths (alert queue, case detail, timeline, observable graph, automation dispatch) against `test_pg` schema with representative data; record plans + index usage in the plan §13 record | AC10.5 |
| D2 | Fix any unbounded scan (index tweak or query rewrite); expected: existing M1 indexes cover; verify only | AC10.5 |

### Wave E — Verifier + gate + docs

| # | Task | AC |
|---|---|---|
| E1 | Run both suites: SQLite `make check` + Postgres `DJANGO_SETTINGS_MODULE=amalthea.settings.test_pg pytest -q` (containers up) | §1 gate |
| E2 | Update `TODO.md` (§5.7 DONE; §2.1 coverage DONE; §13-13 supersede note), plan §13 records (P10-a..P10-y), COMPLETED.md | — |
| E3 | `verifier` pass: evaluate every AC in this doc + PLAN §1-§12; report in `docs/planning/VERIFY-2026-10-07-phase10.md` | AC10.6 |
| E4 | Commit (single wave; message like "Phase 10: close T1 surface, conformance, security & perf pass") | — |

## 8. Security & OWASP notes (for the auditor)

- **login**: throttle + `AllowAny` + `SessionAuthentication`; no `REST_FRAMEWORK` default change
  leaks to other endpoints (Session already in chain). CSRF: DRF `SessionAuthentication` enforces
  CSRF on session-authenticated unsafe methods; the API login POST is public (CSRF-exempt is
  default for DRF ApiView? — **must verify**: if `AuthenticationSessionProcess` rejects
  POST without CSRF, use `@csrf_exempt` on the API login view only, documented).
- **Read-only keys**: `ApiKey.scope == "read"` must 403 on every mutating T1 endpoint
  (PATCH/POST/DELETE); enforce centrally (a small `@require_write` decorator or check in audit —
  the plan §10 says "enforced at the permission layer, not the view"; add `compat/auth.py` check).
- **Idor**: every new `/{id}` route resolves via the same `_resolve_case`/link helpers that already
  scope to org; confirm the new `task`, `customEvent`, `observable` lookups do **not** leak across
  orgs (task/customEvent looked up by UUID with `case__owner_org` guard or org-scoped queryset).
- **Logout**: GET and POST both terminate the session; ensure `logout()` then `SessionAuthentication`
  cannot resurrect (return 200 with cleared cookie).
- **customEvent delete guard**: protect Phase 7 ledger kinds; only `kind="custom"` deletable;
  prevents an API client destroying audit history.

## 9. Testing

| Layer | Files | Focus |
|---|---|---|
| Unit | `tests/unit/` | `parse_timestamp` boundaries (exists), serializers new shapes |
| Integration | `tests/conformance/test_core_views.py` | each new route happy-path + 404/400/204 envelopes |
| Conformance | new `tests/conformance/test_t1_surface.py` (+ `test_authz.py` matrix) | fixtures × endpoints; unknown-field sweep |
| Interop | live/AC-style optional (skip by default; record that DELETE/204 through thehive4py is covered by unit-level `make_request` shape) | thehive4py `delete`/`update` expect None — 204 matters |
| Regression | existing suites (491/3 SQLite, 470/24 PG) must stay green | no drift |
| Perf | `EXPLAIN` script run (Postgres) | hot-path index usage |
| Static | `ruff`, `mypy` (95 files), `bandit`, `pip-audit` | clean |

**Test-harness notes:** `transaction=True` tests need `serialized_rollback=True` (pytest-django
memory). thehive4py builders available for request shapes. Repository conformance tests use
`get_or_create` seeds — match that culture.

## 10. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | T1 surface closure expands into T2 territory | Keep scope list crisp; customEvent `_bulk`/attachments/query-DSL out. New endpoints are thin wrappers over existing services |
| R2 | ISO-vs-ms conformance question re-opens in verifier | Section 3.2 pre-records the deviation; verifier confirms or flips (one-line `_iso` → `to_epoch_ms`) |
| R3 | Delete cascade removes linked alerts on case delete (TheHive behavior is soft-unlink per alert) | Record: our `DELETE case` is the **hard** delete 5.8.0 documents ("permanently delete"); unrelated alerts with a `case` FK are severed by cascade and their `case_id` cleared via FK null? — verify model FK `on_delete` for `Alert.case` (null=True?) and document behavior; if FK is CASCADE, alerts die with the case — acceptable (they are unlink-able beforehand) but must be stated in the record |
| R4 | Login CSRF friction | Verify vs DRF SessionAuthentication behavior; `@csrf_exempt` + throttle on the public login POST only |
| R5 | Read-only key enforcement without permission classes | Add `has_permission` central check in `ApiKeyAuthentication`/a mixin; keep `IsAuthenticated` default |
| R6 | Coverage not reaching 80% in one pass | B5 is budgeted deliberately; fallback = targeted unit tests on `query/engine.py` error paths (already enumerated in TODO §2.1) |

## 11. Resolved Findings (verified 2026-10-07, supersede the open questions)

1. **`Alert.case` is `SET_NULL, null=True`** (`alerts/models.py:104`). `DELETE /api/v1/case/{id}`
   therefore *unlinks* the case's alerts (row survives, `case_id` → NULL) — it does not destroy
   them. R3 wording is settled: hard-delete the case row + its cascade children (tasks,
   observables links, timeline, customFieldValues), alerts survive unlinked. Timeline gets a
   `case.deleted`-style entry **before** the case row is deleted? — no: the case is gone; record
   the unlink in the *alert's* timeline instead (alert-imported/removed entry on the case is
   deleted with it). Brief: write an `alert-removed` ledger entry only where a case still exists
   (the `DELETE case/{id}/alert/{alertId}` unlink case).
2. **CSRF on login/logout.** DRF `SessionAuthentication` enforces CSRF for *authenticated*
   unsafe methods; an anonymous `POST /login` has no session yet → no CSRF needed, but the view
   must be `@permission_classes([AllowAny])` (the default is `IsAuthenticated`, which would 401
   it). `POST /logout` with a live session cookie *will* be CSRF-checked by DRF → use
   `@csrf_exempt` + `AllowAny` on the **public** login/logout views only (documented in
   `compat/views.py`), matching TheHive's "logout via link/redirect" GET variant. Clients that
   need CSRF-bearing calls use Bearer API keys (no CSRF under `ApiKeyAuthentication`).
3. **`assignee` resolution** in task create/update: mirror `_update_case` — try `User.login`
   first, then `pk`; `None`/empty → unassign.
4. **`CustomField.displayName` does not exist** (`cases/models.py:64-77`: only `name`, `group`,
   `type`, `options`). Emit `"displayName": field.name` fallback (deviation P10-d).
5. **AC10.3 gap found: `ScopePermission` is defined (`compat/auth.py`) but never wired.** Read-only
   API keys (`ApiKey.scope == "read"`) can currently PATCH/POST/DELETE. Fix: add
   `"compat.auth.ScopePermission"` to `DEFAULT_PERMISSION_CLASSES` in
   `amalthea/settings/base.py` (after `IsAuthenticated`), so read-only keys fail closed on every
   mutating T1 endpoint. The class already raises `PermissionDenied` → 403 `AuthorizationError`
   via the exception handler. Must not break: login/logout views will carry `AllowAny` explicitly,
   overriding the default.
6. **`POST /api/v1/alert/{id}/observable` links `AlertObservable` rows** (model exists,
   `alerts/models.py:166`), NOT the case-centric `add_observable`. Brief: a small
   `alerts/escalation.py`-adjacent helper or inline `Observable.get_or_create` +
   `AlertObservable.get_or_create` + `dispatch_observable_linked(observable, case_pk=None)`
   (verify the dispatch signature accepts no case); no case involvement, per TheHive semantics.

## 12. Acceptance Criteria (this wave)

Each Wave A task: endpoint returns the §5 status/body; 404 on unknown id; 401 anon; 403 for
read-only key on mutating verbs; `make check` green.

- **AC-A1** `DELETE alert` → 204 empty; body absent after delete (GET → 404).
- **AC-A2** `DELETE case` → 204; cascade semantics per R3 record. `DELETE case/{id}/alert/{alertId}`
  → 204, alert survives unlinked, timeline shows removal.
- **AC-A3** task `GET`→OutputTask shape; `PATCH` updates only present fields (incl. status
  validation 400 on `Todo`); `DELETE`→204 + GET→404. New-task default status `Waiting`.
- **AC-A4** customEvent `POST` → 201 + WS publish; `PATCH` `204`; `DELETE` `204`; ledger-system
  kinds → 400.
- **AC-A5** observable `PATCH` → 204, fields applied, dataType change re-hashes; `DELETE` → 204,
  links removed, GET → 404.
- **AC-A6** login 200 + cookie + `OutputUser`; bad creds 400 (TheHive: `AuthenticationError`? —
  verify OpenAPI; net: 400 envelope, not 401, to avoid user-enumeration timing); logout 200, cookie
  cleared; session-authenticated calls then 401.
- **AC-A7** serializers match §5.1 shapes (unit-tested).
- **AC-A8** `compat/mappers/` gone; no imports break; mypy/ruff clean.
- **AC-B1** one fixture per new endpoint group, structurally derived; `test_thehive_fixtures`
  extended to load all.
- **AC-B2** authz matrix parametrized; every row fails closed.
- **AC-B3** contract tests green for all §5 routes.
- **AC-B4** unknown-field sweep: no generic-400, no silent mis-parse.
- **AC-B5** coverage ≥80% (`make coverage` gate passes).
- **AC-C1** auditor report; findings fixed or recorded.
- **AC-D1** EXPLAIN transcript recorded; no unbounded scans.
- **AC-E1** SQLite **and** Postgres suites green (≥491/3 + ≥470/24 baseline).
- **AC-E2** TODO/plan/COMPLETED updated; deviations in §13.
- **AC-E3** verifier report file exists, every AC listed met/deviated.

## 13. Handoff notes for the Implementation Brief

- Start with Wave A; run `make check` after each task group.
- Wave B depends on Wave A (endpoints must exist to be tested).
- C/D/E depend on B. E3 (verifier) is the only *other-agent* step; run auditors in parallel with D.
- Record deviations P10-a…P10-y (mappers deleted; ISO entity timestamps; alert-cascade; login
  CSRF; Task-assignee parity; customField displayName fallback; ScopePermission wiring; anything
  the auditor surfaces).
- Keep `compat.urls` catch-all **last**; new `task/`, `customEvent/`, `customField/`,
  `login/`, `logout/` routes before it (same ordering rule as `cases/urls.py`).
- `login`/`logout` live in `compat/views.py` + `compat/urls.py` (before its `re_path` catch-all);
  `task/`, `customEvent/`, `customField/` live in `cases/views.py` + `cases/urls.py` (distinct
  top-level prefixes, no collision with `case/<id>`); `DELETE/POST observable` handlers go into
  `cases/views.py` alongside `observable_detail`.
- Update each `__all__` (alerts/views.py, cases/views.py) with the new handlers.
- Existing `alert_list` GET/POST and `_create_alert` (P8-5) already exist — do not regress them;
  the DELETE variant is added to `alert_detail`'s view (or a sibling handler) returning `Response
  (status=204)`.