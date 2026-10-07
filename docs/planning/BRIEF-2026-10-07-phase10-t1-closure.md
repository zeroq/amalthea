# Implementation Brief — Phase 10a+10: T1 Surface Closure, Conformance, Security & Performance

**Date:** 2026-10-07 · **Planner:** big-pickle · **Status:** Approved (user chose Option 1)
**Plan:** `docs/planning/PLAN-2026-10-07-phase10-t1-closure.md`
**Source plan:** `PLAN-2026-10-03-thehive-compatible-mvp.md` §7.2 (T1 inventory), §9 Phase 10 (AC10.1–10.6)
**Audience:** `django-backend` (Wave A–B), `security-auditor` (Wave C), `qa-tester` (Wave B co-review),
`verifier` (Wave E3)

---

## Context (why this wave exists)

The plan §7.2 T1 surface is ~half-implemented. Phases 4–9 built behaviors (webhook → case →
observable → timeline → query) and the UI, but never the full T1 route inventory. Route audit
(2026-10-07) shows **twelve T1 endpoint groups missing**. Phase 10's own AC10.1 ("every T1 endpoint
has ≥1 conformance test keyed to a recorded TheHive example") cannot be met without them. User
approved **Option 1: implement the missing endpoints as part of Phase 10**.

Current gate: SQLite `make check` 491 passed / 3 skipped; Postgres (`test_pg`) 470 passed / 24
skipped. Coverage 77% (fail_under=80). Working tree clean, HEAD `40d9e73`.

---

## Wave A — Backend implementation (django-backend)

Conventions to reuse (do not reinvent): `@api_view` + `@renderer_classes([JSONRenderer])`,
`_not_found(what)`, `_resolve_case(identifier)`, `link_alert_from_identifier` (raises
`Alert.DoesNotExist`), `case_json(case, detail=True)`, `alert_json`, `compat/time.py`
(`parse_timestamp`, `to_epoch_ms`), repo `_id` = **plain UUID string (no `~` prefix)**.

Route ordering rule (LOAD-BEARING, from `cases/urls.py` docstring): sub-resources before bare
`case/<id>`; `compat.urls` `re_path(r"^.*$")` catch-all stays the **last** include in
`amalthea/urls.py`.

### A1. Alert mutators — `alerts/views.py`, `alerts/urls.py`

1. **`DELETE /api/v1/alert/{alertId}` → 204 empty.** Add `DELETE` to `alert_detail`'s method list
   (or a sibling view). Resolve like `_lookup_alert`; 404 on miss; `alert.delete()`; return
   `Response(status=status.HTTP_204_NO_CONTENT)` (no body).
2. **`POST /api/v1/alert/{alertId}/observable` → 201 `OutputObservable[]`.**
   - Resolve alert (404 on miss).
   - `InputCreateObservable`: `dataType` (string), `data` (string **or array of strings**),
     optional `message`, `tlp`, `pap`, `ioc`, `sighted`.
   - Per item: `Observable.get_or_create(data_type=..., normalized_data=canonical, defaults={"data":
     value.strip()})` + `AlertObservable.get_or_create(alert=alert, observable=obs)` — the link
     model already exists (`alerts/models.py:166`). Reuse `normalize`/`canonical_value_under`
     from `observables/extractor.py` and the `ObservableType` vocabulary lookup; unknown
     `dataType` → 400 `BadRequest` with `fields: {"dataType": [...]}` (mirror `case_observable_add`).
   - Dispatch: **none at link time.** `dispatch_observable_linked(observable, case_id)` requires a
     case (`automation/registry.py:113` — takes `case_id: UUID | str`, not optional). TheHive
     semantics match: an alert observable is a *candidate*; automation fires when it is linked to a
     case (the import/merge path already dispatches via `extract_into_case`/`add_observable`). Do
     not invent a case id. Note this decision in the commit (P10-b).
   - Response: array of `observable_json`-style objects for the alert's links (see existing
     `observable_json(link: CaseObservable)` in `core/serializers.py`; make an alert variant or
     widen the helper to accept a link with either parent — keep it DRY per serializers' module
     docstring).
   - AC: A1.

### A2. Case mutators + customEvent create — `cases/views.py`, `cases/urls.py`

1. **`DELETE /api/v1/case/{idOrName}` → 204.** Add `DELETE` to `case_detail`'s methods. Resolve
   with `_resolve_case` (404 on miss); `case.delete()`; `Response(status=204)`.
   - **Cascade semantics (verified):** `Alert.case` is `SET_NULL, null=True` — alerts survive
     unlinked. Tasks/links/timeline/customFieldValues die via CASCADE. State this in the commit
     message and plan §13 record (P10-c).
2. **`DELETE /api/v1/case/{caseId}/alert/{alertId}` (unlink) → 204.** Resolve case (404). Resolve
   alert by id **where `case=case`** (404 if not linked). Set `alert.case = None`; save. Append a
   ledger entry via `append_timeline_event(case, title="Alert removed", kind="alert-removed",
   actor=_actor(request), metadata={"alert_id": str(alert.id)})` (the friendly kind goes through
   the existing publisher so WS clients see it). Return 204. **Do not** delete the alert.
   - System-protection AC: the reconcile uses the same `remove_`-style path UI uses if any
     (grep `unlink`/`remove_alert` in `alerts/escalation.py` first and reuse).
3. **`POST /api/v1/case/{caseId}/customEvent` → 201 `OutputCustomEvent`.** Resolve case (404).
   `InputCustomEvent`: required `date` (epoch-ms int), `title` ; optional `endDate`,
   `description`. **`append_timeline_event` hardcodes `date=timezone.now()` (`cases/ledger.py:52`)
   — extend it with an optional `date: datetime | None = None` keyword (default `timezone.now()`)
   and thread `end_date` too (or set both on the row after create, inside the same
   `transaction.atomic`); the WS publish must carry the caller's date, so set the fields before
   `on_commit`. Kind `"custom"`, actor from request.** Return `custom_event_json(event)` with
   **201**. Add a `kind="custom"` CSS/kind mapping check — `_TIMELINE_KIND_MAP` in `cases/views.py`
   already maps unknown kinds to `"custom"`; verify.
   - AC: A4 (WS publish must happen — `append_timeline_event` publishes).

### A3. Task detail — `cases/views.py`, `cases/urls.py`

1. **`GET /api/v1/task/{taskId}` → 200 `OutputTask`.** Lookup `Task.objects.select_related(
   "case","assignee").filter(pk=..., case__owner_org=<org>)` — org-scoped (Idor guard, AC10.3);
   404 on miss. Return `task_json(task)`.
2. **`PATCH /api/v1/task/{taskId}` → 204.** Same lookup. Fields (only-present, "no field, no
   write"): `title`, `description`, `group`, `status`, `flag`, `order`, `dueDate`, `startDate`,
   `endDate`, `assignee` (login string → `User`; parity with `_update_case`). `status` validated
   against `TASK_STATUS_CHOICES` (`Waiting/InProgress/Completed/Cancel`); invalid → 400 naming
   the value. No updatable field → 400 `BadRequest` "No updatable field supplied". Save with
   `update_fields=[...]`. Return 204 (thehive4py `task.update()` expects None / the OpenAPI says
   204).
3. **`DELETE /api/v1/task/{taskId}` → 204.** `task.delete()`.
4. **BUG FIX (`case_task_create`):** default `status=str(payload.get("status") or "Todo")` →
   `"Waiting"` (matches model default + `TASK_STATUS_CHOICES`; `"Todo"` violates the
   `task_status_valid` CHECK constraint on Postgres).
   - **AC:** A3.
   - Also consider: `case_task_create`'s response is minimal (`_id/id/title/status`) — leave as-is
     (not §7.2's contract for create; low value to change and would ripple Phase 9 tests).

### A4. customEvent detail + customField list — `cases/views.py`, `cases/urls.py`

1. **`PATCH /api/v1/customEvent/{eventId}` → 204.** Lookup `TimelineEvent` org-scoped via
   `case__owner_org`; 404 on miss. **Guard:** only `kind == "custom"` is patchable; any other kind
   (Phase 7 ledger/system/automation) → 400 `BadRequest` "Only custom events can be updated".
   Fields: `date`, `endDate`, `title`, `description` (only-present). Return 204.
2. **`DELETE /api/v1/customEvent/{eventId}` → 204.** Same guard (`kind == "custom"`); delete;
   204. Protects audit history (Security §8).
3. **`GET /api/v1/customField` → 200 `OutputCustomField[]`.** List all `CustomField` rows
   (`CustomField.objects.all()`, order by `name`), each via `custom_field_json`.
   - **AC:** A4, A7.

### A5. Observable mutators — `cases/views.py`, `cases/urls.py`

1. **`PATCH /api/v1/observable/{observableId}` → 204.** Lookup `Observable` (org-scope: observable
   has no owner_org — scope via `case_observables__case__owner_org` or accept global observable
   read+write? **decision:** observables are globally deduplicated (Module C); PERMISSION check is
   auth-level (authenticated + not read-only key); no per-org row guard — same as
   `observable_detail` today. 404 on miss. Fields (only-present) from `InputUpdateObservable`:
   `dataType` (re-type + re-hash via existing `Observable.save()` hook — verify it re-hashes when
   data_type changes), `message`, `tlp`, `pap`, `ioc`, `sighted`, `ignoreSimilarity`. Return **204**
   (thehive4py `observable.update()` expects None).
2. **`DELETE /api/v1/observable/{observableId}` → 204.** `Observable.delete()`. Link rows die via
   CASCADE. 204.
   - **AC:** A5.

### A6. Login/logout API — `compat/views.py`, `compat/urls.py` (before the catch-all re_path)

1. **`POST /api/v1/login` → 200 `OutputUser` + session cookie.**
   - `@api_view(["POST"])`, `@permission_classes([AllowAny])`, `@csrf_exempt` (public; no session
     exists yet, but be explicit). `@renderer_classes([JSONRenderer])`.
   - `LoginInput`: `user` + `password`. `authenticate(request, username=user, password=password)`.
     Success → `login(request, user)`; return `user_json(user)` (which includes `_id` = plain UUID,
     `login`, `name`, `org`, flags) status 200.
     Failure → **400** `BadRequest` "Invalid credentials" (TheHive: `AuthenticationError`? — check
     OpenAPI `/api/v1/login` responses; if it says 400 use 400; do NOT return 401 which leaks that
     a user exists — see plan §12 AC-A6 note).
   - Org: `LoginInput` also has optional `org` — if supplied and user's org doesn't match → 400.
2. **`GET` and `POST /api/v1/logout` → 200 empty.** `@permission_classes([AllowAny])`,
   `@csrf_exempt`, call `logout(request)`, return `Response(status=200)` — no body per OpenAPI.
   Cookie cleared by `logout()`.
   - **AC:** A6.
   - Verify read-only key scope still enforced on other endpoints after adding AllowAny here (they
     are separate views — no leakage).

### A7. Serializers — `core/serializers.py`

Add (shapes from recorded 5.8.0 OpenAPI; **ISO-8601 timestamps** per deviation P10-y — match the
existing `_iso()` style in this module; the ms-epoch convention is the timeline-wire exception P8-1):

- `task_json(task)` → `_id` (str uuid), `id` (str uuid), `_type="Task"`, `_createdBy`,
  `_createdAt`, `_updatedBy`, `_updatedAt`, `title`, `group`, `description`, `status`, `flag`,
  `startDate`, `endDate`, `order`, `dueDate`, `assignee` (login), `mandatory`, `extraData={}`.
- `custom_event_json(event)` → `_id`, `id`, `_type="CustomEvent"`, `date`, `endDate`, `title`,
  `description`, `_createdBy`, `_createdAt`, `_updatedBy`, `_updatedAt`, `caseId` (str uuid).
- `custom_field_json(field)` → `_id`, `_type="customField"`, `name`, `displayName` (**= name**,
  deviation P10-d), `group`, `description`, `type`, `options`, `order`(0 if absent), `mandatory`,
  `_createdBy`, `_createdAt`.
- `user_json(user)` → `_id`, `_type="user"`, `login`, `name`, `org` (org name or id?), `hasKey`
  False, `hasPassword` True, `hasMFA` False, `locked` False, `_createdBy`, `_createdAt`.
- **AC:** A7.

### A8. Delete `compat/mappers/` (dead stubs)

Nine files: `__init__.py` (empty), `base.py`, `alert.py`, `case.py`, `custom_event.py`,
`custom_field.py`, `observable.py`, `task.py`, `user.py` — all `class XMapper(BaseMapper): pass`
with **zero importers** (verified by grep). Delete the whole package. Confirm `make check` (mypy
95-file scope) stays green. Update plan §13-13 wording (deviation P10-a).
- **AC:** A8.

### A9. Route wiring + permission hardening

1. New routes in place **before** `compat.urls` (they are separate includes under `api/v1/`, so
   as long as `compat.urls` stays last in `amalthea/urls.py` all is well — verify order).
2. **Wire `ScopePermission` (AC10.3 fix):** add `"compat.auth.ScopePermission"` to
   `DEFAULT_PERMISSION_CLASSES` in `amalthea/settings/base.py` after `IsAuthenticated`. Read-only
   API keys then 403 on POST/PATCH/DELETE everywhere. Login/logout carry explicit `AllowAny`.
   - Verify against `test_authz.py` + existing tests (no existing test asserts a read-only key
     *can* mutate — check).
   - **AC:** AC10.3.

---

## Wave B — Golden corpus + contract tests (django-backend + qa-tester co-review)

### B1. Golden corpus — `tests/fixtures/thehive/`

Keep `case_example.json` (pinned by `test_thehive_fixtures.py`). Add one structural fixture per
new endpoint group, derived from recorded examples in `/tmp/thehive-openapi.yaml` + thehive4py
request builders:
- `task_example.json` (OutputTask shape from OpenAPI ~line 51883, e.g. title "Isolate affected
  workstation from the network", status `InProgress`, group `Containment`),
- `custom_event_example.json`, `custom_field_example.json`, `user_example.json`
  (OutputUser ~line with `login` "lucas@example.com"),
- `alert_observable_example.json` (InputCreateObservable: `{"dataType": "domain", "data":
  "evil.example.com", "tlp": 2}`).
Extend `tests/conformance/test_thehive_fixtures.py` to load each fixture and assert its pinned
keys exist (title/type/`_id` shape).

### B2. Authz matrix — `tests/conformance/test_authz.py`

Parametrize over **every T1 endpoint × {anonymous, bad key, read-only key}**:
- anonymous → 401 `AuthenticationError`;
- bad key → 401;
- read-only key on mutating verb (POST/PATCH/DELETE) → 403 `AuthorizationError`;
- read-only key on GET → 200/404-by-id (never 403).
Matrix rows include the new endpoints from A1–A6 AND the pre-existing ones (alert detail/import/
merge/webhook, case collection/detail/task/observable/timeline, observable detail, query).

### B3. Contract tests — new `tests/conformance/test_t1_surface.py`

One test per §5 route (parametrized where possible), asserting status + body shape:
- DELETE×3 → 204, empty body; subsequent GET → 404.
- tasks: create via `case_task_create` (status defaults `Waiting`), GET shape, PATCH only-present,
  PATCH invalid status 400, DELETE.
- customEvent: POST 201 + WS publish (reuse `test_realtime.py` patterns or assert
  `TimelineEvent` row + kind), PATCH/DELETE 204, system-kind 400.
- customField GET list shape.
- observable PATCH (tlp/ignoreSimilarity) 204 + persisted, DELETE 204.
- alert observable POST (string + array data) 201.
- unlink: `DELETE case/{id}/alert/{alertId}` → alert survives with `case_id=None`, timeline entry.
- login: good → 200 + cookie (`client.cookies`), bad → 400, logout → 200 + subsequent
  session-authenticated call 401.
- `_id` assertions = plain UUID strings (repo convention), no `~`.

### B4. Unknown-field sweep — `tests/conformance/test_unknown_fields.py`

POST/PATCH each entity with extra unknown fields in the body: assert **not** a generic
`400 "Bad request"` (i.e., the handler either ignores them — expected — or returns a clean 400
with `fields` detail). This is AC10.2. Note P8-2: the query view builds inline 400s; only the
compat surface here.

### B5. Coverage — `make coverage`

Target ≥80% overall. New tests above cover: `cases/views.py` new handlers, `core/serializers`
new functions (0% today), `compat/views.py` login/logout. Remaining gap: `query/engine.py` 66%
(415 stmts) — if under 80% after B1–B4, add targeted error-path unit tests (listAny whitelist
branches, error-accumulation, `_between` tiles, page `extraData`) — enumerated in TODO §2.1.

---

## Wave C — Security-auditor pass (security-auditor)

Review after Wave A/B merge (endpoints + tests exist):
1. New login/logout: throttle (DRF `AnonRateThrottle` — confirm default rates apply), CSRF
   exemption scope, no user-enumeration (400-only), session fixation (`login()` rotates session —
   verify).
2. Read-only key enforcement now wired (`ScopePermission`) — confirm no hole (e.g., HEAD/OPTIONS
   → mutating? no; check read-only bypass via `case/{id}/observable` POST etc.).
3. Idor on task/customEvent/customField/alert-observable lookups (org scoping where applicable).
4. Webhook boundary (AC10.4) — 413 pre-parse + per-source/IP throttle **already implemented**
   (`ingest/views.py`); pin with tests if missing (B3 may add one: oversized payload → 413,
   throttled → 429).
5. Secrets, dependency audit (`pip-audit`), markdown/raw_payload rendering (no new paths).
6. Produce findings list; fix or record each in plan §13 (P10-f…).

## Wave D — Postgres EXPLAIN (django-backend or db-postgres)

Run `EXPLAIN (ANALYZE, BUFFERS)` on five hot paths against `test_pg` schema with seeded
representative data:
1. alert queue (`Alert.objects.filter(status=...).order_by("-date")` — `alert_status_date_idx`),
2. case detail (`case_json` detail: case + observables + timeline + tasks + automationRuns —
   verify joins use M1 indexes),
3. timeline (`TimelineEvent.objects.filter(case=...).order_by("date","id")` —
   `timeline_case_date_idx`),
4. observable graph (`observable.case_observables.select_related("case")` — `alertobs_obs_alert_idx`
   + case table PK),
5. automation dispatch (pending `AutomationRun` scan / dispatch query — `ar_pending_idx` from
   §3.1).
Record the transcripts in plan §13 record (P10-p). Fix any unbounded scan found (budget small;
M1 review says indexes already cover). AC10.5.

## Wave E — Gates, docs, verifier (planner + verifier)

- E1: SQLite `make check` + Postgres `test_pg` suite, both green (≥491/3 and ≥470/24 baseline +
  new tests).
- E2: Update `TODO.md` (§5.7 → DONE with figures; §2.1 coverage → DONE), COMPLETED.md, plan §13
  records (P10-a…P10-y, Wave A–D summaries), this brief closed.
- E3: `verifier` → `docs/planning/VERIFY-2026-10-07-phase10.md`: every AC in this doc + source plan
  (§1–§12) evaluated; deviations recorded. AC10.6.
- E4: Commit (single wave).

---

## Acceptance criteria summary (from plan §12)

| AC | Check |
|---|---|
| A1 | DELETE alert 204+gone; alert observable 201 (string & array) |
| A2 | DELETE case 204 (alerts survive SET_NULL); unlink 204 + alert survives + timeline entry; customEvent POST 201 (custom kind) |
| A3 | task GET OutputTask; PATCH only-present; invalid status 400; DELETE 204+gone; create default `Waiting` |
| A4 | customEvent PATCH/DELETE 204; system kinds 400; customField GET list |
| A5 | observable PATCH 204 persisted (dataType re-hash); DELETE 204+links gone |
| A6 | login 200+cookie+OutputUser / bad 400; logout GET+POST 200, cookie cleared |
| A7 | serializers match shapes |
| A8 | `compat/mappers/` deleted, nothing imports it, mypy/ruff clean |
| A9 | ScopePermission wired; read-only keys 403 on mutating verbs everywhere |
| B1–B4 | corpus fixtures load; authz matrix all fail-closed; T1 contract tests green; unknown-field sweep no generic-400 |
| B5 | coverage ≥80% |
| C1/D1 | auditor findings resolved/recorded; EXPLAIN transcripts no unbounded scans |
| E1–E4 | both suite baselines green + new tests; docs updated; verifier report; commit |

## Constraints & dependencies

- **No migrations** — all models exist. Do not add fields.
- `compat/time.py` is the only timestamp converter (R6). `parse_timestamp` accepts ms-int, ISO
  string, `null`.
- Repository test culture: `get_or_create` seeds (see `test_mvp_loop.py` / `test_query_api.py`),
  `transaction=True` tests need `serialized_rollback=True`.
- Do NOT regress `alert_list` GET/POST + `_create_alert` (P8-5), `alert_merge` → OutputCase
  (P8-6), query engine, Phase 7/8/9 tests.
- Run `make check` after each task group; Postgres suite before E1.
- Commit message suggestion: `Phase 10: close T1 surface, conformance, security & perf pass`.

## Deviations to record (plan §13)

P10-a: `compat/mappers/` deleted (§13-13 superseded). P10-b: alert-observable creation does not
dispatch automation (no case yet; candidates fire on import). P10-c: DELETE case cascade semantics
(SET_NULL on alerts). P10-d: customField `displayName = name`. P10-e: login/logout AllowAny +
csrf_exempt on public API auth views. P10-y: entity serializers stay ISO-8601 (vs §7.1 ms rule);
timeline wire stays ms (P8-1). P10-z: ScopePermission wired as DEFAULT permission. Plus any
auditor/verifier findings.