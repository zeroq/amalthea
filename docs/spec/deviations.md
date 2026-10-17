# Amalthea — Deviation Register

Every place the **implemented** system departs from AGENTS.md §2/§3, the original master plan, or a
phase's stated AC. IDs follow the master plan §13 register (`P8-*`, `P10-*`, `P11-*`, `M*` reviews);
a brief's letter alias is given where one exists (`P10-2 / y`, `P10-7 / z`, the code-only `P10-w`).

**Not a deviation:** `A3`/`A5`/`A9`/`B2` in `PLAN-2026-10-05-phase10-t1-closure.md` are *task IDs*
(endpoints to build / the authz matrix), not plan §13 deviations. Their shipped content is the
`task_detail` (P10-8 default + `_update_task`) and `observable_detail` endpoints documented in
[`api.md`](./api.md).

## Data model (AGENTS.md §3)

| # | AGENTS.md / plan says | Implemented | Status | Code pointer · Evidence |
|---|---|---|---|---|
| **M3** | one `source` field | `Alert.source` (wire string) and `Alert.ingestion_source` (FK) are two independent fields; `source_ref` is the third dedupe key | ACCEPTED | `alerts/models.py` · `REVIEW-2026-10-03-phase3-schema-gate.md` |
| **M6** | table `case` | table is `case_record` (reserved word) | ACCEPTED | `cases/models.py:122` · `REVIEW-2026-10-04-…-round2.md` |
| **M9** | keyset paging on `date` | `timeline_case_date_idx ("case","-date","-id")` + negated tie-break in `events_after` | ACCEPTED | `cases/ledger.py::events_after` · `test_ledger_keyset.py` |
| **M12** | free-enum statuses | `AlertStatus`/`CaseStatus` are DB entities; `compat/enums.py::stage_from_alert_status` | ACCEPTED | `alerts/models.py`, `cases/models.py` · `test_enum_contracts.py` |
| **M14** | `AutomationRun.playbook` free text | FK `PROTECT` **plus** immutable `playbook_name` snapshot | ACCEPTED | `automation/models.py` · `REVIEW-…-round2.md` M14 |
| **—** | observables are per-case | `uniq_obs_org_dtype_hash (owner_org, data_type, data_hash)` dedupes **per organisation** (was global until 2026-10-10 — ADR-004); cases link via M2M `case_observable` | ACCEPTED | `observables/models.py:121` · `test_observable_hashing.py` |
| **—** | `Case.number` caller-supplied | allocated server-side (`allocate_case_number`), covers `save` and `bulk_create` | ACCEPTED | `cases/numbering.py` · `test_case_numbering.py` |

## API / wire deviations

| ID | Plan said | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P8-1** | timeline `→ TimelineEvent[]` | `{"events":[…]}` — ms-epoch date, mapped kinds, `entity`/`entityId` | `cases/views.py::_timeline_event_wire` · `test_query_api.py`; VERIFY-phase10 |
| **P8-2** | 400s via `thehive_exception_handler` | query 400 built inline so the message **names** the token | `query/views.py::_bad_request` · `test_query_api.py` |
| **P8-4** | `getCase` miss undefined | miss → HTTP 200 bare `[]` | `query/engine.py::_get_case` · `test_query_api.py` |
| **P8-5** | no `POST /alert` | added T1 `alert_list` POST (`title/type/source/sourceRef` required) | `alerts/views.py` · `test_t1_surface.py` |
| **P8-6** | merge returns the alert | merge returns `case_json(detail=True)` (5.8.0 `OutputCase`) | `alerts/views.py::alert_merge` · `test_thehive_fixtures.py` |
| **P8-7** | `channels-redis` implied | pinned `channels-redis==4.3.0` | `requirements/base.txt` · plan §13 P8-7 |
| **P10-2 / y** | ISO-8601 or ms (ambiguous) | **split**: entity JSON = ISO-8601, timeline envelope = ms | `core/serializers.py::_iso`, `compat/time.py::to_epoch_ms` · `test_t1_surface.py` (ISO), `test_query_api.py` (ms) |
| **P10-3** | case DELETE cascade unspecified | hard delete; tasks/links/timeline CASCADE; `Alert.case` `SET_NULL` | `alerts/models.py:104-107` · `test_t1_surface.py` |
| **P10-5** | customField `displayName` | emitted `displayName = name` (no model field) | `core/serializers.py::custom_field_json` · `test_thehive_fixtures.py` |
| **P10-6** | login errors → 401 | bad creds → **400** (no enumeration oracle); auth views `AllowAny` + `csrf_exempt` | `compat/views.py` · `test_t1_surface.py` |
| **P10-7 / z** | read-only keys at permission layer | `ScopePermission` wired; `read` key → 403 on every mutating verb | `compat/auth.py`, `settings/base.py` · `test_authz.py` |
| **P10-8** | task create default | fixed `"Todo"` → `"Waiting"`; removed `@api_view` misuse | `cases/views.py` · `test_t1_surface.py` |
| **P10-9** | `append_timeline_event` date=now | gained optional `date`/`end_date` | `cases/ledger.py` · `test_t1_surface.py` |
| **P10-12** | slashless `/case/{id}/observable` POST | merged into one GET+POST dispatcher; both spellings | `cases/views.py::case_observable_list` · `test_t1_surface.py` |
| **P10-13** | several §7.2 rows untested | pinned (logout, alert-import-into, array-observable, per-IP throttle, slashless) | `test_t1_surface.py`, `test_webhook_hardening.py` |
| **— (no number)** | task PATCH echoes | `PATCH /task/{id}` → **204, no body** (case/alert PATCH echo; task does not) | `cases/views.py::_update_task` · `test_t1_surface.py` |
| **P10-w** | (code-only label) | sub-resources are **org-scoped through the owning case** (`_case_org_scope` → `identity.scoping.org_q`) so a guessable id is not a cross-org read | `cases/views.py:143,150` · REVIEW/plan §13; **live since 2026-10-10** — `owner_org` is now populated and `NOT NULL` (ADR-004) |

## Automation deviations

| ID | Plan said | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P10-4** | alert-observable link dispatches automation | **No dispatch at link time** — alert observables are import candidates; fire on import/merge via the case path | `alerts/views.py::alert_observable_add`; `automation/registry.dispatch_observable_linked` requires a case · `test_mvp_loop_automation.py` |
| **P12-1** | `TRIGGER_EVENTS` are the events that drive playbooks | `task.completed` **was** registered but never emitted (no `Task` receiver) — **FIXED 2026-10-09** by adding `registry.task_saved` receiver that emits `TaskCompleted(task_id, case_id)` only on transition **into** `Completed` (mirrors `case_saved` pattern). A playbook bound to `task.completed` now fires. `automation/registry.py:83-119`; `tests/conformance/test_playbook_authoring.py` (AC6.12-P2-d) |
| **P12-2** | TheHive 5 has no playbook route | **playbook authoring API + UI** (`GET|POST /playbook`, `GET /playbook/_meta`, `GET|PATCH|DELETE /playbook/{idOrName}`, `POST /playbook/{idOrName}/run`) are an Amalthea extension. Write-time config validation mirrors `executor.execute`; `_meta` serves `TRIGGER_EVENTS` + `registered_actions()`; manual run enqueues with `triggered_by="manual"`. `automation/{views,urls,playbooks}.py`, `core/serializers.py::playbook_json` · `test_playbook_authoring.py` (AC6.12-P2-a–c) |

## Wave 6.1 — T2 Phase P1 (2026-10-09)

The identity/vocabulary/tag/`describe` surface. Verified against `thehive4py` 2.1.0
(`.venv/.../thehive4py/endpoints/`): `observable/type` detail CRUD and `POST /organisation` /
`GET|PATCH /organisation/{id}` are **parity**; the rows below are the divergences.

| ID | Plan / TheHive | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P1-1** | "case/alert status CRUD" | **`caseStatus` / `alertStatus`** REST vocabularies | TheHive 5 exposes **no** case/alert status route (`thehive4py` 2.1.0 has no status endpoint module); these are an Amalthea extension so the vocabularies are editable without a query document. `cases/views.py::case_status_*`, `alerts/views.py::alert_status_*` · `test_t2_p1_surface.py` |
| **P1-2** | `POST /organisation` (bare) | bare **`GET /organisation`** added | TheHive's bare path is create-only; the read is an extension. Returns a list holding *at most* the caller's own org. `identity/views.py::organisation_collection` · `test_t2_p1_surface.py` |
| **P1-3** | types listed via `POST /query` `listObservableType` | **`GET /observable/type`** collection added | TheHive's only collection read is the query DSL; the REST list (and the symmetric `POST /observable/type`, which *is* parity) makes the vocabulary editable without one. `observables/views.py::observable_type_collection` · `test_t2_p1_surface.py` |
| **P1-4** | `describe/{model}` | `describe/{model}` **+ `describe/_all`** | `_all` (the whole catalogue keyed by model name) is an Amalthea convenience; `{model}` follows the recorded per-entity shape. `compat/views.py::describe_*` · `test_t2_p1_surface.py` |
| **P1-5** | global org directory | **tenant-scoped** | A foreign org id/name is the *same 404* as "does not exist" (no tenant-enumeration oracle); PATCH only writes the caller's own org. `identity/views.py::_own_org` · `test_t2_p1_surface.py` |

## Wave 6.1 — T2 Phase P2 (2026-10-09)

The collaboration surface: `comment`, `page`, `shares`, `flow`. Routes and bodies are pinned to
`thehive4py` 2.1.0 (`endpoints/comment.py`, `endpoints/case.py`); the rows below are the divergences.

| ID | Plan / TheHive | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P2-1** | `comment` as a `TimelineEvent` projection | **own `Comment` model** (plan §6-Q1) | A comment's `_id` must be stable across an edit, while a ledger entry is append-only. A create *also* appends a `comment` ledger event so the live case view renders it — one comment, two rows, both written in one transaction. `cases/models.py::Comment`, `cases/views.py::case_comment_list` · `test_t2_p2_surface.py` |
| **P2-2** | plan scope listed `comment` for **task** too | **case + alert only** | TheHive 5 exposes no task-comment route (`thehive4py` has none); inventing one would be a new surface, not parity. The plan's "(case/alert/task)" was speculative. `cases/views.py`, `alerts/views.py` · `test_t2_p2_surface.py` |
| **P2-3** | `OutputShare` with `profileName`/`taskRule`/`observableRule`/`owner` | single **`permissions` bag** + `canWrite` | This deployment has no per-task/observable rule engine; the profile-shaped keys degrade to the value the stored row implies and the real grant is exposed as `permissions` + `canWrite` (TheHive has neither key). `core/serializers.py::share_json`, `cases/models.py::Share.can_write` · `test_t2_p2_surface.py` |
| **P2-4** | `flow` as a TheHive shape | **extension** | TheHive 5.8 has no REST `flow` route and `thehive4py` 2.1.0 has no `flow` module; shaped after the entities it links (case/alerts/observables/tasks) and explicitly never inlines `raw_payload`. `cases/views.py::case_flow` · `test_t2_p2_surface.py` |
| **P2-5** | plan said `shares` for **case/alert** × org | **case only** | TheHive's share routes are case-only (`/api/v1/case/{id}/shares`); the plan's alert half was speculative and is dropped. `cases/models.py::Share` · `test_t2_p2_surface.py` |
| **P2-6** | — | page create emits a **`page` WebSocket event** | TheHive has no realtime contract, but Module B requires the collaborative surface to stay synced, so a page create publishes on commit via the `realtime.publisher` choke point. `cases/views.py::case_page_list` · `test_t2_p2_surface.py` (AC-a) |
| **P2-7** | plan P2 was endpoints only; UI unchanged | **UI wired to the new entities** (follow-up) | The note form now creates a real `Comment` in the same transaction as its ledger event, so `GET /case/{id}/comment` and the timeline agree; and `case_detail.html` renders a Pages card that live-appends `page` events instead of letting them fall into the timeline renderer. `ui/views.py::case_comment`/`case_detail`, `ui/templates/ui/_page_entry.html`, `ui/static/ui/live.js` · `test_ui_loop.py` |

## Wave 6.1 — T2 Phase P3 (2026-10-09)

Attachments. The upload contract is TheHive 5.8 (`POST /case/{id}/attachments` multipart under the
`attachments` field, `…/attachment/{id}/download`, `DELETE …/attachment/{id}`); the rows below are
the divergences, all on the safety side.

| ID | Plan / TheHive | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P3-1** | TheHive stores under the client filename | **opaque server-generated key** | The blob name is `attachments/<uuid4hex><sanitized-ext>` — the client's filename is display data only, so a traversal filename has nothing to climb and two uploads of the same name never collide. `cases/views.py::_attachment_storage_path`/`_safe_attachment_name` · `test_t2_p3_attachments.py` (AC-a) |
| **P3-2** | TheHive accepts any content type | **allowlist + magic-byte sniff** | A declared type outside the allowlist is 415, and bytes whose signature contradicts the declared type (PNG as `text/plain`) is 415; text-ish types must decode as UTF-8. No `libmagic` dependency. `cases/views.py::_sniff_attachment` · `test_t2_p3_attachments.py` (AC-a) |
| **P3-3** | `OutputAttachment.hashes` is a list | single **SHA-256** in the list | One hash is computed at write time; the wire keeps TheHive's list shape (`hashes=[sha256]`). `core/serializers.py::attachment_json` · `test_t2_p3_attachments.py` (AC-b) |
| **P3-4** | — | **413 before write** | The size cap (`ATTACHMENT_MAX_BYTES`, env `AMALTHEA_MAX_ATTACHMENT_BYTES`) is checked before the blob reaches storage; a rejected request leaves no blob and no row. `amalthea/settings/base.py`, `cases/views.py::case_attachment_list` · `test_t2_p3_attachments.py` (AC-a) |

## Wave 6.1 — T2 Phase P4 (2026-10-09)

Bulk edits, case merge, case templates and the aggregate taxonomy. The four `PATCH …/_bulk`
endpoints, `POST /case/_merge/{ids}` and `POST /case/_bulk/caseTemplate` are TheHive 5.8 parity
(`thehive4py` 2.1.0 `endpoints/{case,alert,task,observable}.py`); the rows below are divergences.

| ID | Plan / TheHive | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P4-1** | plan §6-P4 framed `_bulk` as `POST /query` operations | **REST `PATCH /{case,alert,task,observable}/_bulk`** | Ground truth is TheHive 5's REST PATCH (verified in `thehive4py` 2.1.0); the plan's query-engine framing was speculative. Per-item isolation + status reporting is the AC. `compat/bulk.py`, `cases/views.py::case_bulk_update` · `test_t2_p4_surface.py` (AC6.1-P4-a) |
| **P4-2** | — | **`case/_merge` replay is idempotent** | A second POST of the same `<ids>` finds the absorbed cases gone, so it resolves to the surviving target and returns it rather than 500/duplicating provenance. One `case-merged` ledger event per absorbed case is written before `source.delete()`. `cases/merge.py::merge_cases` · `test_t2_p4_surface.py` (AC6.1-P4-b) |
| **P4-3** | — | **`taxonomy` + case-template CRUD are extensions** | TheHive 5 exposes no aggregate taxonomy route and no case-template module in `thehive4py` 2.1.0; both are Amalthea additions so the UI can drive its pickers. `cases/views.py::taxonomy`/`case_template_*` · `test_t2_p4_surface.py` |

## Wave 6.1 — T2 Phase P5 (2026-10-09)

Procedures (ATT&CK techniques observed on a case/alert) and case export.

| ID | Plan / TheHive | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P5-1** | `InputProcedure.occurDate` is an epoch-ms integer | **accepted, re-emitted ISO-8601** | House style is ISO-8601 in entity JSON (deviation P10-2); the parser still accepts TheHive's ms integer so clients need no change. `compat/procedures.py`, `core/serializers.py::procedure_json` · `test_t2_p5_surface.py` |
| **P5-2** | (no TheHive route) | **`ttp` vocabulary + `ttpId` on a procedure** | TheHive stores a free-text `patternId`; Amalthea adds a first-class, editable TTP vocabulary (`GET|POST /ttp`, `GET|PATCH|DELETE /ttp/{idOrName}`) and a nullable `Procedure.ttp` link, with the P1 in-use refusal on DELETE. `cases/models.py::TTP`, `cases/views.py::ttp_*` · `test_t2_p5_surface.py` (AC6.1-P5-b) |
| **P5-3** | `GET /case/{id}/export` returns an encrypted `.thar`; `password` param | **plain JSON document, `password` ignored** | The export is the one place `raw_payload` is inlined (deliberate — it is the case archive); it is **not** encrypted and the `password` query parameter is accepted only for client compatibility. `cases/views.py::_case_export_document` · `test_t2_p5_surface.py` (AC6.1-P5-a) |
| **P5-4** | procedure PATCH echoes the row | **204, no body** | Matches the task-PATCH precedent (deviation "task PATCH echoes"); the change is observable via a follow-up GET. `cases/views.py::_update_procedure` · `test_t2_p5_surface.py` |

## Security hardening findings

Source: security-auditor Wave C, carried as **P10-10** (F1/F6/F3/F5/F7 fixed in the burst). **F9 is
now fixed** (2026-10-08 hardening wave); **F2 is now fixed** (2026-10-10 tenant-isolation wave,
ADR-004).

| ID | Finding | Status / follow-up |
|---|---|---|
| **F2** | case/alert resolution is org-blind (`owner_org` never populated, so scoping is a no-op); **`Observable` PATCH/DELETE is global** (any authenticated user can mutate any observable) | **FIXED 2026-10-10 — superseded by ADR-004.** `Case/Alert/Observable.owner_org` are `NOT NULL`; every resolution/mutation runs through `identity/scoping.py` and cross-tenant access is a uniform **404**. Observable dedupe is per-org (`uniq_obs_org_dtype_hash`). Shipped across plan `PLAN-2026-10-10-tenant-isolation.md` P1–P7; verdict **CLEAR TO BUILD ON** (`docs/reviews/SECURITY-2026-10-10-tenant-isolation-final.md`). |
| **F9** | prod did not fail closed on `SECRET_KEY` and did not force secure cookies | **Fixed 2026-10-08** — `amalthea/settings/prod.py` now raises `ImproperlyConfigured` unless `DJANGO_SECRET_KEY` is ≥50 chars and `DJANGO_ALLOWED_HOSTS` is non-empty, and forces `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/`SECURE_SSL_REDIRECT`/HSTS/`SECURE_CONTENT_TYPE_NOSNIFF`/`X_FRAME_OPTIONS=DENY`; proven by `tests/conformance/test_prod_settings.py` (9 tests). Plan: [`PLAN-2026-10-08-secret-hygiene.md`](../planning/PLAN-2026-10-08-secret-hygiene.md) |

## Wave 6.2 — O1–O4: SuperAdmin Organisation & User Management (2026-10-11)

Per `PLAN-2026-10-10-org-management.md`, the superAdmin-only Settings area (API + UI).

| ID | Plan / TheHive | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **O1-1** | TheHive: `POST /organisation` (bare) only | **`GET|POST /api/v1/organisation`**, `GET|PATCH|DELETE /api/v1/organisation/{idOrName}` | TheHive's bare path is create-only; Amalthea adds superAdmin directory (GET all with counts), create, update, guarded delete. Non-superAdmin GET/PATCH own org unchanged. `identity/views.py`, `tests/conformance/test_org_user_management.py` |
| **O1-2** | no user collection/mutation | **`GET|POST /api/v1/user`**, `GET|PATCH|DELETE /api/v1/user/{idOrLogin}`** (superAdmin) | TheHive 5 has no user management REST API. Amalthea adds full CRUD with password validation, last-superAdmin guards, audit logging. `identity/views.py`, `tests/conformance/test_org_user_management.py` |
| **O1-3** | Organisation: no timestamps | **`Organisation` extends `TimeStampedModel`** (`created_at`/`updated_at`) | Additive migration; `organisation_json` now emits real timestamps (was `null`). `identity/models.py`, `core/serializers.py` |
| **O2-1** | Org delete: cascade or undefined | **Guarded delete** — pre-flight blockers (cases/alerts/observables/tokens) → **409**; members require explicit `reassign` or `delete` | TheHive 5 delete is undefined for non-empty orgs. Amalthea enforces safe deletion with an **atomic** transaction (member moves/removals + org delete share one `transaction.atomic()`), refuses `reassign` without a real different target, and audits. `identity/services.py::delete_organisation` (shared by `identity/views.py` and `ui/views.py`) |
| **O3-1** | User delete: no self/last-admin guards | **Guards**: cannot delete self, cannot delete/demote/deactivate last active superAdmin | Prevents tenant management lock-out. `identity/services.py::update_user`/`delete_user`, `identity/views.py` |
| **O4-1** | TheHive UI: no self-service tenant admin | **`superAdmin`-only Settings UI** — `/settings/organisations` + `/settings/users` (list/add/edit/delete), rail item, `,` shortcut, `<dialog>` modals (blocked-delete counts + member-choice) | Amalthea extension; non-superAdmin gets `ui/404.html`. `ui/views.py`, `ui/urls.py`, `ui/templates/ui/settings_*.html`. **No TheHive `compat/` parity** (decision Q4). |


## Wave 6.3A — Settings UI hardening + security F3/F5–F10 (2026-10-11)

Per `PLAN-2026-10-10-org-management.md` §13A; findings in `SECURITY-2026-10-11-org-user-management.md`.

| ID | Deviating from | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W63A-1** | §5.4/§13A prose: "create/**edit** as server-side forms in a `<dialog>`" | **Create/delete = `<dialog>`; edit stays a standalone page** | No AC is violated. Edit already worked as a plain CSRF POST page and the wave's goal (CSP + no-JS CRUD + XSS sink removal) is met for it; converting edit to a dialog was out of scope. Verifier deviation **D1** (Low). |
| **W63A-2** | Literal "externalise the modal `<script>`" | **`_modal.html` became a parameterised markup partial** (`form_id`, `form_method`, `body_template`) with the four bodies as server-rendered partials; behaviour moved to `ui/static/ui/modal.js` | The old design injected HTML from `escapejs` strings via `innerHTML` (F3). Server-rendering the bodies removes the sink entirely and gives the no-JS fallback; a literal JS refactor would not. `ui/templates/ui/_modal.html`, `ui/static/ui/modal.js`, `ui/static/ui/modal.css`. |
| **W63A-3** | "last-superAdmin TOCTOU" fix implied to be universal | **Serialisation is Postgres-only** — `select_for_update` is a no-op on SQLite (dev/test) | Locks are proven by spy tests on the SQLite inner loop and take effect on the Postgres target; a real concurrency test needs Postgres (the `test_pg` schema currently errors on setup — separate follow-up). `identity/services.py::lock_active_superadmins` + the two decorators. |
| **W63A-4** | "`is_active` rejects non-boolean strings" | **Real bools + explicit `"true"/"false"` (case-insensitive) accepted; anything else → 400** | Guards the `bool("false") is True` bug (F8) while keeping form round-trips working. `identity/services.py::parse_bool`. |
| **W63A-5** | Finding F9 "`secret_hash` never displayed" | **`exclude`, not just `readonly_fields`** — and admin add disabled | Removing it from `readonly_fields` alone would have made the Argon2 hash an *editable* widget; the hash is non-null so the add form could never be valid anyway (tokens are minted by `IngestionToken.create_token`). `identity/admin.py`. |

**Residual risk (accepted, Low):** no DB-level ≥1-active-superAdmin invariant (Django admin/management/SQL can still zero admins); the share form does not validate the target against `available_orgs`; UI superAdmin guards run inside the lock. Security sign-off: `docs/reviews/SECURITY-2026-10-11-wave63a.md`; verification: `docs/planning/VERIFY-2026-10-11-wave63a.md`.


## Wave 6.3B — repo-wide ADR-003 compliance (TODO §6.15, 2026-10-11)

Per `PLAN-2026-10-10-org-management.md` §13B. Closes the repo-wide inline-asset violation left open after §13A.

| ID | Deviating from | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W63B-1** | §13B prose: "externalise every remaining inline `<script>`" (implying a new JS file each) | **Four of the five inline scripts were deleted outright** — a new `prettyjson` server-side filter renders the config blob into the `<pre>`/`<textarea>`, so no client pretty-printer (nor a `json_script` `<script type="application/json">`) is needed at all | Those scripts existed *only* to `JSON.parse` a `json_script` blob and re-stringify it. Deleting them is strictly better under CSP: fewer files and no inline JSON script either. The AC ("no inline script in the rendered page") is met **more** completely than the plan specified. `ui/templatetags/ui_tags.py::prettyjson`. |
| **W63B-2** | Playbook form JS assumed to be a straight move into a static file | **Rewritten, not copied** — `<template>` clone + one delegated click listener instead of `innerHTML` string building | The original `addHeaderRow()` built markup with `innerHTML` — the same sink class §13A removed from the modal. Lifting it verbatim into the new file would have re-introduced it. `ui/static/ui/playbook_form.js`. |
| **W63B-2b** | — | **Latent bug fixed alongside:** the header rows had no `{% for %}` at all, so all four inputs shared one literal name and **no** header was ever submitted without JS | The four rows are now indexed and server-rendered (`header_key_0..3`), so an HTTP playbook saves with zero JavaScript. `ui/templates/ui/playbook_form.html`. |
| **W63B-3** | "externalise the styles too" | **New component classes, not Tailwind utilities** in `app.css` (`.notfound`, `.field-row`, `.filters`, `.chips-scroll`, `.btn-tight`, …) | `app.css` is its own Tailwind input (no template rescan), so a new Tailwind *utility* used only in a template would never be generated; plain component CSS is the only reliable path until §7.4 splits source from artifact. |

**Not a deviation, recorded for clarity:** the guard is a static template scan (`tests/conformance/test_ui_inline_assets.py`), not a render-time assertion — matching §13A's approach but applied to all `ui/templates/ui/*.html` (116 cases). `{% comment %}`/`{# #}` blocks are stripped first, so prose that *mentions* `<script>` (design notes in `_modal.html`) is not a false positive.

**Residual risk (accepted, Low):** the playbook **edit** form still does not restore its stored action config (the `action` select is unselected on load and `trigger_event` is rendered `disabled`), so saving an edit can reset a playbook's action. Pre-existing UI gap, outside §6.15's ADR-003 scope; logged in the UI gap table.


## Wave 6.16-P1 — UI completeness, phase 1 (TODO §6.16, 2026-10-11)

Per `PLAN-2026-10-11-ui-completeness.md` §8 P1. Verification: `docs/verification/VERIFY-2026-10-11-p1-ui-completeness.md`.

| ID | Deviating from | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W616P1-1** | “wire `handler404/403/500` in `amalthea/urls.py`” (import the views, as the first implementation did) | **Dotted-string handlers** (`handler404 = "ui.views.handler404"`); the DEBUG-only `__test_*__` routes the first pass added were removed | `ui` is deliberately **outside** the mypy pinned scope (`pyproject [tool.mypy].files`). Importing `ui.views` from an in-scope module made mypy check the whole untyped package for the first time (74 latent errors) and broke the gate. Django resolves string handlers via `get_callable`, so this is also the idiomatic form. `amalthea/urls.py`; `test_error_handlers_are_dotted_paths_that_resolve`. |
| **W616P1-2** | Attachments wired by pointing at the existing page | **Contract fixes**: the file input is `name="attachments"` (the API reads `request.FILES.getlist("attachments")`, not `attachment`); the list renders `created_by.login` (the field is `created_by`, not `uploaded_by`); upload/delete redirect to the case, not the standalone attachments page | The first frontend pass guessed the field/variable names; without these the upload would 400 and the “uploaded by” column would always be em-dash. `ui/templates/ui/case_detail.html`, `ui/views.py::case_detail`. |
| **W616P1-3** | Styles via Tailwind utilities | **Component classes** in `app.css` (`.btn-err`, `.tenant-indicator`, `.attachment-upload`, `.card-narrow`, `.card-spaced`, `.rail-links a i`) | Same constraint as W63B-3: `app.css` is its own Tailwind input (no rescan). `.btn-err` was referenced by ~15 templates but **defined nowhere** — a pre-existing gap fixed here (destructive buttons were indistinguishable from neutral ones). `ui/static/ui/app.css`. |
| **W616P1-4** | “add icons to the rail” | **Substituted one glyph and extended the subset**: `fa-gauge-high`/`fa-book`/`fa-rss`/`fa-gear` were added as `.fa-X::before` rules to the committed Font Awesome subset; `fa-building-lock` (used by the cross-org badge in three templates) was replaced with the already-subsetted `fa-shield-halved` | The bundled `fa-solid-900.woff2` has **no** `unicode-range` (full FA6 Free font), so a CSS rule suffices for the four known-Free icons; `fa-building-lock` could not be confirmed present in FA6 **Free**, and a missing glyph renders as a blank box. New guard `tests/conformance/test_ui_icons.py` makes any future undefined `fa-*` a test failure. |
| **W616P1-5** | `500.html` extends `base.html` like `404.html`/`403.html` | **Standalone** `500.html` rendered by `loader.render_to_string` (no `request=`), so no context processor runs | `render()` would run the `auth` + `messages` context processors: the `messages` processor touches session storage and the lazy user can be evaluated, so an authenticated request issued queries (measured: 2) at exactly the moment a 500 is most likely to mean “the DB is down”. `ui/views.py::handler500`; `test_handler500_zero_queries_authenticated_user`. |
| **W616P1-6** | Q2 recommendation (“delete the bulk stub”) | Done as recommended, **plus a role gate** on `case_template_create/edit/delete` | The security review found template mutations were `@login_required` only, while the analogous playbook authoring is role-gated; the gate now mirrors playbook authoring and answers with the uniform 404. Raised by `docs/reviews` → P1 remediation. |
| **W616P1-7** | “every primary action reachable without a mouse, shortcuts discoverable” (plan §7.2a) | Cheat-sheet opened by `?` **and** by a visible rail-foot control | A keyboard-only opener is not sufficient for WCAG 2.1.1 (keyboard *and* pointer reachable); reuses the existing `data-modal-open` mechanism, so no new JS. `ui/templates/ui/base.html`. |

**Not a deviation, recorded for clarity:** the plan’s `Q3` (HTMX scope), `Q4` (audit viewer read-only) and `Q5` (search over cases+alerts+observables) affect **later** phases and are unchanged. **`Q1` (a runtime Markdown + sanitiser dependency) remains open and still gates P5/D3** — no runtime markdown/sanitiser package exists in `requirements/base.txt`.

**Residual risk (accepted, Low):** duplicate URL *names* for `ui-case-template-list` and the procedure routes pre-date this wave (present in `HEAD`) and were left untouched; two new guard modules are source-level parsers (they fail on a shape change, which is the intended behaviour but needs a matching edit).

## Wave 6.16-P2 — UI completeness, phase 2 (TODO §6.16, 2026-10-11)

Per `PLAN-2026-10-11-ui-completeness.md` §8 P2 (C1 alert triage, C2 case edit, C3 observable edit/delete).
New guard: `tests/conformance/test_ui_p2_wiring.py` (24 cases).

| ID | Deviating from | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W616P2-1** | The UI triage form was to submit an `AlertStatus` **id** | The control submits `AlertStatus.value` (New/Triaged/Dismissed) and the view validates against the **existing** vocabulary only (never `resolve_alert_status`) | The first backend pass posted the pk while the form contract was a value; worse, feeding the tolerant resolver a form field would let a crafted POST **mint** a new `AlertStatus` row (ADR-002 §D11 is deliberately tolerant for the API). The API's PATCH keeps the tolerant resolver, so this is a UI/API *restriction*, not a behaviour change. `ui/views.py::alert_status`, `ui/templates/ui/alert_detail.html`. |
| **W616P2-2** | Case edit was to re-implement the field set | Extracted `cases/update.py` (`CASE_SCALAR_FIELDS`, `CaseUpdateError`, `_set_case_fields`, `_apply_case_tags`, `apply_case_update`) and pointed **both** the API `_update_case`/`_bulk` and `ui-case-edit` at it | One definition for the field set, tag replacement/deltas, status/tag timeline kinds and the error shape, so the two surfaces cannot drift. The UI additionally **validates the posted status against the existing `CaseStatus` vocabulary** before calling (same reason as W616P2-1). `cases/update.py`, `cases/views.py`, `ui/views.py::case_edit`. |
| **W616P2-3** | Plan C3 says "observable edit" (implying the value is editable) | `data` is **immutable** on both the API and the UI; the editable set is `dataType`, `message`, `tlp`/`pap`, `ioc`, `sighted`, `ignoreSimilarity`, `enrichment_data` | `data` is the per-org dedupe key (`uniq_obs_org_dtype_hash`); the API's `_set_observable_fields` never accepted it, so "editing the value" would be a new, constraint-breaking semantic. The form shows it read-only with an explanatory hint. `ui/views.py::observable_edit`, `ui/templates/ui/observable_detail.html`. |
| **W616P2-4** | Observable edit was to apply fields directly | Edit **and** delete both pass through `_observable_cross_case_guard`; the UI renders a required acknowledgement checkbox **inside** the form (`name="force"`) for a multi-case observable | The API PATCH requires `?force=true` for a shared artifact; an edit path without it would be a weaker second door. The checkbox must live inside the `<form>` — the first frontend pass put it in the modal body and sent a hidden `force=1`, which (a) let the browser submit without acknowledgement and (b) never matched the view's `== "on"` test, so **every** shared delete was silently refused. `ui/views.py::observable_edit/observable_delete`. |
| **W616P2-5** | The observable form was to offer `TLP_CHOICES` | The observable form lists TLP `0..3` only | `cases.views._set_observable_fields` bounds observable TLP to `0..3`, while `TLP_CHOICES` also carries `4/Unknown` (valid on `Case`/`Alert`). Offering `4` would only produce a validation error. The API bound is unchanged (a candidate for a future `TLP_MAX` reconciliation). `ui/views.py::observable_edit`. |
| **W616P2-6** | (defect found in-wave, no plan text) | `resolve_alert`/`resolve_observable` now catch `ValidationError`/`ValueError`/`TypeError` and return `None` (uniform 404) | A non-UUID id raised Django's `ValidationError` out of the pk lookup → **500**, an existence oracle and the exact F7-class trap `link_case_from_identifier` already guards. Found by the tenant-scoping route enumeration once the new `<str:…>` routes were discovered. `identity/scoping.py`; `test_tenant_scoping.py::TestUIRouteEnumerationConformance`. |
| **W616P2-6 bug-fix detail** | — | `observable_delete` captures `str(observable.id)` and the target org **before** `delete()` | Django nulls the pk on the deleted instance, so the audit row recorded `target_id="None"`. `ui/views.py::observable_delete`; asserted by `test_observable_delete_single_case`. |

**Not a deviation, recorded for clarity:** the seeded alert vocabulary is exactly `New / Triaged / Dismissed`(`+Imported`) with stages `New/InProgress/Closed/Imported`, so the plan's vocabulary and the DB agree. The case-edit `TLP/PAP/severity` "no preselection" defect is a template bug (`form_values.tlp` is a `str`, choices are `int`), not a plan deviation, and is fixed.

**Residual risk (accepted, Low):** a re-typed observable that collides with an existing `(owner_org, data_type, data_hash)` raises `IntegrityError` (500) on **both** the API and the UI — pre-existing, parity-preserving, and out of P2 scope. `resolve_alert`/`resolve_observable` now swallow `ValidationError`, so an unparseable id is a 404 rather than a 500.

### Post-verification security remediation (2026-10-11)

`docs/reviews/REVIEW-2026-10-11-p2-ui-mutations.md` returned one MEDIUM and two LOW; all are fixed, and the
detail is in the plan `§13c` and `COMPLETED.md`. Summary (not new plan deviations — corrections of review
findings):

| ID | Was | Now |
|---|---|---|
| **M1** (MEDIUM) | `_resolve_login` was org-blind, so a crafted `assignee` bound a case/task to another org's user (and acted as a cross-tenant login oracle) | `_resolve_login(value, *, org=None)` scopes login **and** UUID; the case applier and the task path pass the owner org; the UI rejects a non-member with a clear error (API PATCH, bulk and UI all fixed) |
| **L1** | Out-of-range `severity`/`tlp`/`pap` hit the DB `CheckConstraint` as an unhandled `IntegrityError` (500) | `_set_case_fields` range-checks against `core/enums` → `CaseUpdateError` (400 / form error) on both surfaces |
| **L2** | The `AuditLog` row was created outside the mutation transaction — a failed audit left an unlogged change | Audit shares the mutation's `atomic()` in `case_edit`, `observable_edit` **and** `alert_status` |
| **NEW-1** | The case-edit assignee `<select>` was prefilled with the assignee **UUID** while its option values are **logins**, so the browser submitted the first option and an untouched save unassigned the case | Prefills `case.assignee.login`; guard test asserts the login is `selected` |
| **NEW-2** | `alert_status` saved the alert then audited outside a transaction (same class as L2) | Save + audit + case-ledger entry share one `atomic()` |
| N1 | Re-render helpers used `dict(QueryDict)` (list values, `['x']` echoes) | Read scalars via `.get()`; `case_edit` also passes `is_cross_org` |

Verified by the appended "Re-audit 2026-10-11" (security) and "Re-verification 2026-10-11" (verifier) sections in the
two reports above; `tests/conformance/test_ui_p2_wiring.py` grew 24 → 30 cases.


## Wave 6.16-P3 — UI completeness, phase 3 (TODO §6.16, 2026-10-12)

Per `PLAN-2026-10-11-ui-completeness.md` §8 P3 (A5 — Procedures/TTP UI).
New guard: `tests/conformance/test_ui_procedures.py` (22 cases) + `tests/conformance/test_ui_css_tokens.py` (2 cases).

| ID | Deviating from | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W616P3-1** | Plan says "invalid ATT&CK id → message, not 500" (referring to `ttpId`) | **Also validates `patternId` (required)** and `occurDate` (timestamp format) | The TheHive wire shape treats `patternId` as optional; the UI form marks it required. Missing `patternId` now shows "ATT&CK ID is required" instead of creating a procedure with an empty ATT&CK ID. `ui/views.py::case_procedure_create` (early validation). |
| **W616P3-2** | Bulk endpoint (`ui-case-procedures-create`) was a JSON API proxy | **Rewritten as a UI POST** with form field parsing (indexed `patternId_0`/`patternId_1`… or list-style `patternId`×N) and per-item messages | The original view delegated to the DRF API which returns JSON; a browser form would receive a JSON blob. The UI version parses form data, reuses `parse_procedure`/`create_procedure` in a transaction, and reports created/skipped counts via `messages`. `ui/views.py::case_procedures_create`. |
| **W616P3-3** | Duplicate route definitions for procedures in `ui/urls.py` | **Removed the duplicate block** (lines ~121-130), keeping the first definition (lines ~75-83) | The duplicate definitions were present in `HEAD` pre-P3. Removed to avoid confusion and potential conflicts. `ui/urls.py`. |
| **W616P3-4** | `case_procedure_create` was `@require_GET` delegating to API | **Changed to `@require_POST`** implementing the mutation directly | `@require_GET` on a write route is incorrect by construction (browsers cannot GET a mutation). The view now handles the POST directly, mirroring the P2 mutation pattern (`case_edit`, `alert_status`, etc.). `ui/views.py::case_procedure_create`. |
| **W616P3-5** | The API's bulk endpoint (`POST /api/v1/case/{id}/procedures`) has no item cap | **UI caps one bulk POST at `MAX_BULK_PROCEDURES = 100`** (over the cap → flash + redirect, nothing created) | The item index set is form-controlled, so it must be bounded explicitly rather than inherited from `DATA_UPLOAD_MAX_NUMBER_FIELDS` (1000) — each item costs a row insert plus a `ttpId` lookup. Far above real usage (nobody attaches 100 procedures by hand). `ui/views.py::MAX_BULK_PROCEDURES` · `test_procedures_bulk_create_rejects_more_than_the_cap`. |
| **W616P3-5a** | n/a — defect fixed in-wave | **Per-item savepoint in the bulk POST** | Items ran inside one atomic block with no savepoint, so a DB-level failure in one item poisoned the transaction and every *later* item failed with `TransactionManagementError` (one bad row silently became N). Each item now runs in `transaction.atomic()`; the regression test fails without the fix. `ui/views.py::case_procedures_create` · `test_procedures_bulk_create_survives_a_db_level_failure_in_one_item`. |
| **W616P3-5b** | — | **Flash messages carry a generic string; the exception goes to the logger** | `except Exception as exc: messages.error(f"...{exc}")` leaked schema/SQL text to the analyst. Note that seven *pre-existing* UI flashes still do this (TODO §11.4). `ui/views.py::case_procedure_create`/`::case_procedures_create`. |
| **W616P3-6** | Plan does not ask for a stylesheet guard | **Added `tests/conformance/test_ui_css_tokens.py`** (undefined `var(--color-*)` → test failure; `outline:none` → test failure) | An undefined custom property makes the whole declaration invalid at computed-value time, so app.css silently dropped every border/badge colour from the new rules (7 invented token names) — the second time this class of bug shipped. The guard also protects the WCAG 2.4.7 focus ring. |

**Not a deviation, recorded for clarity:** the Procedures card renders descriptions through the existing sanitized `markdown` filter (ADR-005, `nh3` allow-list), consistent with P5/D3. No new sanitizer or Markdown dependency introduced. The UI imports the canonical `_resolve_ttp_value` from `cases.views` rather than re-implementing the ttpId resolution (it was duplicated twice during implementation).

**Residual risk (accepted, Low):** the bulk create form is not yet reachable from the UI (only the single create is on the case detail page). The bulk endpoint exists and is tested but has no entry point — consistent with the plan's scope (P3 only requires the single attach form on case detail). The attach form is rendered for read-share viewers too (they get the uniform 404 on POST) — that visibility question is the same role policy as TODO §11.1/§11.4.

**Verification:** `docs/verification/VERIFY-2026-10-12-ui-completeness-p3.md` (5/5 ACs met) · **Security:** `docs/reviews/REVIEW-2026-10-12-ui-completeness-p3.md` (0 Critical / 0 High; follow-ups in TODO §11.4).

**Residual risk (accepted, Low):** the bulk create form is not yet reachable from the UI (only the single create is on the case detail page). The bulk endpoint exists and is tested but has no entry point — consistent with the plan's scope (P3 only requires the single attach form on case detail).


## Wave 6.16-P5.1 — viewer role enforcement (ADR-006, 2026-10-12)

Plan: `docs/planning/PLAN-2026-10-12-viewer-role-enforcement.md`. Verification:
`docs/verification/VERIFY-2026-10-12-viewer-role-enforcement.md`. Security review:
`docs/reviews/REVIEW-2026-10-12-viewer-role-enforcement.md` (0 Critical / 0 High).

| # | Plan / AGENTS.md says | Implemented | Status | Code pointer · Evidence |
|---|---|---|---|---|
| **V1** | `User.role` value `read-only` (ADR-004) | value + label renamed to **`viewer`**; data migration rewrites existing rows and reverses | ACCEPTED (owner-approved Q1) | `core/enums.py`, `identity/migrations/0008_role_viewer_rename.py` · `test_viewer_role_rename.py` |
| **V2** | "Read routes unchanged" (§7) | `not_viewer` denies **GET** as well, so `case-template-detail` (a pure read) and the GETs of `playbook_detail`/`tag_detail` return 404 to a viewer | ACCEPTED — those pages double as authoring forms; the lists stay readable and the entry links are hidden (P4) | `ui/views.py`, `ui/templates/ui/*_list.html` · `test_viewer_readonly_ui.py` |
| **V3** | Layer 1 `write=True` on **all** mutating call sites | wired at the **5 UI** sites only; the API's alert/observable mutations rely on Layer 2 | ACCEPTED — defence in depth, not the guarantee; Layer 2 covers every endpoint globally | `ui/views.py` · `test_viewer_readonly_api.py` |
| **V4** | `POST /api/v1/query` is read-through-POST | a viewer gets **403** (the global `ViewerReadOnly` sees an unsafe verb) | ACCEPTED — consistent with the existing read-scope-key decision; documented in plan §6 | `query/views.py`, `settings/base.py` · `test_viewer_readonly_api.py` |
| **V5** | UI 404 / API 403 (decision 3) | as specified; the API answer is normalised by `compat.errors` so a viewer never sees the scoping shape | ACCEPTED (deliberate, no oracle) | `compat/errors.py`, `identity/permissions.py` · `test_viewer_precedence.py` |
| **V6** | Role vs share precedence | the **role wins**: `write=True` resolution denies a viewer before the owner/share branches | ACCEPTED (owner-approved Q2) | `identity/scoping.py` · `test_viewer_precedence.py` |
| **V7** | Keyboard shortcuts (`keys.js`) | **no change needed** — the file is navigation-only; one minor pre-existing gap: `,` → `/settings/organisations` still navigates for a non-superAdmin (the nav link itself is hidden) | ACCEPTED (Low) | `ui/static/ui/keys.js` · `test_viewer_affordances.py` |
| **V7a** | — | `ui/views.py::not_viewer` logged `request.path` (percent-decoded; `<str:case_id>` accepts `%0A`) → CWE-117 log forgery | FIXED (security review L1) — logs resolver `url_name` + method | `ui/views.py::not_viewer` |
| **V6b** | The viewer denial sat *below* the privileged branch in `resolve_case` (and was skipped outright for superAdmins in `resolve_alert`/`resolve_observable`) | the deny is now the **first** statement of all three resolvers, so it holds even for an account that also looks privileged | ACCEPTED — decision 2 makes "viewer" a cap rather than another grant to arbitrate. Note the combination is *unreachable* by decision 4 and the old ordering was therefore harmless; the ordering is pinned by a monkeypatched test so it cannot silently regress (round-2 review **R2-L1**: the first version of this test set `is_superuser` and passed for an unrelated reason) | `identity/scoping.py` · `test_viewer_precedence.py::test_the_viewer_deny_precedes_the_privileged_branch` |
| **V7b** | (pre-existing bug, found by the new tests) | `ui-case-attachment-download` 500'd for **every** user: the route's `<uuid:attachment_id>` converter handed a `UUID` to the API helper, which did `UUID(value)` → `AttributeError` | FIXED | `cases/views.py::_as_uuid` · `test_viewer_reads.py` |

**Residual risk (accepted, Low) — reported by the review, not fixed:**

- **Django admin is outside the role model** (round-2 review **L2** — kept open, tracked as `TODO §11.5`). `role` is orthogonal to `is_staff`/`is_superuser`, and
  `/admin/` is not reached by any of the three enforcement layers. A viewer who is also `is_staff`
  keeps whatever the admin site grants them. Mitigation is deployment-side (do not grant `is_staff`
  to a viewer) — a structural fix (an admin `has_permission` hook) is tracked as a follow-up.
- **Layer 3 is UX, not a boundary.** `can_mutate` guards keep dead controls off the page; the server
  layers are the guarantee, and the route-enumeration sweeps are what catch drift.
- **Django's own 404 logger still writes `request.path` verbatim** (`django.request`), so an
  authenticated caller can forge log lines in the framework's log even though `not_viewer` no longer
  can (round-2 review **R2-I1**). Pre-existing and role-independent; a follow-up (`TODO §11.5`) rather
  than a blocker — the fix is a logging filter, and it belongs to the logging configuration, not to
  the role work.
- **`ui-case-attachment-detail` is a dead route.** The UI view is `@require_GET` but delegates to the
  API's `DELETE`-only `case_attachment_detail`, so a GET answers 405. It is not linked from any
  template and is exercised only by the tenant-scoping route tables. Pre-existing; tracked as a
  follow-up rather than fixed here (out of scope for the role work).


## Wave 10.2 — UI Case Create (2026-10-13)

Per `PLAN-2026-10-13-ui-case-create.md`. Adds `/cases/new` (GET form + POST create) and extracts the
case-construction logic into `cases/create.py::create_case`, shared by `POST /api/v1/case` and the UI.

| ID | Plan / AGENTS.md says | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W102-1** | API and UI had separate construction logic | **Shared `cases.create.create_case` service** | Mirrors the P2 update-surface collapse (`cases.update.apply_case_update`, deviation **W616P2-3**). One definition of field set, enum bounds, org-scoped assignee, tags, extraction, ledger entry. `cases/create.py`, `cases/views.py::_create_case`, `ui/views.py::case_create`. |
| **W102-2** | Plan §7 form included "Extract observables" checkbox (Q1) | **Checkbox dropped; extraction is unconditional** | `create_case` always runs `extract_into_case` over title + description (same as `_create_case` before and `import_alert_to_case` for escalation). Payload `extract`/`observables` only *adds* explicit strings. UI template shows a hint line instead of a checkbox. `cases/create.py:142`, `ui/views.py:536`. |
| **W102-3** | Enum tolerance (ADR-002 §D11): out-of-range reached DB `CHECK` as 500; `severity: 0`/empty fell back to default 2 silently | **Stricter enum validation on create** | `create_case` range-checks `severity`/`tlp`/`pap` via `_set_case_fields` (SEC-2026-10-11 L1). Non-integer or out-of-range → 400 (`CaseUpdateError`), no row written. Empty/null/empty-string still means "model default" (tolerance preserved). `cases/create.py:74-77`, `cases/update.py:_set_case_fields`. |
| **W102-4** | No `case.created` automation trigger (plan §3 non-goal) | **No trigger added** | `automation/dispatcher.py::TRIGGER_EVENTS` remains `observable.created`, `alert.ingested`, `case.status_changed`, `task.completed`. Extraction fires `observable.created` which drives playbooks — the loop an analyst sees. |
| **W102-5** | `CaseUpdateError` retained (plan table §4) | **Error type not renamed** | Renaming to `CaseWriteError` would churn `cases/update.py`, `cases/create.py`, `ui/views.py`, `cases/views.py`. Recorded as deviation rather than churn; a rename is a §2.5-style cleanup. |
| **W102-6** | Plan §3 adds an ungated rail entry + `n` shortcut | **Rail link and cheat-sheet row are `can_mutate`-gated; the raw `n` shortcut is not** | ADR-006 §4.4 Layer 3 ("a viewer is never offered a control"): the rail `<li>` and its cheat-sheet `<tr>` render only when `can_mutate` (security review **L1**). The keyboard shortcut itself stays in `ui/static/ui/keys.js` — a static, CSP-allowed file cannot read the role, so an `n` press by a viewer still navigates to a 404; same accepted limitation as the `,` → Settings shortcut (see **V7**). `ui/templates/ui/base.html`. |
| **W102-7** | Audit parity between the UI and API create paths | **Only the UI path writes `case.created`** | `POST /api/v1/case` (`cases/views.py::_create_case`) writes no `AuditLog` row (pre-existing); the new `ui/views.py::case_create` writes one inside the create transaction. Recorded as an informational asymmetry (security review **Info**), not a regression — closing it is a `cases/create.py` decision (audit every create, or neither) rather than part of §10.2. |

**Superseded by Wave 10.3 (below):** the rail `<li>` described in **W102-6** was later removed entirely and
`n` became a non-nav key — see **W103-1**.

**Defect fixed during the wave (not a deviation).** Security review **M1**: `POST /api/v1/case` with
`status: ""`/`null` returned **500** — a regression introduced by the shared-applier refactor, whose
predecessor spelled `str(payload.get("status") or "InProgress")`. The empty value reached
`resolve_case_status`, which `get_or_create`d a `CaseStatus` whose `stage=""` violates the
`case_status_stage_valid` `CHECK`. `cases/create.py::_create_scalars` no longer forwards a falsy `status`
(absent/`""`/`null` now keep the default `InProgress`, matching the pre-refactor behaviour); pinned by 3
regression tests in `tests/conformance/test_ui_case_create.py` and documented in `docs/spec/api.md`.


## Wave 10.3 — Cases view: trailing-slash tolerance + New-case modal (2026-10-13)

Per `PLAN-2026-10-13-cases-view-fixes.md`. Owner report: `GET /cases/` 404'd as an `admin`. Root cause was
URL registration, not the view: `ui/urls.py` registers every UI route slash-less and Django's `APPEND_SLASH`
only ever *appends* a slash, never strips one.

| ID | Plan / AGENTS.md says | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W103-1** | §10.2 put "New case" on the left rail with an `n` shortcut (and AGENTS.md §6 wants discoverable keyboard shortcuts) | **Rail item removed; `n` demoted to a non-nav key** | Owner request: creating a case belongs on the cases view, not the main menu. `ui/templates/ui/base.html`'s rail `<li>` is deleted; `ui/templates/ui/case_list.html` renders a `can_mutate`-gated button (`href="/cases/new"` + `data-modal-open="new-case-modal"`) and the server-rendered modal. `n` is removed from `ROUTES` in `ui/static/ui/keys.js` but kept in `CHEATSHEET` and the cheat-sheet table, and is now listed in `_NON_NAV_KEYS` (`tests/conformance/test_ui_nav.py`): the key opens the modal on the cases list, else navigates to `/cases?new=1`. Supersedes W102-6's rail link. |
| **W103-2** | The API's idiom for trailing slashes is an explicit `-slash` route pair (and `test_t2_p1_surface.py` argues `APPEND_SLASH` must not be relied on because a 301 drops a POST body) | **One UI-wide middleware instead of ~20 per-route aliases** | Completeness is the point: a per-route list silently stops covering the next page that is added. `core/middleware.py::StripTrailingSlashMiddleware` redirects **301** for GET/HEAD and **308** for unsafe methods (preserving method + body), only when the slash-less path `resolve()`s, and never for `/api/`, `/admin/`, `/static/`, `/media/`, `/ws/`. Registered after `CommonMiddleware`. Pinned by `tests/conformance/test_ui_trailing_slash.py` (15 tests), including "`/api/v1/case/` is not rewritten" and "unknown path stays 404". |
| **W103-3** | Plan §4 says "strip a *single* trailing slash" | **`rstrip("/")` collapses repeated slashes; accepted** | `/cases////` → `/cases` (one hop) rather than `/cases/` (iterated hops). No security impact (security review **LOW-02**): the `resolve()` guard still governs whether a redirect happens at all. Recorded rather than changed; a stricter `path[:-1]` would merely require more round-trips for a malformed URL. |

**Recorded as not-exploitable (security review LOW-01).** The preserved `QUERY_STRING` reaches the
`Location` header verbatim, but `django.utils.encoding.iri_to_uri` percent-encodes CR/LF
(`'/cases?a=1\r\nX'` → `'/cases?a=1%0D%0AX'`) and Django's `BadHeaderError` is the fail-closed backstop, so
header injection is not reachable. No code change; evidence in
`docs/reviews/REVIEW-2026-10-13-cases-view-fixes.md`.


## Wave 10.4 — Case pages: create / edit / delete (2026-10-14)

Plan: `docs/planning/PLAN-2026-10-14-case-pages-ui.md`. Adds analyst-facing CRUD for `Page` on the case
detail **General** tab (routes `ui-case-page-create/-edit/-delete`), so a page no longer needs the API.

| ID | Plan / AGENTS.md says | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **W104-1** | §3.3 lists a `Page` entity but no UI surface | **Pages get their own card on General, sibling to the case description** | The old markup nested the page list inside the case-description `<section>`; headings, counts and empty states did not match the other cards. `ui/templates/ui/_tab_general.html`, `_page_entry.html`. |
| **W104-2** | §6 prefers "progressive enhancement, `<dialog>` modals over native dialogs" | **create/edit/delete are `<dialog>` modals** built from the shared `ui/_modal.html` (no inline JS, CSP-safe) | App idiom (fix #9/#10); the body templates `_page_form.html` and `_page_delete_form.html` are shared by the create and edit paths. |
| **W104-3** | `Case.description` is Markdown | **Page content is rendered as Markdown** (`{{ page.content\|markdown }}`) | Same filter and sanitizer as the description (`ui.markdown.render_markdown`, ADR-005); the raw Markdown stays visible only inside the edit form's textarea. |
| **W104-4** | §3 `Page` carries `category` and `order` | **`category` is editable in the UI; `order` is not** | `order` is API-managed (create appends `max(order)+1`); a reorder UI is a non-goal (N1) of the plan. `ui/views.py::case_page_create/_update`. |
| **W104-5** | The API publishes a `page` frame on create only | **The UI publishes on create *and* edit; delete publishes nothing** | Deliberate API parity for delete (open question Q6 in the plan); a delete therefore reaches other analysts on their next load. `ui/views.py` (`transaction.on_commit(partial(publish_case_event, …))`). |
| **W104-6** | `live.js` had a `pageEntry()`/`appendPage()` mirror of `_page_entry.html` | **Mirror deleted; a `page` frame now re-fetches `#case-pages`** | The mirror was byte-coupled to the template (any markup change silently desynced the two); `refreshPages()` re-renders through the same template, and needs no `innerHTML` sink. `ui/static/ui/live.js`. |
| **W104-7** | ADR-006 §4.4: a viewer is never offered a control | **Every page control is `can_mutate`-gated and all three routes are `@not_viewer`** | Read-only users still see the pages and their rendered content. `ui/urls.py`, `ui/views.py`. |
| **W104-8** | The case Audit tab records `case.created`/`case.updated` | **A page create/edit/delete is not `AuditLog`ged** | Pre-existing gap (same as tags/tasks): the UI writes are invisible in Audit. Recorded for `TODO.md`, not fixed here (plan §8 S6 / R3). |

**Defects found and fixed during the wave (not deviations).** Four were in the shared modal/stylesheet
layer, found by driving the real page in a headless browser rather than by reading CSS:

- **Modal width (was: every `_modal.html` modal).** A `<dialog>` is shrink-to-fit and the `size` classes
  only set `max-width`, so `size="lg"` modals rendered **270px wide with a 177px Markdown textarea** —
  the new case form, the new-user form and the org-delete modal were all affected. `.modal--sm/md/lg/xl`
  in `ui/static/ui/modal.css` now set a real `width` plus `max-width: 100%` (measured 560px, still
  inside a 482px viewport).
- **Tall modal overflows the viewport:** the page form (3 fields + an 8-row textarea) grew past the
  viewport, and with `inset: 0` + `margin: auto` an over-tall `<dialog>` loses its auto margins and pins
  to the top — the footer's confirm button sat off-screen with nothing to scroll. `.modal-content` is now
  capped at `calc(100dvh - 2rem)` (with a `vh` fallback) and `.modal-body` is the only scroll region.
- **Undefined `--color-focus`** in `app.css` (used twice, defined nowhere): the invalid `var()` dropped
  the declaration, so the tab/panel focus ring was gone (WCAG 2.4.7 / AGENTS.md §6) — and
  `test_ui_css_tokens.py::test_every_color_token_used_is_defined` was red. Both rules now use
  `--color-accent`.
- **`.tabpanel:focus { outline: none }`** — `test_ui_css_tokens.py::test_no_rule_removes_the_focus_outline`
  was red, and the rule outranked the global `:focus-visible` ring for Tab-reachable panels. Removed;
  `:focus-visible` keeps the indicator. *(Note for future edits: that guard scans the whole file,
  comments included — do not write the forbidden declaration even in prose.)*
- **Page-level horizontal scrollbar on the case page** at common laptop widths: `.main` is a grid item
  with the automatic minimum size, and the 8-tab strip's min-content (`.tab` is `nowrap`) exceeded the
  column, so the strip's own `overflow-x: auto` never applied. `.main { min-width: 0 }` lets the strip
  scroll itself.

Evidence: `PLAN-2026-10-14-case-pages-ui.md` §11 — 63 checks through the Django test client, 31 in
headless Edge, and 7 dialogs across 4 pages measured at two viewports; `make css-check` clean.


## Wave 2026-10-15/16 — token-only ingest routing + the show-once token dialog

Plans: `docs/planning/PLAN-2026-10-15-token-only-webhook-routing.md` (routes, ADR-008) and
`docs/planning/PLAN-2026-10-16-token-created-modal.md` (the show-once dialog).

| ID | Plan / AGENTS.md says | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **WTR-1** | `PLAN-2026-10-15-settings-sources-ingestion-tokens` non-goal **N1**: "No source CRUD…" | **`IngestionSource` is now provisionable by name from the token form** | That plan shipped a create form whose source list had no writer in the product (`IngestionSource.objects.order_by("name")` had no write path). Sources stay global and gain no editor. `ui/views.py::settings_token_create`. |
| **WTR-2** | `sources_list.html` describes sources as `open`/`required` and says an open source "accepts unauthenticated posts" | **Stale copy left in place** (only the URL sentence was fixed) | The claim has been false since strict token auth (2026-10-10) and is pinned by `test_ui_loop.py:520-537`; copy-and-pin is its own change. `TODO.md` §10.8. |
| **WTC-1** | §6 prefers progressive enhancement; a fragment view was fine | **`_modal.html` gains `show_cancel`, `close_url`, `modal_static`** | All three are opt-in, so every existing dialog renders unchanged (`test_settings_ingestion_tokens.py`). |
| **WTC-2** | `data-modal-static` documented as "backdrop-click only" | **Esc now respects it too** | A static dialog whose purpose is to hold a one-time secret must not be dismissible with a single key; no dialog carried the attribute before, so no behaviour changed. `ui/static/ui/modal.js`. |
| **WTC-3** | `[data-modal-close]` was button-only | **`preventDefault()` for the anchor form of the closer** | The only way to provide a JS-less exit from a static dialog is a real `<a>`; with JS it must close in place, not navigate. Buttons have no default action, so their path is untouched. `ui/static/ui/modal.js`. |
| **WTC-4** | The show-once dialog was rendered as a bare partial | **The create POST's 200 renders the whole Sources page with the dialog opened** | The partial had no `{% extends %}`, so the response was a document with no shell and no stylesheets — the one secret shown once landed unstyled. Polluting the response with the list is accepted; `token_plaintext` is set on that path only, never in a URL/session/log. `ui/views.py`. |
| **WTC-5** | `app.css` is where component styles live | **The `.token-*` rules went into `modal.css`** | They style only the dialog body. Trade-off recorded: `test_ui_css_tokens.py` scans `app.css` only, so a typo'd token here is not caught (and `.blockers-warning` at `modal.css:123` is already broken by undefined tokens — see `TODO.md`). |

**Defects found and fixed during the wave (not deviations).**

- **A multi-line `{# … #}` is not a comment.** The first version of the added block in
  `settings_sources.html` spanned four lines, so Django emitted it verbatim: every
  `GET /settings/sources` carried literal `{# … #}` markup (and a raw `data-modal-static` substring on a
  page where no dialog has the attribute). Found by `qa-tester`, fixed to
  `{% comment %}…{% endcomment %}`; no other template has this shape.
- **The scanner that should have caught it assumed the wrong syntax.** `test_ui_inline_assets.py` used
  `{#.*?#}` `re.DOTALL`, i.e. it *stripped* multi-line `{# … #}` as if it were a comment, hiding exactly
  this leak. Now single-line-only, with a new `test_no_multiline_inline_comment` parametrized over all 69
  templates and two rendered-body assertions in `test_settings_ingestion_tokens.py`.
- **`{# #}` inside `{% comment %}` is not stripped in prose** — noted in the guard's docstring so the
  next editor does not "fix" the new test by widening the regex again.

Evidence: `PLAN-2026-10-16-token-created-modal.md` §6/§10 — AC1–AC8, 8 rendered assertions, 69 template
cases; full suite 1918 passed / 3 skipped (109 s), `ruff` clean, `make css-check` clean.


## Wave 2026-10-16 — the per-source field mapping engine (P1)

Plan: `docs/planning/PLAN-2026-10-16-source-field-mapping.md` §8 P1. Scope of this wave: the pure
engine (`ingest/mapping.py`), the receiver and pipeline wiring, and its tests. The editor, the org
gate (ADR-009) and the preview dialog are P2—P4 and are not in this diff.

| ID | Plan / AGENTS.md says | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **MAP-1** | AC4: a legacy flat `mapping_config` ingests "exactly as it did" | **A legacy severity is no longer `int()`-truncated** | `_coerce_severity` did `int(value)`, so `90` → rejected but `3.7` → **3** and `True` → 1. The replacement (`_severity_from_int`) accepts only integers, integral floats and numeric strings. A vendor sending `3.7` has not told us "High", and rounding a triage field down silently is the failure this wave exists to remove. `ingest/mapping.py`. |
| **MAP-2** | AC4.3: an unconfigured source ingests "without error" | **A rule that matched nothing now also emits a warning** (the old reader was silent when a configured JSONPath missed) | AC7 asks that every tolerance be recorded; the old silence was only "safe" because nothing was configurable. Silent when *no* rule exists, which is the honest case: an unconfigured feed never expected that field. `ingest/mapping.py::apply_mapping`. |
| **MAP-2a** | The pipeline's `severity_defaulted: …` text | **The engine now emits that same prefix, with the reason appended** | `severity` is the one target whose fallback is always reported, and `test_ingest_pipeline.py` pins the prefix. Text is now `severity_defaulted: $.severity, $.severityLevel; defaulted to 2` (was `…no severity mapping; defaulted to 2`). The pipeline branch is unchanged and still fires for callers that pass no severity. |
| **MAP-2b** | `ingestion_warnings` was `{"warnings": [...]}` | **Gains a `"mapping"` key**: `[{"field", "reason", "paths"?, "value_preview"?}]` | The string list is for humans and is pinned by tests; the structured list is what P4's editor and the alert view can render per field. Additive, so `ingestion_warnings["warnings"]` is untouched. `ingest/pipeline.py`, `ingest/mapping.py::FieldWarning`. |
| **MAP-3** | The legacy reader returned the first *non-`None`* match | **An empty/whitespace-only string is now "no answer"** | `""` is a payload that did not fill the field, so the next candidate path (or the literal key) gets a turn — which is the entire purpose of a candidate list. `mapping.py`. |
| **MAP-3a** | The legacy reader took `int` for severity from the first candidate that answered at all | **A candidate that cannot become a severity no longer stops the search** | `{"severity": "$.severity"}` against a 0–100 score of `90` used to end the search at the first key and default; it now tries `severityLevel`/literal keys too, then defaults with a warning that names the paths. `mapping.py`. |
| **MAP-4** | (no plan statement) | **Compiled JSONPath expressions are cached process-wide** | Measured: `jsonpath_ng.parse` costs ~1.8 ms, and a configured source walks up to 7 targets x 8 paths per webhook — i.e. tens of ms of Lark per alert, re-parsing strings that have not changed since the source was saved. Cache keyed by the source string, bounded at 512 entries, deliberately caching a `None` (bad path) result. 1.8 ms → 1.0 µs. `ingest/mapping.py::_compile`. |

**Confirmed by this wave, not new deviations.** `IngestionSource.default_severity` was written by no
code and read by no reader (`ingest/pipeline.py` used the module constant `DEFAULT_SEVERITY`); the
engine now threads it through `default_severity=`, with the constant as the floor for direct pipeline
callers (`test_ingest_pipeline.py` passes no severity, so that branch stays live). `_verify_secret`
in `ingest/views.py` remains dead code (pre-existing, unchanged by this wave; `TODO.md`).

Evidence: `test_mapping_engine.py` — 184 cases (AC1 purity via a cursor tripwire, AC2 = the synonym
table + score bands, AC3 = the stored CrowdStrike payload end to end through `/api/v1/alerts/webhook/`,
AC4/AC5 = legacy shapes and 132 no-raise combinations of malformed config × non-object payload, AC6 =
`default_severity`, AC7 = the structured warnings); the five pre-existing flat-`mapping_config` fixture
files pass unchanged (86 tests); full suite 2181 passed / 3 skipped (113 s), `ruff` clean.


## Wave 2026-10-16 — the org-owned source and the mapping editor (P2 + P3)

Plan: `docs/planning/PLAN-2026-10-16-source-field-mapping.md` §8 P2/P3. P2 (`IngestionSource` becomes
org-owned, ADR-009) has no *behavioural* deviation beyond the ADR itself; P3 is the mapping editor
(`ingest/editor.py` + `ui/views.py::_save_source_mapping` + the rewritten
`ui/templates/ui/source_detail.html`) and the four rows below.

| ID | Plan says | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **MAP-5** | §7.3: "per-field errors via a Django `Form`" | **Hand-parsed POST with per-row errors** (no `Form`, no `ModelForm`) | The repo contains no Django `Form` anywhere; every POST view parses `request.POST` by hand and re-renders with the input kept (`settings_token_create` is the model). Introducing the first `Form` here would make this the one page that behaves differently on a validation failure — and the schema it would validate against (`validate_config`) is not a form. `ingest/editor.py` · `test_mapping_editor.py` (66 cases), `test_settings_source_mapping.py` (AC11: a bad path is a 200 that echoes `name</textarea>` and writes nothing). |
| **MAP-6** | §7.2: "one path per line" (unspecified separator) | **Newline only** — never a comma | A JSONPath may legitimately contain a comma (`$['a','b']` union, `$.x[1,2]`, a filter's `?(@.n == 'a,b')`), so a comma separator would silently split valid paths into garbage. `ingest/editor.py::parse_paths` · `test_mapping_editor.py::TestPathsAreNewlineSeparatedOnly`. |
| **MAP-7** | §6: the audit row carries the changed keys | **`identity.models.AuditLog.details` is new** (`JSONField(default=dict, blank=True)`) | There was no structured detail column, and the alternative — stuffing `['fields.title.paths', …]` into `message` — would make the changed keys unqueryable and unrendered forever. `details={"changed": [...]}` holds dot-paths into the config, **never values**: an audit row outlives the config and must not become a copy of it. `identity/audit.py::record_ingestion_source_updated` · `test_mapping_editor.py::TestTheEditorChangedKeys`, `test_settings_source_mapping.py` (`"$.name" not in str(log.details)`). |
| **MAP-8** | §5.1 rule schema lists a `required` per field | **No `required` key, and no "required" column in the table** | The v2 schema has no `required`: absence is already covered by a rule's own `default` and by `on_missing`, and a "required" flag would only be a promise the ingest path cannot keep (a webhook may legitimately lack the field — `apply_mapping` never raises, by design). Adding a column with no reader is how a schema grows a lie. `ingest/mapping.py` (`_normalize` reads `paths`/`transform`/`default`/`max_length`/`bands`/`severity_default`) · `test_mapping_engine.py` (config validation), `test_schema_mutation_guards.py`. |

**Behaviour found and pinned while implementing P3 (not deviations, but worth naming).**

* **An empty row deletes the rule**, which is the only delete control the table has — and a preserved
  `bands`/`max_length` may **not** resurrect it. A v2 rule with no `paths` and no `default` answers
  *nothing* while still shadowing the literal-key fallback, so "clearing" a row would have silently
  stopped that field from being read at all. `ingest/editor.py::build_table_config` ·
  `test_mapping_editor.py::test_a_preserved_bands_list_cannot_resurrect_a_cleared_rule`.
* **AC12 is measured against the table's own rendering**, not against the raw column: the table omits
  `transform: "str"` (the engine's default), so a config that spells it out is canonicalised rather
  than reported as changed on every save — while a **legacy** config is deliberately *not* normalised,
  because converting the shape is a real change (the literal keys stop being fallbacks).
  `ingest/editor.py::is_noop_save` · `test_settings_source_mapping.py::TestTheNoOpSave`.
* **`validate_config`'s shape branch now matches `_normalize`'s** (`version != 2 and fields is not a
  dict`): `{"version": 2}` was validated as *legacy* while being read as v2-with-no-rules, i.e. a
  config that validated and then did something else. `ingest/mapping.py` ·
  `test_mapping_editor.py::test_a_v2_config_without_fields_is_valid`.
* **A legacy config's `value_maps` is not validated** (the legacy branch returns before it) and is
  preserved verbatim by a table save, so it becomes *live* — and validated — on the first save that
  converts the config. Pinned so the surprise is documented rather than discovered.

Evidence: `test_mapping_editor.py` (66 cases, pure — including an import-level guard that the editor
never imports Django, and a cursor tripwire); `test_settings_source_mapping.py` 16 → 34 cases (AC11–AC14
added: write+audit, echo-on-error, no-op, JSON box, and the analyst/foreign-admin 404 with the
cross-org superAdmin write audited on both organisations).


## Wave 2026-10-16 — P7: re-applying a mapping to existing alerts

Plan: `docs/planning/PLAN-2026-10-16-source-field-mapping.md` §8 P7 (AC17–AC23). This wave is the §11
Q8/Q9 follow-on: a second button next to "Save mapping" that repairs the alerts a wrong mapping already
produced. Three rows, all narrowing what a bulk write may touch.

| ID | Plan says | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **MAP-9** | §8 P7: "re-apply the mapping" (the field set unspecified) | **`title`, `description`, `summary`, `severity`, `date` only** — `type`/`source_ref`/`correlation_key` are never written | `type` and `source_ref` are the dedupe key with `source`/`owner_org` (`uniq_alert_source_type_ref`) *and* the replay key, so rewriting them can collide with a stored row and moves the identity of an alert that already exists; `correlation_key` is not a v2 field (§11 Q8). Everything else on the row is a human/triage decision (`status`, `case`, `tags`, `assignee`, `flag`, `follow`, `tlp`, `pap`), i.e. the mapping's *input*, not its output. `ingest/remap.py::REMAP_FIELDS` · `test_mapping_remap.py::TestWhatItWrites::test_it_leaves_the_identity_fields_alone`. |
| **MAP-10** | §8 P7: "re-apply … to unlinked open alerts" (whether an unanswered field is filled) | **A text/binary field the new mapping cannot answer keeps its stored value** — only `severity` is always resolved | Ingest fills an unanswered title with the literal `"Alert"` because the alert has none yet; on a *stored* alert that is destructive, so clearing one row in the editor would silently retitle every unlinked open alert of the feed. Leaving the field alone makes the operation non-destructive, and the modal body states it in the operator's own reading of the dialog. `ingest/remap.py::_target_values` · `test_mapping_remap.py::test_a_text_field_the_new_mapping_cannot_answer_keeps_its_value`. |
| **MAP-11** | §5/§7: the editor's `MAX_BULK_PROCEDURES`-style cap idiom, applied to a re-map | **`MAX_REMAP_ALERTS = 5000` per request; over it the button refuses and (deliberately) saves nothing** | The pass runs in the request/response cycle (no task queue surface this wave). A "save & apply" that silently drops the apply half would be the worse failure, so the two halves are atomic: over the cap, neither the config nor the alerts are written. Past the cap the honest answer is a background task with a status surface, which is a later wave. `ingest/remap.py::MAX_REMAP_ALERTS`, `ui/views.py::_apply_mapping_to_alerts` · `test_settings_source_mapping.py::test_over_the_cap_the_dialog_disables_its_confirm`. |
| **MAP-12** | §8 P7: "re-applies it to its unlinked open alerts" (what a pass that changes no *field* still writes) | **A no-op pass replaces an out-of-date `mapping` record and appends nothing to the event log** — `refreshed` (a sub-count of `unchanged`) reports it, in the flash message and in the audit row | `ingestion_warnings["mapping"]` describes the alert's *current* fields (it names the rule paths and the values that failed), so a rules change that lands on the same values still leaves a record describing rules the feed has stopped storing — the page would keep saying "severity: nothing at `$.severity`" under a severity that was read fine. Replacing it is a real write, and reporting it as "unchanged" alone is what made a working re-map read as "nothing happened". The log is not touched, because nothing happened to the *alert*. `ingest/remap.py::_refresh_record`, `_record_is_current` · `test_mapping_remap.py::TestTheRecordRefresh`, `test_settings_source_mapping.py::test_the_flash_says_the_record_moved_when_only_the_record_moved`. |

**Behaviour found and pinned while implementing P7 (not deviations).**

* **Two audit rows, not one.** `ingestion_source.mapping_applied` is written even when the config did not
  change, because "apply again" is a real operation against the alerts; `ingestion_source.updated` stays
  conditional on a real config edit (AC12). `identity/audit.py` · `test_settings_source_mapping.py`.
* **`ingestion_warnings["mapping"]` is replaced, `["warnings"]` is appended.** The structured key
  describes the row's *current* fields, so a stale entry contradicts the row it is attached to ("defaulted
  to 2" under a severity of 4); the log key is history and keeps its ingest-time lines. Both are asserted
  with deliberately pre-seeded values. `ingest/remap.py::_refreshed_warnings` ·
  `test_mapping_remap.py::TestTheWarningsItLeaves`.
* **The candidate predicate is written once** (`candidate_alerts`) and the counts the dialog shows come
  from the same function the write path uses (`summarise_candidates`), so a dialog cannot promise one set
  of rows and write another. A candidate count that *grows* between rendering and confirming is caught
  again by the over-cap check on the write path. `ingest/remap.py` · `test_mapping_remap.py`.

Evidence: `test_mapping_remap.py` (14 cases — candidate exclusions asserted by leaving rows byte-identical,
the field set, the destructive-title guard, severity fallback, no-write/dedupe on an unchanged pass,
warnings, the cap) and `test_settings_source_mapping.py::TestApplyingTheMappingToExistingAlerts` (10
cases — the extra button on both cards, `intent=apply` writes nothing, the confirmed two-step
saves+remaps+audits, a tampered `pending_config`, the over-cap refusal saving nothing, the
unchanged-config apply auditing only `mapping_applied`, zero candidates still saving, and the role/org
gates on both intents).


## P7.1 — "I added a rule and nothing changed" (re-map feedback)

Found by an operator-visible failure rather than by a test: a `severity` rule reading `$.severity` (the
vendor's 0-100 score) with the engine's default `str` transform runs, produces `"70"`, is rejected as
`bad_type`, and falls back to the feed's `default_severity` — which is the value it already had. The pass
wrote nothing, the flash said "0 updated, 1 unchanged", and the alert page kept showing the ingest-time
`severity_defaulted` line. Every individual piece was working; the *operator* could not tell a working
rule from an unreadable one. This wave is the feedback, not the engine.

* **The dialog previews the real alerts** (`preview_alerts`) instead of counting them: per target, how
  many rows the rule would change and how many it produced nothing for, by reason. The dry pass shares
  `_evaluate` with the writer, so the promise cannot drift from the behaviour; it writes nothing (asserted
  on `updated_at`, the fields and `ingestion_warnings`). `ingest/remap.py::preview_alerts` ·
  `test_mapping_remap.py::TestThePreview`.
* **Unresolved rules are named in the flash**, by target and reason ("severity: the mapping produced no
  value on 19 of 19 alerts (bad_type)"), because that sentence is the fix: it says *which* rule and *what
  kind* of failure, neither of which "0 updated" can carry. `ingest.mapping.UNRESOLVED_REASONS` is the
  single definition of "produced no value" (`truncated` is a warning but not one of them — that row did get
  a value). `ui/views.py::_unresolved_lines` · `test_settings_source_mapping.py::test_the_flash_names_the_rule_that_produced_no_value`.
* **The alert and case pages render `ingestion_warnings["mapping"]`** — the live record, with the path and
  the offending value — above the append-only log. Both are needed and only the pair reads correctly.
  `ui/templates/ui/_mapping_resolution.html` · `test_ui_loop.py::test_the_alert_page_explains_why_a_mapped_field_fell_back`,
  `test_ui_markdown.py::MappingResolutionRenderingTests`.
* **The dialog states what a re-map will never apply** (MAP-9): a `source_ref`/`type` rule is stored, works
  for new alerts, and is never written to existing ones. The disclosure is read from the *candidate* config,
  so it fires on the one render where it matters. `ui/views.py::_source_detail_context` ·
  `test_settings_source_mapping.py::test_the_dialog_names_the_rules_a_remap_never_reapplies`.
* **The engine's `reason` tokens are user-visible now**, so their wording lives next to the engine
  (`ingest.mapping.REASON_LABELS`) and reaches templates through `|reason_label`/`|is_unresolved` rather
  than being re-typed in two pages. `ui/templatetags/ui_tags.py`.

Evidence: `test_mapping_remap.py` (25 cases, +11), `test_settings_source_mapping.py` (48 cases, +4),
`test_ui_loop.py`, `test_ui_markdown.py`.


## Evidence index

- `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §13 — full P7/P8/P10/P11 records
- `docs/reviews/REVIEW-2026-10-0{3,4,5}-phase3-schema-gate*.md` — M-series schema review
- `docs/planning/VERIFY-2026-10-07-phase10.md`, `VERIFY-2026-10-07-phase11.md` — independent checks
- `docs/decisions/ADR-002-thehive-api-compatibility.md` — the compatibility contract