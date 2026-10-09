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
| **—** | observables are per-case | `uniq_obs_dtype_hash (data_type, data_hash)` dedupes **globally**; cases link via M2M `case_observable` | ACCEPTED | `observables/models.py:111` · `test_observable_hashing.py` |
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
| **P10-w** | (code-only label) | sub-resources are **org-scoped through the owning case** (`_case_org_scope`) so a guessable id is not a future cross-org read | `cases/views.py:88,309` · REVIEW/plan §13; no-op until `owner_org` is populated (see F2) |

## Automation deviations

| ID | Plan said | Implemented | Why · Code pointer · Evidence |
|---|---|---|---|
| **P10-4** | alert-observable link dispatches automation | **No dispatch at link time** — alert observables are import candidates; fire on import/merge via the case path | `alerts/views.py::alert_observable_add`; `automation/registry.dispatch_observable_linked` requires a case · `test_mvp_loop_automation.py` |
| **P12-1** | `TRIGGER_EVENTS` are the events that drive playbooks | `task.completed` is **listed but nothing emits it** — no `Task` receiver, no call site | `automation/dispatcher.py:36,47`; `core/events.py:31`; `ui/views.py:367` does a bare task save. A playbook bound to it is inert. Fix (receiver **or** remove trigger) is an approved-surface change — see VERIFY-docs-spec |

## Security hardening findings

Source: security-auditor Wave C, carried as **P10-10** (F1/F6/F3/F5/F7 fixed in the burst). **F9 is
now fixed** (2026-10-08 hardening wave); **F2 remains deferred**.

| ID | Finding | Status / follow-up |
|---|---|---|
| **F2** | case/alert resolution is org-blind (`owner_org` never populated, so scoping is a no-op); **`Observable` PATCH/DELETE is global** (any authenticated user can mutate any observable) | **Deferred** — follow-up "tenant isolation" wave; until `owner_org` is set the filter changes nothing, and the observable mutation path is the real gap (code: `cases/views.py::observable_detail`) |
| **F9** | prod did not fail closed on `SECRET_KEY` and did not force secure cookies | **Fixed 2026-10-08** — `amalthea/settings/prod.py` now raises `ImproperlyConfigured` unless `DJANGO_SECRET_KEY` is ≥50 chars and `DJANGO_ALLOWED_HOSTS` is non-empty, and forces `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`/`SECURE_SSL_REDIRECT`/HSTS/`SECURE_CONTENT_TYPE_NOSNIFF`/`X_FRAME_OPTIONS=DENY`; proven by `tests/conformance/test_prod_settings.py` (9 tests). Plan: [`PLAN-2026-10-08-secret-hygiene.md`](../planning/PLAN-2026-10-08-secret-hygiene.md) |

## Evidence index

- `docs/planning/PLAN-2026-10-03-thehive-compatible-mvp.md` §13 — full P7/P8/P10/P11 records
- `docs/reviews/REVIEW-2026-10-0{3,4,5}-phase3-schema-gate*.md` — M-series schema review
- `docs/planning/VERIFY-2026-10-07-phase10.md`, `VERIFY-2026-10-07-phase11.md` — independent checks
- `docs/decisions/ADR-002-thehive-api-compatibility.md` — the compatibility contract